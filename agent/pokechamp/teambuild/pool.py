"""파티 추천의 후보와 위협, 1:1 상성 행렬 (PROJECT_PLAN 7절 1~2번).

- Unit: 구체적인 세트 하나(원형의 대표 성격·배분). 메가스톤을 들면 메가 전후 두 상태를 가진다.
- Pool: 내 후보 상태(행) × 위협 상태(열)의 빠른 상성 점수 행렬 M. 없는 칸은 MatchupCache로 계산해 캐시에 넣는다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from pokechamp.matchup.fast import MatchupCache, set_key
from pokechamp.sets.archetypes import Archetype, load_archetypes


@dataclass
class Unit:
    species: str                    # 기본 폼 id (종 중복 판정 기준)
    set: dict[str, Any]             # PokemonSet (species, item, ability, nature, evs, moves)
    mega_species: str | None        # 메가스톤 소지자면 메가 폼 id
    weight: float = 1.0             # 그 포켓몬 안에서의 원형 확률 (위협 쪽에서 씀)
    rule_generated: bool = True
    locked: bool = False            # 사용자가 세트까지 고정했으면 국소 탐색이 바꾸지 않는다
    row: int = -1                   # Pool 안의 상태 번호 (메가 전)
    mega_row: int | None = None     # 메가 후

    @property
    def item(self) -> str:
        return self.set.get("item") or ""

    @property
    def key(self) -> str:
        return set_key(self.set)


def unit_from_archetype(a: Archetype) -> Unit:
    nature = max(a.nature_dist, key=a.nature_dist.get) if a.nature_dist else "serious"
    spread = max(a.spread_dist, key=lambda x: x[1])[0] if a.spread_dist else {}
    st = {"species": a.species, "item": a.item or "", "ability": a.ability or "", "nature": nature,
          "evs": dict(spread), "moves": list(a.moves)}
    return Unit(a.species, st, a.mega_species, a.weight, a.rule_generated)


def species_units(conn: sqlite3.Connection, species: str, top_k: int, regulation: str | None = None,
                  include_mega: bool = True) -> list[Unit]:
    """원형 가중치 상위 top_k개. include_mega면 상위에 메가스톤 원형이 없을 때 가장 무거운 메가스톤 원형을 더한다."""
    archs = load_archetypes(conn, species, regulation)
    picked = archs[:top_k]
    if include_mega and not any(a.mega_species for a in picked):
        picked += [a for a in archs if a.mega_species][:1]
    return [unit_from_archetype(a) for a in picked]


class _Side:
    def __init__(self) -> None:
        self.states: list[tuple[dict[str, Any], bool]] = []
        self.index: dict[tuple[str, bool], int] = {}

    def add(self, st: dict[str, Any], mega: bool) -> int:
        k = (set_key(st), mega)
        if k not in self.index:
            self.index[k] = len(self.states)
            self.states.append((st, mega))
        return self.index[k]

    def register(self, u: Unit) -> Unit:
        u.row = self.add(u.set, False)
        u.mega_row = self.add(u.set, True) if u.mega_species else None
        return u


class Pool:
    """내 후보 상태 × 위협 상태 점수 행렬. 행이나 열을 더하면 다음 matrix() 호출 때 빈 칸만 계산한다."""

    def __init__(self, cache: MatchupCache):
        self.cache = cache
        self.mine, self.opp = _Side(), _Side()
        self._M = np.zeros((0, 0))

    def add_mine(self, u: Unit) -> Unit:
        return self.mine.register(u)

    def add_opp(self, u: Unit) -> Unit:
        return self.opp.register(u)

    def matrix(self) -> np.ndarray:
        r0, c0 = self._M.shape
        r1, c1 = len(self.mine.states), len(self.opp.states)
        if (r0, c0) == (r1, c1):
            return self._M
        M = np.zeros((r1, c1))
        M[:r0, :c0] = self._M
        cells = [(i, j) for i in range(r1) for j in range(c1) if i >= r0 or j >= c0]
        scores = self.cache.scores([(self.mine.states[i], self.opp.states[j]) for i, j in cells])
        for (i, j), s in zip(cells, scores):
            M[i, j] = s
        self._M = M
        return M


@dataclass
class Threats:
    """위협 목록: 포켓몬별 출현 가중치와 원형 Unit들 (가중치는 상위 원형 안에서 다시 정규화)."""
    weights: dict[str, float]
    units: dict[str, list[Unit]] = field(default_factory=dict)

    def column_weights(self, n_cols: int) -> np.ndarray:
        """열(위협 상태)별 출현 가중치. 메가스톤 원형은 메가 전후에 반씩 나눈다."""
        w = np.zeros(n_cols)
        for sp, us in self.units.items():
            for u in us:
                p = self.weights[sp] * u.weight
                if u.mega_row is not None:
                    w[u.row] += p / 2
                    w[u.mega_row] += p / 2
                else:
                    w[u.row] += p
        return w / (w.sum() or 1)


def build_threats(conn: sqlite3.Connection, pool: Pool, species_weights: dict[str, float], n_species: int,
                  top_k: int, regulation: str | None = None) -> Threats:
    top = list(species_weights)[:n_species]
    tot = sum(species_weights[s] for s in top) or 1
    th = Threats({s: species_weights[s] / tot for s in top})
    for sp in top:
        us = species_units(conn, sp, top_k, regulation, include_mega=False)
        if not us:
            th.weights.pop(sp)
            continue
        wsum = sum(u.weight for u in us) or 1
        for u in us:
            u.weight /= wsum
            pool.add_opp(u)
        th.units[sp] = us
    tot = sum(th.weights.values()) or 1
    th.weights = {s: w / tot for s, w in th.weights.items()}
    return th
