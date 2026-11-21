"""로컬 메타 (PROJECT_PLAN 4.5 "로컬 메타 반영", 10절 "상대 팀과 관찰된 세트는 로컬 메타에 반영한다").

내가 실제로 둔 배틀(my_battles, source='manual')의 상대 팀 기록을 공개 통계와 섞는다. 자동 배틀(sim)은 쓰지 않는다.
- 출현 가중치: 디리클레 사후 평균. 공개 통계 가중치를 배틀 strength판 분량의 사전으로 두고 로컬 등장 횟수를 더한다.
  w_s = (strength × 6 × w_pub(s) + count(s)) / (strength × 6 + 6 × n). 판이 쌓일수록 로컬 비중이 커진다.
- 선출률(상대 모델): 종마다 팀 프리뷰에 보인 횟수 중 실제로 나온 비율. 사전 0.5를 강도 prior_n판으로 두는 베타 사후 평균.
  표본이 적으면 0.5에 가깝다 (표본이 충분할 때만 반영한다는 원칙).
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any


def local_records(conn: sqlite3.Connection, regulation: str | None = None) -> list[list[dict[str, Any]]]:
    q = "SELECT opp_team_json FROM my_battles WHERE source='manual'"
    args: list[Any] = []
    if regulation:
        q += " AND regulation=?"
        args.append(regulation)
    return [json.loads(r[0]) for r in conn.execute(q, args)]


def blend_weights(public: dict[str, float], counts: dict[str, int], n_battles: int, strength: float) -> dict[str, float]:
    """공개 출현 가중치(합 1)와 로컬 등장 횟수의 디리클레 사후 평균 (합 1)."""
    if n_battles <= 0:
        return dict(public)
    a = strength * 6
    keys = set(public) | set(counts)
    raw = {k: a * public.get(k, 0.0) + counts.get(k, 0) for k in keys}
    tot = sum(raw.values()) or 1.0
    return {k: v / tot for k, v in sorted(raw.items(), key=lambda kv: -kv[1])}


def species_counts(records: list[list[dict[str, Any]]]) -> Counter:
    c: Counter = Counter()
    for team in records:
        for m in team:
            c[m["species"]] += 1
    return c


def bring_stats(records: list[list[dict[str, Any]]]) -> dict[str, tuple[int, int]]:
    """종 → (팀 프리뷰에 보인 횟수, 실제로 나온 횟수)."""
    seen: Counter = Counter()
    brought: Counter = Counter()
    for team in records:
        for m in team:
            seen[m["species"]] += 1
            brought[m["species"]] += int(bool(m.get("brought")))
    return {s: (seen[s], brought[s]) for s in seen}


def bring_rates(stats: dict[str, tuple[int, int]], prior: float = 0.5, prior_n: float = 4.0) -> dict[str, float]:
    return {s: (prior * prior_n + b) / (prior_n + n) for s, (n, b) in stats.items()}
