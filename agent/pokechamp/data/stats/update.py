"""update-stats: 통계 스냅샷 수집 (PROJECT_PLAN 4.3).

사용자가 명령을 실행할 때만 동작한다. 규칙:
- 스냅샷은 덮어쓰지 않고 쌓는다. 출처의 데이터 날짜 하나당 스냅샷 하나 (같은 날짜는 건너뜀, --force면 교체).
- 시즌은 출처 표기가 아니라 데이터 날짜와 공식 시즌 달력으로 판정한다. 표기가 다르면 verified=0과 메모를 남기고,
  설정 season_label_policy="strict"면 저장하지 않는다.
- 활성 정적 데이터의 레귤레이션과 다른 레귤레이션의 데이터는 저장하지 않는다.
- 값마다 snapshot_id를 통해 출처, 시즌, 레귤레이션, 데이터 날짜가 추적된다.
- 출처 하나가 실패해도 다른 출처와 기존 스냅샷에는 영향이 없다.
"""

from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from pokechamp.config import stats_config
from pokechamp.data.calendar import KST, Calendar, normalize_season
from pokechamp.data.db import active_version
from pokechamp.data.static.names import to_id
from pokechamp.data.stats import championsbattledata as cbd

TABLES = {"moves": ("usage_moves", "move_id"), "items": ("usage_items", "item_id"),
          "abilities": ("usage_abilities", "ability_id"), "natures": ("usage_natures", "nature"),
          "teammates": ("usage_teammates", "mate_id")}
STATIC_TABLE = {"moves": "moves", "items": "items", "abilities": "abilities", "natures": "natures", "teammates": "species"}


@dataclass
class SourceReport:
    source: str
    status: str = "ok"                      # ok | partial | failed | unchanged
    seconds: float = 0.0
    stored: list[str] = field(default_factory=list)       # 저장한 데이터 날짜
    skipped: list[str] = field(default_factory=list)      # 이유와 함께
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    latest_date: str | None = None


def _today_kst() -> date:
    return datetime.now(KST).date()


def latest_page_date(conn: sqlite3.Connection, source: str, fmt: str) -> date | None:
    r = conn.execute("SELECT MAX(page_updated_at) FROM snapshots WHERE source=? AND format=?", (source, fmt)).fetchone()
    return date.fromisoformat(r[0]) if r and r[0] else None


def days_to_fetch(last: date | None, today: date, backfill: int) -> int:
    if last is None:
        return max(1, min(31, backfill))
    return max(1, min(31, (today - last).days + 1))


def store(conn: sqlite3.Connection, res: cbd.CollectResult, cal: Calendar, *, regulation: str,
          policy: str, force: bool, max_age_days: int, today: date | None = None) -> SourceReport:
    rep = SourceReport(source=res.source, seconds=res.seconds, errors=list(res.errors))
    v = active_version(conn)
    known = {t: {r[0] for r in conn.execute(f"SELECT id FROM {t} WHERE version_id=?", (v["id"],))}
             for t in set(STATIC_TABLE.values())}
    allowed_species = known["species"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    newest = max(res.days) if res.days else getattr(res, "newest", None)
    unmapped: dict[str, set[str]] = {}
    mismatched: list[date] = []
    for d in sorted(res.days):
        day = res.days[d]
        official = cal.season_on(d)
        reg = cal.regulation_of_season(official) if official else None
        label = normalize_season(day.page_season)
        if reg != regulation:
            rep.skipped.append(f"{d}: 레귤레이션 {reg or '알 수 없음'} 데이터 (현재 {regulation})")
            continue
        verified = label == official
        note = "" if verified else f"출처 시즌 표기 {day.page_season!r} ≠ 공식 시즌 {official} (날짜 {d} 기준)"
        if not verified and policy == "strict":
            rep.skipped.append(f"{d}: {note} → strict 정책으로 저장 안 함")
            continue
        exists = conn.execute("SELECT id FROM snapshots WHERE source=? AND format=? AND page_updated_at=?",
                              (res.source, res.format, d.isoformat())).fetchone()
        if exists and not force:
            rep.skipped.append(f"{d}: 이미 저장됨 (스냅샷 {exists[0]})")
            continue
        with conn:
            if exists:
                conn.execute("DELETE FROM snapshots WHERE id=?", (exists[0],))
            sid = conn.execute(
                "INSERT INTO snapshots (captured_at, source, format, regulation, official_season, page_season,"
                " page_updated_at, requested_season, verified, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (now, res.source, res.format, reg, official, day.page_season, d.isoformat(), "Current",
                 int(verified), note),
            ).lastrowid
            if d == newest:
                conn.executemany("INSERT INTO usage_species (snapshot_id, species_id, rank_position, usage_rate) VALUES (?,?,?,NULL)",
                                 [(sid, s, pos) for s, pos in res.rankings.items() if s in allowed_species])
            for species, cats in day.rows.items():
                if species not in allowed_species:
                    unmapped.setdefault("species", set()).add(species)
                    continue
                for cat, rows in cats.items():
                    if cat == "spreads":
                        conn.executemany("INSERT INTO usage_spreads (snapshot_id, species_id, rank, spread_json, rate) VALUES (?,?,?,?,?)",
                                         [(sid, species, rank, json.dumps(extra), rate) for _, rank, rate, extra in rows])
                        continue
                    table, col = TABLES[cat]
                    vals = []
                    for name, rank, rate, _ in rows:
                        key = to_id(name)
                        if key not in known[STATIC_TABLE[cat]]:
                            unmapped.setdefault(cat, set()).add(name)
                            continue
                        vals.append((sid, species, key, rank, rate))
                    conn.executemany(f"INSERT OR IGNORE INTO {table} (snapshot_id, species_id, {col}, rank, rate) VALUES (?,?,?,?,?)", vals)
        rep.stored.append(d.isoformat())
        if note:
            mismatched.append(d)
    if mismatched:
        labels = sorted({str(res.days[d].page_season) for d in mismatched})
        seasons = sorted({cal.season_on(d) or "?" for d in mismatched})
        rep.warnings.append(f"출처 시즌 표기 {', '.join(labels)} ≠ 공식 시즌 {', '.join(seasons)} "
                            f"({mismatched[0]}~{mismatched[-1]}, {len(mismatched)}일). 날짜 기준 공식 시즌으로 저장했다")
    for cat, names in unmapped.items():
        rep.warnings.append(f"정적 데이터에 없는 {cat} {len(names)}개 (저장 안 함): {', '.join(sorted(names)[:10])}")
    if newest:
        rep.latest_date = newest.isoformat()
        age = ((today or _today_kst()) - newest).days
        if age > max_age_days:
            rep.warnings.append(f"출처의 최신 데이터가 {age}일 전({newest})이다")
    if res.errors and not res.days:
        rep.status = "failed"
    elif res.partial or res.errors:
        rep.status = "partial"
    elif not rep.stored:
        rep.status = "unchanged"
    return rep


