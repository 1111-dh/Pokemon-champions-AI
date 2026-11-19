"""파티 추천 테스트: 선출 게임 풀이, 점수 구성 요소, (DB와 엔진이 있으면) 재현성과 제약."""

import numpy as np
import pytest

from pokechamp.teambuild.game import payoff_tensor, solve_batch, solve_exact, subsets
from pokechamp.teambuild.score import weighted_cvar


def test_solve_exact_rock_paper_scissors():
    P = np.array([[0.5, 0, 1], [1, 0.5, 0], [0, 1, 0.5]])
    v, x, y = solve_exact(P)
    assert v == pytest.approx(0.5, abs=1e-6)
    assert np.allclose(x, 1 / 3, atol=1e-6) and np.allclose(y, 1 / 3, atol=1e-6)


def test_solve_exact_dominant_row():
    P = np.array([[0.9, 0.8], [0.1, 0.2]])
    v, x, _ = solve_exact(P)
    assert v == pytest.approx(0.8) and x[0] == pytest.approx(1)


def test_batch_solver_close_to_exact():
    rng = np.random.default_rng(0)
    P = rng.random((8, 20, 20))
    approx, _, _ = solve_batch(P, 400)
    exact = np.array([solve_exact(P[i])[0] for i in range(8)])
    assert np.max(np.abs(approx - exact)) < 0.02


def test_subsets_mega_rules():
    rows, mega = [0, 1, 2, 3], [None, 10, 11, None]
    mine = subsets(rows, mega, limit_mega=True)
    assert len(mine) == 2          # 소지자 둘(1, 2번)이 함께 든 조합은 뺀다: {0,1,3}, {0,2,3}만
    for s in mine:
        assert sum(x >= 10 for x in s) == 1      # 소지자는 메가 폼으로 센다
    opp = subsets(rows, mega, limit_mega=False)
    assert len(opp) == 4
    both = [s for s in opp if {10, 2} <= set(s) or {1, 11} <= set(s)]
    assert all(10 in s and 2 in s for s in both)   # 상대는 첫 소지자만 메가


def test_payoff_assignment_rewards_coverage():
    # 내 상태 0,1,2 / 상대 상태 0,1,2. 0은 상대0만, 1은 상대1만, 2는 상대2만 이긴다.
    M = np.full((3, 3), 0.1)
    np.fill_diagonal(M, 0.9)
    P = payoff_tensor(M, np.array([[0, 1, 2]]), np.array([[[0, 1, 2]]]), assignment_weight=1.0)
    assert P.shape == (1, 1, 1) and P[0, 0, 0] == pytest.approx(0.9)
    P = payoff_tensor(M, np.array([[0, 1, 2]]), np.array([[[0, 1, 2]]]), assignment_weight=0.0)
    assert P[0, 0, 0] == pytest.approx((0.9 * 3 + 0.1 * 6) / 9)


def test_weighted_cvar():
    v = np.array([0.1, 0.5, 0.9, 0.3])
    w = np.array([0.05, 0.45, 0.45, 0.05])
    assert weighted_cvar(v, w, 0.1) == pytest.approx(0.2)    # 하위 10% = 0.1(0.05) + 0.3(0.05)


# ---------- 실제 DB + 엔진 (없으면 건너뜀) ----------

def _ready():
    try:
        from pokechamp.data.db import connect
        from pokechamp.engine_client import EngineClient
        conn = connect()
        if not conn.execute("SELECT COUNT(*) FROM set_archetypes").fetchone()[0]:
            return None
        EngineClient(timeout=2).health()
        return conn
    except Exception:  # noqa: BLE001
        return None


SMALL = {"threat_species": 15, "threat_archetypes": 2, "candidate_archetypes": 1, "opponent_teams": 16,
         "beam_width": 2, "prefilter": 6, "local_search_rounds": 1, "nash_iterations": 150}


@pytest.fixture(scope="module")
def real_conn():
    c = _ready()
    if c is None:
        pytest.skip("DB(세트 원형)나 엔진 서버가 없다")
    return c


def test_recommend_reproducible_and_legal(real_conn):
    from pokechamp.teambuild.search import Builder
    r1 = Builder(real_conn, SMALL, seed="t1").run()
    r2 = Builder(real_conn, SMALL, seed="t1").run()
    assert r1["team"] == r2["team"] and r1["score"] == r2["score"]          # 같은 입력, 같은 결과
    units = r1["_units"]
    assert len(units) == 6
    assert len({u.species for u in units}) == 6
    items = [u.item for u in units if u.item]
    assert len(items) == len(set(items))                                    # 도구 중복 없음
    assert sum(1 for u in units if u.mega_species) <= 2                     # 메가 후보 최대 2
    assert 0 <= r1["score"]["score"] <= 1
    assert sum(m["pick_rate"] for m in r1["members"]) == pytest.approx(3, abs=0.05)   # 선출 3마리


def test_recommend_fixed_and_excluded(real_conn):
    from pokechamp.teambuild.search import Builder
    r = Builder(real_conn, SMALL, seed="t2").run(fixed_species=["garchomp"], exclude=["primarina"])
    species = [u.species for u in r["_units"]]
    assert "garchomp" in species and "primarina" not in species
    locked = {"species": "Garchomp", "item": "Choice Scarf", "ability": "Rough Skin", "nature": "Jolly",
              "evs": {"hp": 2, "atk": 32, "spe": 32}, "moves": ["Earthquake", "Outrage", "Rock Slide", "Fire Fang"]}
    r = Builder(real_conn, SMALL, seed="t3").run(locked_sets=[locked])
    g = next(u for u in r["_units"] if u.species == "garchomp")
    assert g.locked and g.item == "choicescarf" and "choicescarf" not in [u.item for u in r["_units"] if u is not g]
