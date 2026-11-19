"""선출 추천 테스트: 선출 열거와 메가 제약, 더블 오라클, 상대 모델 혼합. (DB와 엔진이 있으면) 실제 추천."""

import numpy as np
import pytest

from pokechamp.select.picks import Pick, enumerate_picks, mega_slot
from pokechamp.select.solver import PreciseCells, calibrate, double_oracle, mix_with_model, opponent_model
from pokechamp.teambuild.game import solve_exact


def test_enumerate_picks_counts_and_choice():
    picks = enumerate_picks(6)
    assert len(picks) == 60
    assert len(set(picks)) == 60
    assert Pick(4, (1, 2)).choice() == "team 523"


def test_my_picks_have_at_most_one_mega_holder():
    picks = enumerate_picks(6, holders={0, 3}, max_mega=1)
    assert all(len(p.members & {0, 3}) <= 1 for p in picks)
    assert len(picks) == 60 - 4 * 3        # 0번과 3번이 함께 든 조합 4개 × 선봉 3가지를 뺀다
    assert mega_slot(Pick(2, (0, 5)), {0, 3}) == 0
    assert mega_slot(Pick(3, (0, 5)), {0, 3}) == 3     # 선봉이 소지자면 선봉이 메가진화


def test_enumerate_picks_falls_back_when_constraint_empties():
    picks = enumerate_picks(3, holders={0, 1, 2}, max_mega=1)
    assert len(picks) == 3


def _true_game(seed: int, n: int = 60):
    rng = np.random.default_rng(seed)
    a, b = rng.normal(size=n), rng.normal(size=n)
    T = 1 / (1 + np.exp(-(a[:, None] - b[None, :] + 0.6 * rng.normal(size=(n, n)))))
    A = 0.5 + 0.6 * (T - 0.5) + 0.05 * rng.normal(size=(n, n))   # 눈금이 다르고 잡음이 있는 근사
    return T, A


def _exact_evaluator(T, P):
    def evaluate(reqs):
        for cell, n in reqs:
            P.add(cell, T[cell] * n, n)
        return len(reqs)
    return evaluate


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_double_oracle_matches_full_game(seed):
    T, A = _true_game(seed)
    full, _, _ = solve_exact(T)
    P = PreciseCells()
    r = double_oracle(A, _exact_evaluator(T, P), P, initial=4, add_per_iter=2, eps=0.005, samples=1, max_samples=1)
    assert r.converged
    assert abs(r.value - full) < 0.02
    assert len(P) < 0.5 * T.size          # 칸의 절반도 정밀 계산하지 않는다
    assert r.candidate_precise_ratio() == 1.0
    assert r.x.sum() == pytest.approx(1) and r.y.sum() == pytest.approx(1)


def test_double_oracle_stops_on_budget():
    T, A = _true_game(3)
    P = PreciseCells()
    budget = {"left": 10}                      # 초기 후보 4×4칸도 다 못 채운다

    def evaluate(reqs):
        done = 0
        for cell, n in reqs:
            if budget["left"] <= 0:
                break
            P.add(cell, T[cell] * n, n)
            budget["left"] -= 1
            done += 1
        return done

    r = double_oracle(A, evaluate, P, initial=4, add_per_iter=2, samples=1)
    assert not r.converged
    assert len(P) == 10
    assert r.candidate_precise_ratio() < 1     # 못 채운 후보 칸은 보정한 근사값으로 풀었다
    assert r.x.sum() == pytest.approx(1)


def test_calibrate_fits_scale():
    T, A = _true_game(4)
    P = PreciseCells()
    for i in range(0, 60, 5):
        for j in range(0, 60, 7):
            P.add((i, j), T[i, j], 1)
    H, (a, b) = calibrate(A, P)
    assert b > 1.2                             # 근사는 눈금이 0.6배로 눌려 있다
    assert H[0, 0] == pytest.approx(T[0, 0])   # 정밀 칸은 정밀값
    mask = np.ones_like(T, dtype=bool)
    for c in P.games:
        mask[c] = False
    assert np.abs(H - T)[mask].mean() < np.abs(A - T)[mask].mean()


def test_opponent_model_and_mix():
    H = np.array([[0.8, 0.3], [0.4, 0.6]])
    q = opponent_model(H, [0, 1], np.array([0.5, 0.5]), temperature=5.0)
    assert q[1] > q[0]                         # 상대 입장 승률: 0번 열 0.4, 1번 열 0.55 → 1번 열을 더 고른다
    T, A = _true_game(5, 10)
    P = PreciseCells()
    r = double_oracle(A, _exact_evaluator(T, P), P, initial=3, samples=1, max_samples=1)
    q = np.full(10, 0.1)
    s, br = mix_with_model(r, q, 0.3)
    assert s.sum() == pytest.approx(1)
    assert s[br] >= 0.3 - 1e-9
    assert br in r.rows


# ---------- DB와 엔진이 있을 때: 실제 추천 ----------

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