def run_update_stats(conn: sqlite3.Connection, *, force: bool = False, backfill: int | None = None,
                     progress: Callable[[str, int, int], None] | None = None) -> list[SourceReport]:
    cfg = stats_config()
    v = active_version(conn)
    if v is None:
        raise RuntimeError("정적 데이터가 없다. 먼저 `pokechamp update-static`을 실행하세요.")
    cal = Calendar.from_news()
    if not cal.seasons:
        raise RuntimeError("공식 시즌 달력이 비어 있다. 먼저 공식 공지 확인을 실행하세요 (python -m pokechamp.data.official_news).")
    species = [r[0] for r in conn.execute("SELECT id FROM species WHERE version_id=? AND is_mega=0 ORDER BY num, form_index", (v["id"],))]
    jobs: dict[str, Callable[[], Any]] = {}
    src_cfg = cfg["sources"].get(cbd.NAME, {"enabled": True, "format": "Singles"})
    if src_cfg.get("enabled", True):
        fmt = src_cfg.get("format", "Singles")
        last = latest_page_date(conn, cbd.NAME, fmt.lower())
        days = days_to_fetch(None if force else last, _today_kst(), backfill or cfg["backfill_days"])
        jobs[cbd.NAME] = lambda: cbd.collect(
            species, days, fmt, force=force, interval_s=cfg["request_interval_s"], cache_hours=cfg["cache_hours"],
            timeout_s=cfg["source_timeout_s"], progress=(lambda i, n: progress(cbd.NAME, i, n)) if progress else None,
            have_through=None if force else last)
    reports = []
    # 출처별 수집은 병렬로 하고, 저장은 한 스레드에서 한다 (SQLite)
    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
        futures = {name: pool.submit(fn) for name, fn in jobs.items()}
        for name, fut in futures.items():
            t0 = time.time()
            try:
                res = fut.result()
                rep = store(conn, res, cal, regulation=v["regulation"], policy=cfg["season_label_policy"],
                            force=force, max_age_days=cfg["max_age_days"])
            except Exception as e:  # noqa: BLE001
                rep = SourceReport(source=name, status="failed", errors=[str(e)], seconds=round(time.time() - t0, 1))
            reports.append(rep)
            with conn:
                conn.execute("INSERT INTO update_log (ran_at, kind, source, status, duration_s, note) VALUES (?, 'stats', ?, ?, ?, ?)",
                             (datetime.now(timezone.utc).isoformat(timespec="seconds"), name, rep.status, rep.seconds,
                              f"저장 {len(rep.stored)}일, 오류 {len(rep.errors)}건"))
    return reports


def stats_status(conn: sqlite3.Connection, today: date | None = None) -> list[dict[str, Any]]:
    """출처별 최신 스냅샷의 데이터 날짜, 경과 일수, 공식 시즌, 표기 불일치 여부."""
    out = []
    today = today or _today_kst()
    for r in conn.execute(
        "SELECT source, format, COUNT(*) n, MAX(page_updated_at) latest FROM snapshots GROUP BY source, format"
    ):
        s = conn.execute("SELECT * FROM snapshots WHERE source=? AND format=? AND page_updated_at=?",
                         (r["source"], r["format"], r["latest"])).fetchone()
        out.append({
            "source": r["source"], "format": r["format"], "snapshots": r["n"], "latest_date": r["latest"],
            "age_days": (today - date.fromisoformat(r["latest"])).days, "official_season": s["official_season"],
            "regulation": s["regulation"], "page_season": s["page_season"], "verified": bool(s["verified"]),
            "captured_at": s["captured_at"], "note": s["note"],
        })
    return out
