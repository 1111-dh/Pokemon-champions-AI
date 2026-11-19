"""선출 게임 풀이: 더블 오라클 + 선형계획 + 상대 모델 혼합 (PROJECT_PLAN 8절).

엔진과 DB를 모르는 순수 계산이다. 정밀 칸 계산은 evaluate 함수로 받는다.

- A: 근사 보수 행렬 (내 선출 × 상대 선출, 빠른 상성으로 채움). 값의 눈금이 정밀값(승률)과 다르므로,
  정밀 계산한 칸으로 선형 보정한 뒤(calibrate) 정밀값과 섞어 쓴다 (H: 섞은 행렬).
- 더블 오라클: 근사 균형의 상위 선출로 후보 집합을 만들고, 후보 칸만 정밀 계산해 균형을 구한다.
  전체 선출 중 그 균형보다 나은 응수(H 기준)가 있으면 후보에 넣고 반복한다.
  근사로 더 나은 응수가 없으면 바로 멈추지 않고, 근사 순위가 높은 미탐색 선출을 양쪽 probe개씩 정밀 계산해 확인한다
  (탐색 확인). 넣은 선출이 균형에 들어오지 않는 일이 probe_rounds번 이어지면 수렴으로 본다.
  (작은 사례에서 탐색 확인 없이는 칸 28개만 보고 멈춰, 전체 정밀 행렬 기준 최선 응수에 0.545까지 밀렸다.)
  수렴하면 균형 지지 칸에 샘플을 더 넣어(정밀화) 다시 확인한다. 시간 예산이 끝나면 그때까지의 결과를 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from pokechamp.teambuild.game import solve_exact

Cell = tuple[int, int]


@dataclass
class PreciseCells:
    """정밀 칸의 누적 결과 (판 수와 점수 합). 같은 칸에 샘플을 더 넣으면 합쳐진다."""
    total: dict[Cell, float] = field(default_factory=dict)
    games: dict[Cell, int] = field(default_factory=dict)

    def add(self, cell: Cell, score_sum: float, n: int) -> None:
        self.total[cell] = self.total.get(cell, 0.0) + score_sum
        self.games[cell] = self.games.get(cell, 0) + n

    def value(self, cell: Cell) -> float:
        return self.total[cell] / self.games[cell]

    def __contains__(self, cell: Cell) -> bool:
        return self.games.get(cell, 0) > 0

    def __len__(self) -> int:
        return len(self.games)

    def n_games(self) -> int:
        return sum(self.games.values())


# evaluate(요청 목록[(칸, 추가 샘플 수)]) → 실제로 계산한 요청 수 (예산이 모자라면 앞에서부터 일부만)
Evaluate = Callable[[list[tuple[Cell, int]]], int]


def calibrate(A: np.ndarray, P: PreciseCells, min_cells: int = 8) -> tuple[np.ndarray, tuple[float, float]]:
    """정밀 칸으로 근사값을 선형 보정(최소제곱, 기울기는 0.3~2로 제한)하고 정밀 칸은 정밀값으로 덮는다.
    칸이 적거나 근사값 분산이 거의 없으면 보정 없이 덮기만 한다. → (H, (절편, 기울기))."""
    H = A.copy()
    a, b = 0.0, 1.0
    if len(P) >= min_cells:
        xs = np.array([A[c] for c in P.games])
        ys = np.array([P.value(c) for c in P.games])
        if xs.var() > 1e-4:
            b, a = np.polyfit(xs, ys, 1)
            b = float(np.clip(b, 0.3, 2.0))
            a = float(ys.mean() - b * xs.mean())
            H = np.clip(a + b * A, 0.0, 1.0)
    for c in P.games:
        H[c] = P.value(c)
    return H, (a, b)


def _top(scores: np.ndarray, k: int, exclude: list[int], desc: bool = True) -> list[int]:
    order = np.argsort(-scores if desc else scores, kind="stable")
    return [int(i) for i in order if int(i) not in exclude][:k]


@dataclass
class OracleResult:
    rows: list[int]                 # 후보 집합 (내 선출 번호)
    cols: list[int]                 # 후보 집합 (상대 선출 번호)
    H: np.ndarray                   # 정밀값과 보정 근사값을 섞은 행렬
    value: float                    # 후보 게임의 균형 값
    x: np.ndarray                   # 내 균형 전략 (전체 길이)
    y: np.ndarray                   # 상대 균형 전략 (전체 길이)
    precise: PreciseCells
    calibration: tuple[float, float]
    iterations: int
    converged: bool                 # 양쪽 모두 더 나은 응수가 없어서 멈췄는가 (예산으로 멈추면 False)
    refined: bool                   # 균형 지지 칸을 최대 샘플까지 정밀화했는가
    history: list[dict] = field(default_factory=list)

    def weighted_precise_ratio(self, x: np.ndarray | None = None) -> float:
        """균형 확률(내 전략 x × 상대 전략 y)로 가중한 정밀 칸 비율: 결과가 정밀값에 기대는 정도."""
        x = self.x if x is None else x
        tot = num = 0.0
        for i in np.nonzero(x > 1e-9)[0]:
            for j in np.nonzero(self.y > 1e-9)[0]:
                w = float(x[i] * self.y[j])
                tot += w
                num += w if (int(i), int(j)) in self.precise else 0.0
        return num / tot if tot else 0.0

    def candidate_precise_ratio(self) -> float:
        cells = [(i, j) for i in self.rows for j in self.cols]
        return sum(1 for c in cells if c in self.precise) / max(1, len(cells))


def double_oracle(A: np.ndarray, evaluate: Evaluate, precise: PreciseCells, *, initial: int = 4, add_per_iter: int = 2,
                  eps: float = 0.01, samples: int = 16, max_samples: int = 32, max_iterations: int = 60,
                  probe: int = 4, probe_rounds: int = 2, final: str = "candidates") -> OracleResult:
    """final: 최종 균형을 어디서 푸는가.
    - "candidates": 후보 행 × 후보 열 (고전적인 더블 오라클)
    - "rows_all": 후보 행 × 상대 선출 전체. 후보 밖 열은 보정 근사값을 쓴다 (예산이 모자라 후보가 작을 때
      아직 정밀 계산하지 못한 상대 선출을 무시하지 않는다)"""
    nA, nB = A.shape
    _, x0, y0 = solve_exact(A)
    # 근사 균형의 지지 선출(확률 큰 순) → 모자라면 근사 균형에 대한 응수 값 순으로 채운다
    rows = [i for i in _top(x0, initial, []) if x0[i] > 1e-6]
    rows += _top(A @ y0, initial - len(rows), rows)
    cols = [j for j in _top(y0, initial, []) if y0[j] > 1e-6]
    cols += _top(x0 @ A, initial - len(cols), cols, desc=False)
    history: list[dict] = []
    converged = refined = budget_out = False
    it = 0
    H, cal = calibrate(A, precise)
    value, x, y = 0.5, np.zeros(nA), np.zeros(nB)
    complete: tuple[list[int], list[int]] | None = None    # 모든 칸이 정밀 계산된 마지막 후보 집합
    quiet = 0                                              # 연속으로 아무것도 찾지 못한 탐색 확인 횟수
    probed: tuple[list[int], list[int]] | None = None      # 직전에 탐색 확인으로 넣은 선출
    while it < max_iterations:
        it += 1
        need = [((i, j), samples) for i in rows for j in cols if (i, j) not in precise]
        if need and evaluate(need) < len(need):
            budget_out = True
            # 예산이 모자라 새 후보의 칸을 다 못 채웠다. 정밀 계산을 못 한 선출에 확률을 주지 않도록
            # 마지막으로 완전했던 후보 집합으로 되돌린다 (그런 집합이 아직 없으면 보정 근사값으로 채워 푼다)
            if complete is not None:
                rows, cols = list(complete[0]), list(complete[1])
        else:
            complete = (list(rows), list(cols))
        H, cal = calibrate(A, precise)
        v, xs, ys = solve_exact(H[np.ix_(rows, cols)])
        x, y = np.zeros(nA), np.zeros(nB)
        x[rows], y[cols] = xs, ys
        value = v
        row_vals = H[:, cols] @ ys
        col_vals = xs @ H[rows, :]
        new_rows = [i for i in _top(row_vals, add_per_iter, rows) if row_vals[i] > v + eps]
        new_cols = [j for j in _top(col_vals, add_per_iter, cols, desc=False) if col_vals[j] < v - eps]
        history.append({"iteration": it, "rows": len(rows), "cols": len(cols), "value": round(float(v), 4),
                        "best_row": round(float(row_vals.max()), 4), "best_col": round(float(col_vals.min()), 4),
                        "precise_cells": len(precise), "games": precise.n_games(), "probe": probed is not None})
        if probed is not None:
            # 탐색 확인으로 넣은 선출이 정밀값으로 균형에 들어왔으면 근사가 놓친 응수를 찾은 것이다
            found = any(x[i] > 1e-6 for i in probed[0] if i in rows) or any(y[j] > 1e-6 for j in probed[1] if j in cols)
            quiet = 0 if found else quiet + 1
            probed = None
        if budget_out:
            break
        if new_rows or new_cols:
            rows += new_rows
            cols += new_cols
            quiet = 0
            continue
        if quiet < probe_rounds:
            # 근사로는 더 나은 응수가 없다. 근사가 틀렸을 수 있으므로 근사 순위가 높은 미탐색 선출을 정밀 계산해 확인한다
            pr, pc = _top(row_vals, probe, rows), _top(col_vals, probe, cols, desc=False)
            if pr or pc:
                rows += pr
                cols += pc
                probed = (pr, pc)
                continue
        # 수렴: 균형 지지 칸에 샘플을 더 넣는다 (영향이 큰 칸일수록 샘플을 늘린다, 8절 3번)
        converged = True
        support = [(i, j) for i in rows if x[i] > 1e-6 for j in cols if y[j] > 1e-6]
        more = [(c, samples) for c in support if precise.games.get(c, 0) < max_samples]
        if not more:
            refined = True
            break
        done = evaluate(more)
        if done < len(more):
            break
        converged = False   # 정밀화로 값이 바뀌었으니 응수를 다시 확인한다
    if final == "rows_all":
        H, cal = calibrate(A, precise)
        v, xs, ys_all = solve_exact(H[rows, :])
        x = np.zeros(nA)
        x[rows] = xs
        y = ys_all
        value = v
    return OracleResult(rows, cols, H, float(value), x, y, precise, cal, it, converged, refined, history)


def opponent_model(H: np.ndarray, my_legal: list[int], prior: np.ndarray, temperature: float) -> np.ndarray:
    """상대 선출 분포의 단순 가정 (8절 "상대 모델 데이터"): 사용률 사전 × exp(τ × 상대 입장의 근사 승률).
    상대 입장의 승률은 내가 합법 선출을 고르게 낸다고 볼 때의 값이다 (사람이 '대체로 유리한' 선출을 고른다는 가정)."""
    u = 1.0 - H[my_legal, :].mean(axis=0)
    logit = np.log(np.maximum(prior, 1e-12)) + temperature * (u - u.max())
    q = np.exp(logit - logit.max())
    return q / q.sum()


def mix_with_model(r: OracleResult, q: np.ndarray, mix: float) -> tuple[np.ndarray, int]:
    """균형 해와 상대 모델 최선 응수의 가중 평균 (8절 5번). 최선 응수는 후보 행(정밀 계산한 선출) 안에서 고른다.
    → (최종 전략, 최선 응수 번호)."""
    vals = r.H[r.rows, :] @ q
    br = r.rows[int(np.argmax(vals))]
    s = (1 - mix) * r.x
    s[br] += mix
    return s / s.sum(), br
