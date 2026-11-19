"""빠른 균형 선출: 양쪽 세트를 아는 상태에서 빠른 상성 근사만으로 선출 게임(선봉 포함)을 풀고 선출을 뽑는다.

시뮬레이션 검증과 파티 추천 재평가에서 "양쪽이 근사 균형으로 선출한다"는 조건을 만들 때 쓴다.
(선출 추천 본체(recommend.py)는 상대 세트를 모르므로 이 함수를 쓰지 않는다.)
- 보수: 선출 추천과 같은 근사 = (1−λ) × 선출 게임 값 + λ × 선봉끼리 1:1 값.
- 내 쪽만 메가스톤 소지자 제한을 두고(my_max_mega), 상대는 제한하지 않는다 (PROJECT_PLAN 5.1). 검증에서 양쪽을
  같은 규칙으로 두려면 opp_max_mega도 준다.
"""

from __future__ import annotations

import random
from typing import Any, Callable

import numpy as np

from pokechamp.matchup.fast import MatchupCache
from pokechamp.select.picks import Pick, enumerate_picks, mega_slot
from pokechamp.teambuild.game import payoff_tensor, solve_exact


def _rows(sets: list[dict], mega_of: Callable[[dict], str | None], states: dict, order: list) -> tuple[list[int], list[int | None]]:
    from pokechamp.matchup.fast import set_key

    def add(st, mega):
        k = (set_key(st), mega)
        if k not in states:
            states[k] = len(order)
            order.append((st, mega))
        return states[k]
    return [add(s, False) for s in sets], [add(s, True) if mega_of(s) else None for s in sets]


def quick_game(cache: MatchupCache, my_sets: list[dict], opp_sets: list[dict], mega_of: Callable[[dict], str | None],
               w_assign: float, lead_weight: float, my_max_mega: int | None = 1,
               opp_max_mega: int | None = None) -> tuple[np.ndarray, list[Pick], list[Pick]]:
    """→ (근사 보수 행렬 (내 선출 × 상대 선출), 내 선출 목록, 상대 선출 목록)."""
    ms, mo, os_, oo = {}, [], {}, []
    my_r, my_m = _rows(my_sets, mega_of, ms, mo)
    op_r, op_m = _rows(opp_sets, mega_of, os_, oo)
    my_h = {i for i, r in enumerate(my_m) if r is not None}
    op_h = {i for i, r in enumerate(op_m) if r is not None}
    my_picks = enumerate_picks(len(my_sets), my_h, my_max_mega)
    opp_picks = enumerate_picks(len(opp_sets), op_h, opp_max_mega)
    M = np.array(cache.scores([(a, b) for a in mo for b in oo])).reshape(len(mo), len(oo))

    def pick_rows(p: Pick, rows, mrows, holders):
        m = mega_slot(p, holders)
        return [mrows[i] if i == m else rows[i] for i in p.order]
    myR = np.array([pick_rows(p, my_r, my_m, my_h) for p in my_picks], dtype=np.int64)
    opR = np.array([[pick_rows(p, op_r, op_m, op_h) for p in opp_picks]], dtype=np.int64)
    base = payoff_tensor(M, myR, opR, w_assign)[0]
    lead = M[myR[:, 0, None], opR[0, None, :, 0]]
    return (1 - lead_weight) * base + lead_weight * lead, my_picks, opp_picks


def sample_equilibrium(A: np.ndarray, rng: random.Random) -> tuple[int, int, float]:
    """근사 게임의 균형을 풀고 양쪽 선출을 균형 확률대로 하나씩 뽑는다. → (내 번호, 상대 번호, 균형 값)."""
    v, x, y = solve_exact(A)
    i = rng.choices(range(len(x)), weights=list(np.maximum(x, 0)))[0]
    j = rng.choices(range(len(y)), weights=list(np.maximum(y, 0)))[0]
    return i, j, v


def equilibrium_choices(cache: MatchupCache, my_sets: list[dict], opp_sets: list[dict], mega_of, rng: random.Random,
                        w_assign: float, lead_weight: float, my_max_mega: int | None = 1,
                        opp_max_mega: int | None = None) -> dict[str, Any]:
    A, mp, op = quick_game(cache, my_sets, opp_sets, mega_of, w_assign, lead_weight, my_max_mega, opp_max_mega)
    i, j, v = sample_equilibrium(A, rng)
    return {"mine": mp[i].choice(), "opp": op[j].choice(), "value": v}
