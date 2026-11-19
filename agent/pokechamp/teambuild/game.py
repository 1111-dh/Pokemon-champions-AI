"""선출 게임 (PROJECT_PLAN 7절 V(T,O), 8절과 같은 구조).

내 선출(3마리) × 상대 선출(3마리)의 보수 행렬을 1:1 상성 행렬 M으로 근사하고, 제로섬 균형 값을 구한다.
- 3:3 값 = w × (1:1 짝짓기 최선값: 내 3마리를 상대 3마리에 하나씩 배정한 평균의 최댓값) + (1−w) × (9쌍 평균).
  짝짓기 항은 "상대 하나하나를 맡을 포켓몬이 있는가", 평균 항은 "맡지 않은 상대에게도 버티는가"를 본다.
- 선봉은 빠른 근사 단계에서 따로 다루지 않는다 (선출 3마리 조합 20가지). 선봉 포함 60가지는 단계 4에서 정밀 계산한다.
- 메가: 내 선출은 메가스톤 소지자 최대 1마리이고 그 포켓몬은 메가 폼으로 센다.
  상대는 제약 없이 열거하고, 선출 안에 소지자가 여럿이면 슬롯 순서상 첫 소지자만 메가 폼으로 센다 (배틀당 1회).
"""

from __future__ import annotations

from itertools import combinations, permutations

import numpy as np
from scipy.optimize import linprog

PERMS3 = np.array(list(permutations(range(3))))


def subsets(rows: list[int], mega_rows: list[int | None], limit_mega: bool = True) -> np.ndarray:
    """팀(행 번호와 메가 행 번호)에서 선출 가능한 조합 → (조합 수, 크기) 상태 행 번호 배열.
    limit_mega=True(내 쪽)면 소지자 2마리 이상인 조합을 뺀다. 그런 조합밖에 없으면(부분 팀) 첫 소지자만 메가로 센다."""
    k = len(rows)
    size = min(3, k)
    out, fallback = [], []
    for combo in combinations(range(k), size):
        holders = [i for i in combo if mega_rows[i] is not None]
        state = [mega_rows[i] if holders and i == holders[0] else rows[i] for i in combo]
        (fallback if limit_mega and len(holders) > 1 else out).append(state)
    return np.array(out or fallback, dtype=np.int64)


def subset_slots(mega_rows: list[int | None], limit_mega: bool = True) -> list[tuple[int, ...]]:
    """subsets()와 같은 순서의 슬롯 번호 조합 (시뮬레이션에서 팀 프리뷰 선택을 만들 때 쓴다)."""
    k = len(mega_rows)
    size = min(3, k)
    out, fallback = [], []
    for combo in combinations(range(k), size):
        holders = [i for i in combo if mega_rows[i] is not None]
        (fallback if limit_mega and len(holders) > 1 else out).append(combo)
    return out or fallback


def payoff_tensor(M: np.ndarray, my_subsets: np.ndarray, opp_subsets: np.ndarray, assignment_weight: float) -> np.ndarray:
    """M: (내 상태, 상대 상태) 1:1 점수. my_subsets: (nA, s). opp_subsets: (n, nB, 3).
    → (n, nA, nB) 보수 (내 승률 근사)."""
    s = my_subsets.shape[1]
    sub = M[my_subsets[None, :, None, :, None], opp_subsets[:, None, :, None, :]]   # (n, nA, nB, s, 3)
    mean = sub.mean(axis=(-1, -2))
    if s == 3:
        assign = np.max(np.stack([sub[..., [0, 1, 2], p].mean(-1) for p in PERMS3]), axis=0)
    else:   # 부분 팀: 상대마다 가장 잘 맡는 내 포켓몬 (중복 허용)
        assign = sub.max(axis=-2).mean(-1)
    return assignment_weight * assign + (1 - assignment_weight) * mean


def solve_batch(P: np.ndarray, iterations: int = 300) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """여러 제로섬 게임을 한꺼번에 근사로 푼다 (곱셈 가중치 갱신, 평균 전략).
    P: (n, nA, nB), 행 쪽이 최대화. → (값, 행 혼합 전략 (n,nA), 열 혼합 전략 (n,nB))."""
    n, na, nb = P.shape
    eta = np.sqrt(8 * np.log(max(na, nb, 2)) / iterations)
    lx, ly = np.zeros((n, na)), np.zeros((n, nb))
    sx, sy = np.zeros((n, na)), np.zeros((n, nb))
    for _ in range(iterations):
        x = np.exp(lx - lx.max(1, keepdims=True)); x /= x.sum(1, keepdims=True)
        y = np.exp(ly - ly.max(1, keepdims=True)); y /= y.sum(1, keepdims=True)
        sx += x; sy += y
        lx += eta * np.einsum("nab,nb->na", P, y)
        ly -= eta * np.einsum("nab,na->nb", P, x)
    x, y = sx / iterations, sy / iterations
    lower = np.einsum("nab,na->nb", P, x).min(1)     # 내 평균 전략이 보장하는 값
    upper = np.einsum("nab,nb->na", P, y).max(1)     # 상대 평균 전략이 허용하는 값
    return (lower + upper) / 2, x, y


def solve_exact(P: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """한 게임을 선형계획으로 정확히 푼다 (scipy linprog). → (값, 행 전략, 열 전략)."""
    na, nb = P.shape
    # 변수 [x_1..x_na, v], v 최대화: 모든 열 j에 대해 Σ x_i P_ij ≥ v
    c = np.zeros(na + 1); c[-1] = -1
    A = np.hstack([-P.T, np.ones((nb, 1))])
    res = linprog(c, A_ub=A, b_ub=np.zeros(nb), A_eq=np.hstack([np.ones((1, na)), [[0]]]), b_eq=[1],
                  bounds=[(0, None)] * na + [(None, None)], method="highs")
    x = np.clip(res.x[:na], 0, None); x /= x.sum()
    y = np.clip(-res.ineqlin.marginals, 0, None)
    y = y / y.sum() if y.sum() > 0 else np.full(nb, 1 / nb)
    return float(res.x[-1]), x, y
