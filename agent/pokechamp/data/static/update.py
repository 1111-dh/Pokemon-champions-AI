"""update-static: 정적 데이터 갱신 (PROJECT_PLAN 4.2).

사용자가 명령을 실행할 때만 동작한다. 새 데이터를 바로 덮어쓰지 않고:
1. 공식 허용 목록 + Showdown + 한국어 이름으로 새 묶음을 만든다.
2. 활성 버전과의 차이 목록을 보여 준다.
3. 사용자가 승인하면 새 버전으로 쌓고 활성 버전을 바꾼다(이전 버전은 보관, 되돌리기 가능).
4. 적용 후 상성 캐시를 무효화하고 저장된 내 팀을 다시 검증한다.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pokechamp.data.db import active_version
from pokechamp.data.static.build import StaticBundle

MOVE_FIELDS = ("type", "category", "power", "accuracy", "pp", "priority")


def champions_max_pp(pp: int) -> int:
    """챔피언스 배틀 최대 PP (Showdown champions 모드 calculatePP)."""
    return int((pp / 5 + 1) * 4)
SPECIES_FIELDS = ("types_json", "base_stats_json", "abilities_json")


@dataclass
class StaticDiff:
    first_version: bool
    added: dict[str, list[str]] = field(default_factory=dict)
    removed: dict[str, list[str]] = field(default_factory=dict)
    changed: dict[str, list[str]] = field(default_factory=dict)
    meta: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed or self.meta or self.first_version)


def _rows(bundle: StaticBundle) -> dict[str, dict[str, dict[str, Any]]]:
    """묶음을 표별 {id: 행} 형태로. DB 행과 같은 열 이름을 쓴다."""
    species = {s["id"]: {
        "name_en": s["name_en"], "name_ko": s["name_ko"], "num": s["num"], "form_index": s["form_index"],
        "base_species": s["base_species"], "forme": s["forme"], "is_mega": int(s["is_mega"]),
        "official_code": s["official_code"], "types_json": json.dumps(s["types"]),
        "base_stats_json": json.dumps(s["base_stats"], sort_keys=True),
        "abilities_json": json.dumps(s["abilities"], sort_keys=True, ensure_ascii=False),
        "weightkg": s["weightkg"],
    } for s in bundle.species}
    moves = {m["id"]: {
        "name_en": m["name_en"], "name_ko": m["name_ko"], "type": m["type"], "category": m["category"],
        "power": m["base_power"], "accuracy": m["accuracy"], "pp": m["pp"], "max_pp": champions_max_pp(m["pp"]),
        "priority": m["priority"],
        "target": m["target"], "flags_json": json.dumps(sorted(m["flags"])), "description": m["desc"],
    } for m in bundle.moves}
    items = {i["id"]: {"name_en": i["name_en"], "name_ko": i["name_ko"], "is_mega_stone": int(bool(i["mega_stone"])),
                       "is_berry": int(i["is_berry"]), "description": i["desc"]} for i in bundle.items}
    abilities = {a["id"]: {"name_en": a["name_en"], "name_ko": a["name_ko"], "description": a["desc"]}
                 for a in bundle.abilities}
    natures = {n["id"]: {"name_en": n["name_en"], "name_ko": n["name_ko"]} for n in bundle.natures}
    mega = {m["mega_species"]: {"base_species_id": m["base_species"], "stone_item_id": m["stone_item"]}
            for m in bundle.mega_forms}
    return {"species": species, "moves": moves, "items": items, "abilities": abilities, "natures": natures,
            "mega_forms": mega}


def _db_rows(conn: sqlite3.Connection, vid: int) -> dict[str, dict[str, dict[str, Any]]]:
    out = {}
    for table, key in (("species", "id"), ("moves", "id"), ("items", "id"), ("abilities", "id"),
                       ("natures", "id"), ("mega_forms", "mega_species_id")):
        out[table] = {}
        for r in conn.execute(f"SELECT * FROM {table} WHERE version_id = ?", (vid,)):
            d = dict(r)
            d.pop("version_id")
            out[table][d.pop(key)] = d
    return out


def _learnsets_db(conn: sqlite3.Connection, vid: int) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in conn.execute("SELECT species_id, move_id FROM learnsets WHERE version_id = ?", (vid,)):
        out.setdefault(r[0], set()).add(r[1])
    return out


def compute_diff(conn: sqlite3.Connection, bundle: StaticBundle) -> StaticDiff:
    cur = active_version(conn)
    if cur is None:
        return StaticDiff(first_version=True)
    d = StaticDiff(first_version=False)
    if cur["regulation"] != bundle.regulation:
        d.meta.append(f"레귤레이션 {cur['regulation']} → {bundle.regulation}")
    if cur["showdown_commit"] != bundle.showdown_commit:
        d.meta.append(f"Showdown {cur['showdown_commit'][:7]} → {bundle.showdown_commit[:7]}")
    old, new = _db_rows(conn, cur["id"]), _rows(bundle)
    for table in new:
        o, n = old[table], new[table]
        name = lambda rows, k: rows[k].get("name_ko") or rows[k].get("name_en") or k  # noqa: E731
        if add := sorted(set(n) - set(o)):
            d.added[table] = [f"{name(n, k)} ({k})" for k in add]
        if rem := sorted(set(o) - set(n)):
            d.removed[table] = [f"{name(o, k)} ({k})" for k in rem]
        fields = MOVE_FIELDS if table == "moves" else SPECIES_FIELDS if table == "species" else ()
        ch = []
        for k in sorted(set(o) & set(n)):
            diffs = [f"{f}: {o[k][f]} → {n[k][f]}" for f in fields if o[k][f] != n[k][f]]
            if diffs:
                ch.append(f"{name(n, k)} ({k}) " + ", ".join(diffs))
        if ch:
            d.changed[table] = ch
    old_ls = _learnsets_db(conn, cur["id"])
    ls_changes = []
    for sid, moves in bundle.learnsets.items():
        before = old_ls.get(sid, set())
        plus, minus = sorted(set(moves) - before), sorted(before - set(moves))
        if before and (plus or minus):
            ls_changes.append(f"{sid}: +{','.join(plus) or '-'} / -{','.join(minus) or '-'}")
    if ls_changes:
        d.changed["learnsets"] = ls_changes
    return d


def format_diff(d: StaticDiff, bundle: StaticBundle, limit: int = 30) -> str:
    lines = [f"레귤레이션 {bundle.regulation} (공지 {bundle.official_notice_id}), Showdown {bundle.showdown_commit[:7]}",
             f"포켓몬 {len(bundle.species)} (메가 {len(bundle.mega_forms)}), 기술 {len(bundle.moves)}, "
             f"도구 {len(bundle.items)}, 특성 {len(bundle.abilities)}"]
    if d.first_version:
        lines.append("처음 적용하는 정적 데이터다 (비교할 이전 버전 없음).")
    elif d.empty:
        lines.append("활성 버전과 차이가 없다.")
    lines += [f"· {m}" for m in d.meta]
    for title, group in (("추가", d.added), ("제거", d.removed), ("변경", d.changed)):
        for table, xs in group.items():
            lines.append(f"[{title}] {table} {len(xs)}개")
            lines += [f"    {x}" for x in xs[:limit]]
            if len(xs) > limit:
                lines.append(f"    … 외 {len(xs) - limit}개")
    if bundle.warnings:
        lines.append(f"[경고] {len(bundle.warnings)}개")
        lines += [f"    {w}" for w in bundle.warnings[:limit]]
    return "\n".join(lines)


def apply_bundle(conn: sqlite3.Connection, bundle: StaticBundle, note: str = "") -> int:
    rows = _rows(bundle)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        vid = conn.execute(
            "INSERT INTO static_versions (applied_at, regulation, official_notice_id, regulation_starts_at,"
            " regulation_ends_at, showdown_commit, format, active, note) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (now, bundle.regulation, bundle.official_notice_id, bundle.regulation_starts_at,
             bundle.regulation_ends_at, bundle.showdown_commit, bundle.format, note),
        ).lastrowid
        for table, key in (("species", "id"), ("moves", "id"), ("items", "id"), ("abilities", "id"),
                           ("natures", "id"), ("mega_forms", "mega_species_id")):
            for k, r in rows[table].items():
                cols = ["version_id", key, *r.keys()]
                conn.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                             (vid, k, *r.values()))
        conn.executemany("INSERT INTO learnsets (version_id, species_id, move_id) VALUES (?, ?, ?)",
                         [(vid, sid, m) for sid, ms in bundle.learnsets.items() for m in ms])
        conn.execute("UPDATE static_versions SET active = (id = ?)", (vid,))
        invalidate_matchup_cache(conn)
    return vid


def invalidate_matchup_cache(conn: sqlite3.Connection) -> None:
    """상성 캐시 전체 무효화 (PROJECT_PLAN 6절). 캐시 표는 단계 2에서 생긴다."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='matchup_cache'").fetchone():
        conn.execute("DELETE FROM matchup_cache")


