"""사전확률 모듈 (PROJECT_PLAN 4.5).

통계 스냅샷들을 데이터 날짜 기준 경과 시간으로 지수 감쇠시켜 합치고, "포켓몬 X가 기술/도구/특성/성격/배분 Y를
가질 확률"(주변 분포)을 돌려준다. 값마다 신선도(출처, 데이터 날짜, 경과 일수, 레귤레이션)를 함께 돌려준다.

- 스냅샷 간격은 불규칙하다고 가정한다. 가중치는 스냅샷 개수가 아니라 page_updated_at의 경과 일수로 정한다.
- 기본으로 현재(활성 정적 데이터) 레귤레이션의 스냅샷만 쓴다. 그 포켓몬의 현재 레귤레이션 자료가 없을 때만
  이전 레귤레이션 자료를 낮은 가중치로 쓰고 previous_regulation=True로 표시한다.
- 출처는 범주별 상위 10개만 주므로, 범주형 분포는 covered(상위 10개 합)와 other(나머지)를 함께 돌려준다.
  기술은 "그 기술을 넣었을 확률"(포함 확률)이라 합이 1이 아니다.
- 결합 분포(세트 단위)는 여기서 만들지 않는다. 세트 원형 모듈(4.7)이 이 주변 분포를 보정에 쓴다.
- 로컬 메타(my_battles) 반영은 단계 6에서 이 모듈에 붙인다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from pokechamp.config import priors_config
from pokechamp.data.calendar import KST
from pokechamp.data.db import active_version

KINDS = {
    "moves": ("usage_moves", "move_id"),
    "items": ("usage_items", "item_id"),
    "abilities": ("usage_abilities", "ability_id"),
    "natures": ("usage_natures", "nature"),
    "spreads": ("usage_spreads", "spread_json"),
    "teammates": ("usage_teammates", "mate_id"),
}
CATEGORICAL = {"items", "abilities", "natures", "spreads"}


@dataclass(frozen=True)
class Freshness:
    source: str | None
    latest_date: str | None
    age_days: int | None
    regulation: str | None
    previous_regulation: bool
    n_snapshots: int

    def label(self) -> str:
        if self.latest_date is None:
            return "통계 없음"
        s = f"{self.age_days}일 전 자료" if self.age_days else "오늘 자료"
        return s + (" · 이전 레귤레이션 자료 사용" if self.previous_regulation else "")


@dataclass
class Dist:
    species: str
    kind: str
    values: list[tuple[str, float]]          # (키, 확률) 확률 내림차순
    covered: float                            # 상위 값들의 확률 합 (기술은 의미 없음)
    other: float                              # 1 - covered (범주형만)
    freshness: Freshness
    fallback: bool = False                    # 통계가 없어 비어 있음 → 세트 원형의 규칙 기반 대체가 필요

    def prob(self, key: str) -> float:
        return next((p for k, p in self.values if k == key), 0.0)


@dataclass
class Trend:
    rank_now: int | None
    rank_before: int | None
    gap_days: int | None
    known: bool

    @property
    def delta(self) -> int | None:
        """양수면 순위가 올랐다(숫자가 작아짐)."""
        if not self.known or self.rank_now is None or self.rank_before is None:
            return None
        return self.rank_before - self.rank_now


@dataclass
class _Snap:
    id: int
    source: str
    page_date: date
    regulation: str
    age_days: int
    weight: float


class Priors:
    def __init__(self, conn: sqlite3.Connection, today: date | None = None, cfg: dict[str, Any] | None = None,
                 fmt: str = "singles", source: str | None = None):
        self.conn = conn
        self.cfg = {**priors_config(), **(cfg or {})}
        self.today = today or datetime.now(KST).date()
        v = active_version(conn)
        self.regulation = v["regulation"] if v else None
        q = "SELECT id, source, page_updated_at, regulation FROM snapshots WHERE format=?"
        args: list[Any] = [fmt]
        if source:
            q += " AND source=?"
            args.append(source)
        hl = float(self.cfg["half_life_days"])
        self.snaps: list[_Snap] = []
        for r in conn.execute(q + " ORDER BY page_updated_at", args):
            d = date.fromisoformat(r["page_updated_at"])
            age = max(0, (self.today - d).days)
            self.snaps.append(_Snap(r["id"], r["source"], d, r["regulation"], age, 0.5 ** (age / hl)))
        self.current = [s for s in self.snaps if s.regulation == self.regulation]
        self.previous = [s for s in self.snaps if s.regulation != self.regulation]
        self._cache: dict[tuple[str, str], Dist] = {}

    # ---------- 신선도 ----------
    def freshness(self, snaps: list[_Snap] | None = None, previous: bool = False) -> Freshness:
        snaps = self.current if snaps is None else snaps
        if not snaps:
            return Freshness(None, None, None, None, previous, 0)
        latest = max(snaps, key=lambda s: s.page_date)
        return Freshness(latest.source, latest.page_date.isoformat(), latest.age_days, latest.regulation,
                         previous, len(snaps))

    def warning(self) -> str | None:
        """모든 추천 출력에 붙일 경고. 통계가 없거나 오래됐으면 문장을 돌려준다."""
        f = self.freshness()
        if f.latest_date is None:
            return "현재 레귤레이션 통계가 없다. `pokechamp update-stats`를 실행하세요."
        if f.age_days is not None and f.age_days >= int(self.cfg["stale_after_days"]):
            return f"통계가 {f.age_days}일 전 기준이다."
        return None

    # ---------- 주변 분포 ----------
    def _snaps_with(self, species: str, table: str, snaps: list[_Snap]) -> list[_Snap]:
        if not snaps:
            return []
        ids = ",".join(str(s.id) for s in snaps)
        have = {r[0] for r in self.conn.execute(
            f"SELECT DISTINCT snapshot_id FROM {table} WHERE species_id=? AND snapshot_id IN ({ids})", (species,))}
        return [s for s in snaps if s.id in have]

    def marginal(self, species: str, kind: str) -> Dist:
        key = (species, kind)
        if key in self._cache:
            return self._cache[key]
        table, col = KINDS[kind]
        snaps, previous = self._snaps_with(species, table, self.current), False
        factor = 1.0
        if not snaps:
            snaps = self._snaps_with(species, table, self.previous)
            previous = bool(snaps)
            factor = float(self.cfg["previous_regulation_weight"])
        if not snaps:
            d = Dist(species, kind, [], 0.0, 1.0 if kind in CATEGORICAL else 0.0, self.freshness([], False), fallback=True)
            self._cache[key] = d
            return d
        weight = {s.id: s.weight * factor for s in snaps}
        total_w = sum(weight.values())
        acc: dict[str, float] = {}
        ids = ",".join(str(s.id) for s in snaps)
        for r in self.conn.execute(
            f"SELECT snapshot_id, {col}, rate FROM {table} WHERE species_id=? AND snapshot_id IN ({ids})", (species,)
        ):
            if r["rate"] is None:      # 동료는 비율 없이 순위만 있다 → 순위 역수로 대체
                continue
            acc[r[col]] = acc.get(r[col], 0.0) + weight[r["snapshot_id"]] * r["rate"]
        if kind == "teammates":
            for r in self.conn.execute(
                f"SELECT snapshot_id, {col}, rank FROM {table} WHERE species_id=? AND snapshot_id IN ({ids})", (species,)
            ):
                acc[r[col]] = acc.get(r[col], 0.0) + weight[r["snapshot_id"]] * (1.0 / max(1, r["rank"]))
            norm = sum(acc.values()) or 1.0
            acc = {k: v / norm * total_w for k, v in acc.items()}
        values = sorted(((k, v / total_w) for k, v in acc.items()), key=lambda kv: -kv[1])
        covered = sum(p for _, p in values) if kind in CATEGORICAL else 0.0
        d = Dist(species, kind, values, min(1.0, covered), max(0.0, 1.0 - covered) if kind in CATEGORICAL else 0.0,
                 self.freshness(snaps, previous))
        self._cache[key] = d
        return d

    # ---------- 포켓몬 출현 가중치와 추세 ----------
    def species_weights(self) -> dict[str, float]:
        """출현 가중치(합 1). 출처가 사용률 대신 순위만 주므로 w ∝ 1/순위^지수 로 추정한 값이다."""
        latest = self._latest_ranked()
        if latest is None:
            return {}
        a = float(self.cfg["rank_weight_exponent"])
        ranks = {r[0]: r[1] for r in self.conn.execute(
            "SELECT species_id, rank_position FROM usage_species WHERE snapshot_id=? AND rank_position IS NOT NULL", (latest.id,))}
        w = {s: 1.0 / (rank ** a) for s, rank in ranks.items()}
        tot = sum(w.values()) or 1.0
        return {s: v / tot for s, v in sorted(w.items(), key=lambda kv: -kv[1])}

    def _ranked(self) -> list[_Snap]:
        if not self.current:
            return []
        ids = ",".join(str(s.id) for s in self.current)
        have = {r[0] for r in self.conn.execute(f"SELECT DISTINCT snapshot_id FROM usage_species WHERE snapshot_id IN ({ids})")}
        return [s for s in self.current if s.id in have]

    def _latest_ranked(self) -> _Snap | None:
        ranked = self._ranked()
        return max(ranked, key=lambda s: s.page_date) if ranked else None

    def trend(self, species: str) -> Trend:
        """순위 추세. 비교할 스냅샷이 없거나 간격이 설정 범위를 벗어나면 known=False."""
        ranked = sorted(self._ranked(), key=lambda s: s.page_date)
        if len(ranked) < 2:
            return Trend(self._rank(ranked[-1].id, species) if ranked else None, None, None, False)
        latest = ranked[-1]
        lo, hi = int(self.cfg["trend_min_gap_days"]), int(self.cfg["trend_max_gap_days"])
        cands = [s for s in ranked[:-1] if lo <= (latest.page_date - s.page_date).days <= hi]
        if not cands:
            return Trend(self._rank(latest.id, species), None, None, False)
        before = max(cands, key=lambda s: s.page_date)
        return Trend(self._rank(latest.id, species), self._rank(before.id, species),
                     (latest.page_date - before.page_date).days, True)

    def _rank(self, snapshot_id: int, species: str) -> int | None:
        r = self.conn.execute("SELECT rank_position FROM usage_species WHERE snapshot_id=? AND species_id=?",
                              (snapshot_id, species)).fetchone()
        return r[0] if r else None