@pytest.fixture(scope="module")
def real_conn():
    c = _ready()
    if c is None:
        pytest.skip("DB(세트 원형)나 엔진 서버가 없다")
    return c


TEAM = [  # 메가스톤 소지자 둘(메가갑주무사, 메가마폭시)
    {"species": "Golisopod", "item": "Golisopite", "ability": "Emergency Exit", "nature": "Adamant",
     "evs": {"hp": 32, "atk": 32, "spd": 2}, "moves": ["First Impression", "Iron Head", "Close Combat", "Sucker Punch"]},
    {"species": "Tauros-Paldea-Blaze", "item": "Choice Scarf", "ability": "Intimidate", "nature": "Adamant",
     "evs": {"hp": 31, "atk": 32, "def": 3}, "moves": ["Close Combat", "Earthquake", "Raging Bull", "Rock Slide"]},
    {"species": "Primarina", "item": "Sitrus Berry", "ability": "Torrent", "nature": "Modest",
     "evs": {"hp": 32, "def": 20, "spa": 14}, "moves": ["Aqua Jet", "Flip Turn", "Hydro Pump", "Moonblast"]},
    {"species": "Rillaboom", "item": "Leftovers", "ability": "Grassy Surge", "nature": "Adamant",
     "evs": {"hp": 32, "atk": 32, "spe": 2}, "moves": ["Drum Beating", "Grassy Glide", "High Horsepower", "U-turn"]},
    {"species": "Goodra-Hisui", "item": "Air Balloon", "ability": "Gooey", "nature": "Modest",
     "evs": {"hp": 32, "def": 2, "spa": 32}, "moves": ["Flamethrower", "Flash Cannon", "Ice Beam", "Thunderbolt"]},
    {"species": "Delphox", "item": "Delphoxite", "ability": "Blaze", "nature": "Timid",
     "evs": {"hp": 2, "spa": 32, "spe": 32}, "moves": ["Flamethrower", "Nasty Plot", "Psychic", "Substitute"]},
]
OPP = ["garchomp", "primarina", "hippowdon", "archaludon", "mimikyu", "charizard"]


def test_select_reproducible_and_mega_limited(real_conn):
    from pokechamp.select.recommend import Selector
    cfg = {"opponent_samples": 8, "precise_samples": 4}
    r1 = Selector(real_conn, cfg=cfg).run(TEAM, OPP, seed="t1", max_games=160)
    r2 = Selector(real_conn, cfg=cfg).run(TEAM, OPP, seed="t1", max_games=160)
    assert [(p["choice"], p["probability"]) for p in r1["picks"]] == [(p["choice"], p["probability"]) for p in r2["picks"]]
    assert r1["games"] <= 160
    assert sum(p["probability"] for p in r1["picks"]) == pytest.approx(1, abs=0.03)
    for p in r1["picks"]:
        holders = [s for s in p["slots"] if s in (1, 6)]
        assert len(holders) <= 1                     # 내 선출에 메가스톤 소지자 최대 1마리
        assert p["choice"] in r1["matrix"]["rows"]  # 추천한 선출은 정밀 계산한 후보 행이다
        assert p["precise_share"] > 0
        assert 0 <= p["win_vs_equilibrium"] <= 1
    assert r1["matrix"]["size"] == [48, 60]


def test_parse_opponents_handles_korean_and_mega(real_conn):
    from pokechamp.select.recommend import SelectError, parse_opponents
    ids, notes = parse_opponents(real_conn, "한카리아스, 누리레느, 하마돈, 브리두라스, 따라큐, 메가리자몽Y")
    assert ids == OPP
    assert notes and "메가 폼" in notes[0]
    with pytest.raises(SelectError):
        parse_opponents(real_conn, "한카리아스, 없는포켓몬")


class _FakeCache:
    """세트 이름 첫 글자로 정한 가짜 1:1 점수 (A가 B보다 알파벳이 앞이면 유리)."""

    def scores(self, pairs):
        out = []
        for (a, ma), (b, mb) in pairs:
            sa, sb = a["species"], b["species"]
            out.append(0.9 if sa < sb else 0.1 if sa > sb else 0.5)
        return out


def test_quick_game_and_equilibrium_choices():
    import random
    from pokechamp.select.quick import equilibrium_choices, quick_game
    mine = [{"species": s, "item": "", "moves": []} for s in "ABCXYZ"]
    opp = [{"species": s, "item": "", "moves": []} for s in "DEFGHI"]
    A, mp, op = quick_game(_FakeCache(), mine, opp, lambda st: None, 0.6, 0.2)
    assert A.shape == (60, 60) and len(mp) == len(op) == 60
    best = mp[int(A.mean(axis=1).argmax())]
    assert best.members == frozenset({0, 1, 2})             # A·B·C가 상대 모두에게 유리
    c = equilibrium_choices(_FakeCache(), mine, opp, lambda st: None, random.Random(1), 0.6, 0.2)
    assert set(c["mine"][5:]) == {"1", "2", "3"} and c["value"] > 0.8