def revalidate_my_teams(conn: sqlite3.Connection, client: Any | None = None) -> list[str]:
    """저장된 내 팀을 새 규칙으로 다시 검증한다. 엔진 서버가 꺼져 있으면 건너뛰고 알린다."""
    teams = conn.execute("SELECT id, name, team_json FROM my_teams").fetchall()
    if not teams:
        return []
    if client is None:
        from pokechamp.engine_client import EngineClient
        client = EngineClient()
    msgs = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        results = client.batch("/validate_team", [{"team": json.loads(t["team_json"])} for t in teams])
    except Exception as e:  # noqa: BLE001
        return [f"엔진 서버에 연결하지 못해 저장된 팀 {len(teams)}개의 재검증을 건너뛰었다 ({e})"]
    with conn:
        for t, r in zip(teams, results):
            res = r.get("result") or {}
            valid = bool(res.get("valid"))
            conn.execute("UPDATE my_teams SET validated_at=?, valid=?, problems_json=? WHERE id=?",
                         (now, int(valid), json.dumps(res.get("problems", []), ensure_ascii=False), t["id"]))
            if not valid:
                msgs.append(f"팀 '{t['name']}'이 새 규칙에서 위반: {'; '.join(res.get('problems', [r.get('error', '')]))}")
    return msgs


def log_update(conn: sqlite3.Connection, status: str, started: float, note: str = "") -> None:
    with conn:
        conn.execute("INSERT INTO update_log (ran_at, kind, source, status, duration_s, note) VALUES (?, 'static', ?, ?, ?, ?)",
                     (datetime.now(timezone.utc).isoformat(timespec="seconds"), "official+showdown+pokeapi", status,
                      round(time.time() - started, 2), note))


def activate_version(conn: sqlite3.Connection, vid: int) -> None:
    """이전 버전으로 되돌린다."""
    if not conn.execute("SELECT 1 FROM static_versions WHERE id = ?", (vid,)).fetchone():
        raise ValueError(f"정적 데이터 버전 {vid}이 없다")
    with conn:
        conn.execute("UPDATE static_versions SET active = (id = ?)", (vid,))
        invalidate_matchup_cache(conn)
