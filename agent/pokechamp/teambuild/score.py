"""팀 점수 (PROJECT_PLAN 7절 "팀 점수의 정의").

Score(T) = E[V] − α·(E[V] − CVaR_q[V]) − β·SD_s(E_s[V])
- V(T,O): 선출 게임 균형 값 (game.py). E, CVaR는 현재 분포 가중치, SD는 시나리오별 기대값의 표준편차.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pokechamp.teambuild.game import payoff_tensor, solve_batch, solve_exact, subsets
from pokechamp.teambuild.meta import Meta
from pokechamp.teambuild.pool import Pool, Unit


@dataclass
class TeamScore:
    score: float
    mean: float
    cvar: float
    sd: float
    scenario_means: list[float]
    values: np.ndarray            # 상대 팀별 V


def weighted_cvar(v: np.ndarray, w: np.ndarray, q: float) -> float:
    order = np.argsort(v)
    acc, tot = 0.0, 0.0
    for i in order:
        take = min(w[i], q - acc)
        if take <= 0:
            break
        tot += take * v[i]
        acc += take
    return tot / acc if acc else float(v.min())


class Evaluator:
    def __init__(self, pool: Pool, meta: Meta, cfg: dict):
        self.pool, self.meta, self.cfg = pool, meta, cfg
        self.alpha, self.beta = float(cfg["alpha"]), float(cfg["beta"])
        self.q = float(cfg["cvar_fraction"])
        self.w_assign = float(cfg["assignment_weight"])
        self.iters = int(cfg["nash_iterations"])
        self.opp_subsets = np.stack([subsets([u.row for u in t], [u.mega_row for u in t], limit_mega=False)
                                     for t in meta.teams])                      # (n, 20, 3)
        self.evals = 0

    def my_subsets(self, team: list[Unit]) -> np.ndarray:
        return subsets([u.row for u in team], [u.mega_row for u in team], limit_mega=True)

    def payoff(self, team: list[Unit]) -> tuple[np.ndarray, np.ndarray]:
        A = self.my_subsets(team)
        return payoff_tensor(self.pool.matrix(), A, self.opp_subsets, self.w_assign), A

    def summarize(self, V: np.ndarray) -> TeamScore:
        W = self.meta.scenario_weights
        means = W @ V
        mean = float(means[0])
        cvar = weighted_cvar(V, W[0], self.q)
        sd = float(np.std(means)) if len(means) > 1 else 0.0
        return TeamScore(mean - self.alpha * (mean - cvar) - self.beta * sd, mean, cvar, sd,
                         [float(x) for x in means], V)

    def score(self, team: list[Unit]) -> TeamScore:
        self.evals += 1
        P, _ = self.payoff(team)
        V, _, _ = solve_batch(P, self.iters)
        return self.summarize(V)

    def exact(self, team: list[Unit]) -> tuple[TeamScore, np.ndarray, np.ndarray, np.ndarray]:
        """최종 출력용: 상대 팀마다 선형계획으로 정확히 푼다. → (점수, 내 조합, 내 전략 (n,nA), 상대 전략 (n,20))."""
        P, A = self.payoff(team)
        sols = [solve_exact(P[i]) for i in range(P.shape[0])]
        V = np.array([s[0] for s in sols])
        return self.summarize(V), A, np.stack([s[1] for s in sols]), np.stack([s[2] for s in sols])
