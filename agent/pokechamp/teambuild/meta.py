"""상대 메타 팀 합성과 메타 시나리오 (PROJECT_PLAN 7절 2번, "O ~ 메타").

team_samples(실제 완성 팀)가 아직 없으므로 출현 가중치와 동료 통계로 6마리 팀을 합성한다.
- 다음 포켓몬 확률 ∝ 출현 가중치 × (1 + 세기 × 이미 뽑힌 포켓몬들의 동료 확률 평균). 도구는 겹치지 않게 원형을 고른다.
- 시나리오는 같은 팀 묶음을 다시 가중해서 만든다 (중요도 가중치 = 팀 안 포켓몬들의 가중치 비율의 곱):
  (a) 현재 분포, (b) 상위 포켓몬 비중을 올린 분포 w^지수, (c) 순위가 오르는 포켓몬을 더 반영한 분포.
  추세를 아는 포켓몬이 없으면 (c)는 건너뛰고 그 사실을 결과에 적는다.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np

from pokechamp.data.priors import Priors
from pokechamp.teambuild.pool import Threats, Unit


@dataclass
class Meta:
    teams: list[list[Unit]]
    scenario_names: list[str]
    scenario_weights: np.ndarray        # (시나리오 수, 팀 수), 행마다 합 1
    notes: list[str]


def teammate_table(priors: Priors, species: list[str]) -> dict[str, dict[str, float]]:
    out = {}
    for sp in species:
        d = priors.marginal(sp, "teammates")
        out[sp] = dict(d.values)
    return out


def synth_teams(threats: Threats, mates: dict[str, dict[str, float]], n: int, rng: random.Random,
                strength: float) -> list[list[Unit]]:
    species = list(threats.units)
    teams = []
    for _ in range(n):
        team: list[Unit] = []
        items: set[str] = set()
        banned: set[str] = set()
        while len(team) < 6:
            avail = [s for s in species if s not in banned and all(u.species != s for u in team)]
            if not avail:
                break
            w = []
            for s in avail:
                bonus = sum(mates.get(u.species, {}).get(s, 0.0) for u in team) / max(1, len(team))
                w.append(threats.weights[s] * (1 + strength * bonus))
            sp = rng.choices(avail, w)[0]
            opts = [u for u in threats.units[sp] if not u.item or u.item not in items]
            if not opts:
                banned.add(sp)
                continue
            u = rng.choices(opts, [o.weight for o in opts])[0]
            team.append(u)
            if u.item:
                items.add(u.item)
        teams.append(team)
    return teams


def scenarios(teams: list[list[Unit]], threats: Threats, priors: Priors, top_exponent: float,
              trend_strength: float) -> tuple[list[str], np.ndarray, list[str]]:
    p = threats.weights
    names, rows, notes = ["현재 분포"], [np.ones(len(teams))], []
    q = {s: w ** top_exponent for s, w in p.items()}
    tq = sum(q.values())
    ratio_b = {s: (q[s] / tq) / p[s] for s in p}
    names.append("상위 집중")
    rows.append(np.array([math.prod(ratio_b[u.species] for u in t) for t in teams]))
    deltas = {s: priors.trend(s).delta for s in p}
    known = {s: d for s, d in deltas.items() if d is not None}
    if known:
        scale = max(1, max(abs(d) for d in known.values()))
        q = {s: p[s] * math.exp(trend_strength * (known.get(s, 0) / scale) * 3) for s in p}
        tq = sum(q.values())
        ratio_c = {s: (q[s] / tq) / p[s] for s in p}
        names.append("상승세 반영")
        rows.append(np.array([math.prod(ratio_c[u.species] for u in t) for t in teams]))
    else:
        notes.append("순위 추세를 비교할 스냅샷이 없어 '상승세 반영' 시나리오를 건너뛰었다")
    W = np.vstack(rows)
    W /= W.sum(1, keepdims=True)
    return names, W, notes


def build_meta(threats: Threats, priors: Priors, n: int, seed: str, cfg: dict) -> Meta:
    rng = random.Random(seed)
    mates = teammate_table(priors, list(threats.units))
    teams = synth_teams(threats, mates, n, rng, float(cfg["teammate_strength"]))
    names, W, notes = scenarios(teams, threats, priors, float(cfg["scenario_top_exponent"]),
                                float(cfg["scenario_trend_strength"]))
    notes.insert(0, "실제 완성 팀(team_samples)이 없어 상대 팀은 출현 가중치와 동료 통계로 합성했다")
    return Meta(teams, names, W, notes)
