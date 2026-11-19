"""파티 추천 검증 (PROJECT_PLAN 7절 6번, 인수 기준): 추천 팀이 무작위 팀과 사용률 상위 팀보다 시뮬레이션 승률이 높은가.

- 상대: 메타 팀을 추천 때와 **다른 시드**로 새로 합성하고, 세트는 원형 분포 전체에서 새로 뽑는다
  (추천이 쓴 상대 팀·대표 세트에 맞춰진 결과를 피한다).
- 짝지은 대전: 같은 시드로 진영을 바꿔 두 판씩 둔다. 짝 하나의 점수(0, 0.5, 1)를 표본으로 신뢰구간을 낸다.
- 정책: 양쪽 모두 최대 데미지 봇. 선출은 양쪽 모두 무작위다 (세 팀에 같은 조건. 균형 선출은 단계 4에서 붙인다).
- 무작위 팀은 짝마다 다른 합법 팀을 써서 "무작위 팀의 기대 승률"을 잰다.
"""

from __future__ import annotations

import math
import random
from typing import Any

from pokechamp.engine_client import EngineClient
from pokechamp.teambuild.meta import synth_teams, teammate_table
from pokechamp.sets.archetypes import load_archetypes, sample_sets
from pokechamp.teambuild.pool import Unit, species_units

ENGINE_KEYS = ("species", "item", "ability", "nature", "evs", "moves")
BATCH = 400


def engine_set(st: dict[str, Any]) -> dict[str, Any]:
    return {k: st[k] for k in ENGINE_KEYS if k in st}


def opponents(builder, n: int, seed: str) -> list[list[dict[str, Any]]]:
    """추천과 다른 시드로 합성한 상대 팀 n개. 포켓몬 구성은 같은 위협 목록·동료 통계로 정하되,
    세트는 추천이 쓴 대표 세트가 아니라 각 포켓몬의 원형 분포 전체에서 새로 뽑는다(성격·배분도 분포에서)."""
    th = builder.threats
    rng = random.Random(f"{seed}-validate")
    mates = teammate_table(builder.priors, list(th.units))
    teams = synth_teams(th, mates, n, rng, float(builder.cfg["teammate_strength"]))
    archs: dict[str, list] = {}
    out = []
    for t in teams:
        used: set[str] = set()
        team = []
        for u in t:
            pool = archs.setdefault(u.species, load_archetypes(builder.conn, u.species, builder.regulation))
            st = None
            for _ in range(20):
                cand = sample_sets(pool, 1, rng)[0]
                if not cand["item"] or cand["item"] not in used:
                    st = cand
                    break
            st = st or {**u.set}
            if st.get("item"):
                used.add(st["item"])
            team.append(engine_set(st))
        out.append(team)
    return out


def usage_top_team(builder) -> list[Unit]:
    """사용률(출현 가중치) 상위부터 가장 흔한 원형으로 채운 팀. 종·도구 중복과 메가 후보 수 제약만 지킨다."""
    team: list[Unit] = []
    for sp in builder.weights:
        for u in species_units(builder.conn, sp, 1, builder.regulation, include_mega=False):
            if builder.can_add(team, u):
                team.append(u)
        if len(team) == 6:
            break
    return team


def paired_games(client: EngineClient, mine: list[list[dict]] | list[dict], opps: list[list[dict]], seed: str) -> dict[str, Any]:
    """mine이 팀 하나면 모든 짝에 같은 팀, 팀 목록이면 짝마다 해당 팀."""
    per_pair = mine if mine and isinstance(mine[0], list) else [mine] * len(opps)
    items = []
    for i, (m, o) in enumerate(zip(per_pair, opps)):
        s = f"{seed}-pair{i}"
        items.append({"p1": m, "p2": o, "seed": s, "policy": "maxdamage", "reps": 1})
        items.append({"p1": o, "p2": m, "seed": s, "policy": "maxdamage", "reps": 1})
    results = []
    for k in range(0, len(items), BATCH):
        results += client.batch("/simulate", items[k:k + BATCH])
    pair_scores, errors = [], 0
    for i in range(len(opps)):
        sc = []
        for side, r in (("p1", results[2 * i]), ("p2", results[2 * i + 1])):
            if not r.get("ok"):
                errors += 1
                continue
            w = r["result"]["wins"]
            if w["unfinished"]:
                continue
            sc.append(1.0 if w[side] else 0.0 if (w["p1"] or w["p2"]) else 0.5)
        if sc:
            pair_scores.append(sum(sc) / len(sc))
    return summarize(pair_scores, errors)


def summarize(pair_scores: list[float], errors: int = 0) -> dict[str, Any]:
    n = len(pair_scores)
    if not n:
        return {"win_rate": None, "lo": None, "hi": None, "pairs": 0, "games": 0, "errors": errors}
    mean = sum(pair_scores) / n
    var = sum((x - mean) ** 2 for x in pair_scores) / max(1, n - 1)
    half = 1.96 * math.sqrt(var / n)
    return {"win_rate": mean, "lo": max(0.0, mean - half), "hi": min(1.0, mean + half), "pairs": n, "games": 2 * n,
            "errors": errors, "scores": pair_scores}


def fmt(r: dict[str, Any]) -> str:
    if r["win_rate"] is None:
        return "결과 없음"
    return f"{r['win_rate']:.1%} (95% 구간 {r['lo']:.1%}~{r['hi']:.1%}, {r['games']:,}판)"


def paired_diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """같은 상대·시드로 둔 두 팀의 짝별 점수 차이 (a − b)와 95% 구간."""
    d = [x - y for x, y in zip(a.get("scores", []), b.get("scores", []))]
    if not d:
        return {"diff": None}
    mean = sum(d) / len(d)
    half = 1.96 * math.sqrt(sum((x - mean) ** 2 for x in d) / max(1, len(d) - 1) / len(d))
    return {"diff": mean, "lo": mean - half, "hi": mean + half, "pairs": len(d)}


def validate(builder, team: list[Unit], n_pairs: int, seed: str, client: EngineClient | None = None) -> dict[str, Any]:
    client = client or EngineClient()
    opps = opponents(builder, n_pairs, seed)
    rec = paired_games(client, [engine_set(u.set) for u in team], opps, f"{seed}-v")
    top = usage_top_team(builder)
    usage = paired_games(client, [engine_set(u.set) for u in top], opps, f"{seed}-v")
    rand_teams = [r["result"]["team"] for r in client.batch("/random_team", [{"seed": f"{seed}-rand{i}"} for i in range(n_pairs)])]
    rnd = paired_games(client, rand_teams, opps, f"{seed}-v")
    return {
        "recommended": rec, "usage_top": usage, "random": rnd,
        "usage_top_team": [builder.names.ko("species", u.species) for u in top],
        "vs_usage_top": paired_diff(rec, usage), "vs_random": paired_diff(rec, rnd),
        "policy": "최대 데미지 봇, 양쪽 무작위 선출", "seed": seed,
    }
