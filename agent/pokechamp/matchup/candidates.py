"""세트 원형에서 상성 계산용 대표 세트를 만든다. 원형마다 가장 흔한 성격과 배분을 붙인다.
메가스톤 원형은 메가 전과 후 두 상태를 모두 낸다 (PROJECT_PLAN 5.1, 6절)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from pokechamp.sets.archetypes import load_archetypes


@dataclass
class Candidate:
    species: str
    set: dict[str, Any]
    mega: bool
    weight: float          # 그 포켓몬 안에서의 원형 확률

    @property
    def label(self) -> str:
        return f"{self.species}{'(메가)' if self.mega else ''} @{self.set.get('item') or '-'}"


def candidates(conn: sqlite3.Connection, species: str, top_k: int = 3, regulation: str | None = None) -> list[Candidate]:
    out = []
    for a in load_archetypes(conn, species, regulation)[:top_k]:
        nature = max(a.nature_dist, key=a.nature_dist.get) if a.nature_dist else "serious"
        spread = max(a.spread_dist, key=lambda x: x[1])[0] if a.spread_dist else {}
        st = {"species": species, "item": a.item or "", "ability": a.ability or "", "nature": nature,
              "evs": dict(spread), "moves": list(a.moves)}
        out.append(Candidate(species, st, False, a.weight))
        if a.mega_species:
            out.append(Candidate(species, st, True, a.weight))
    return out
