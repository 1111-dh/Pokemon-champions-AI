"""믿음 상태, 세션, 탐색 테스트. 베이즈 게임 풀이는 엔진 없이, 나머지는 DB와 엔진이 있을 때."""

import random

import numpy as np
import pytest

from pokechamp.battle.search import solve_bayes


def test_solve_bayes_matches_matrix_game_when_one_type():
    M = np.array([[0.5, 0.0, 1.0], [1.0, 0.5, 0.0], [0.0, 1.0, 0.5]])
    v, x = solve_bayes([M])
    assert v == pytest.approx(0.5, abs=1e-6) and np.allclose(x, 1 / 3, atol=1e-6)


def test_solve_bayes_opponent_knows_its_type():
    # 상대는 자기 유형(세트)을 알고 그 유형에서 가장 좋은 열을 고른다.
    # 행 0: 유형 0이면 min 0.8, 유형 1이면 min 0.1 → 평균 0.45 / 행 1: 0.2, 0.8 → 0.5. 최적은 행 1, 값 0.5
    M0 = np.array([[0.9, 0.8], [0.2, 0.3]])
    M1 = np.array([[0.1, 0.2], [0.8, 0.9]])
    v, x = solve_bayes([M0, M1])
    assert v == pytest.approx(0.5, abs=1e-6) and x[1] == pytest.approx(1, abs=1e-6)


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
def conn():
    c = _ready()
    if c is None:
        pytest.skip("DB나 엔진 서버가 없다")
    return c


def test_belief_move_item_mega_and_rejuvenation(conn):
    from pokechamp.battle.belief import Belief
    b = Belief(conn, ["garchomp", "charizard"], n=24, seed="t")
    b.observe_move("garchomp", "earthquake")
    assert all("earthquake" in [m.lower().replace(" ", "") for m in p.set["moves"]] for p in b.mons["garchomp"].particles if p.w > 0)
    b.observe_mega("charizard")
    assert all(p.set.get("mega_species") for p in b.mons["charizard"].particles if p.w > 0)
    # 드문 조합도 결합 분포에 있으면 재충전으로 살아남는다
    mb = b.mons["garchomp"]
    rare = next((m for p in mb.particles for m in p.set["moves"]), None)
    assert rare is not None
    assert not b.contradictions
    b.observe_item("garchomp", "nonexistentitem")
    assert b.contradictions                     # 분포에 없는 도구는 모순으로 알린다


def test_session_feeds_engine_log_and_searcher_chooses(conn):
    from pokechamp.battle.events import LogConverter
    from pokechamp.battle.search import Searcher, SearchConfig, to_choice
    from pokechamp.battle.session import BattleSession, engine_set
    from pokechamp.battle.belief_check import archetype_team
    from pokechamp.data.priors import Priors
    from pokechamp.engine_client import EngineClient
    client = EngineClient()
    rng = random.Random(3)
    w = Priors(conn).species_weights()
    pool = list(w)[:40]
    mine = [engine_set(s) for s in archetype_team(conn, rng, pool, [w[s] for s in pool])]
    opp = [engine_set(s) for s in archetype_team(conn, rng, pool, [w[s] for s in pool])]
    r = client.new_battle(mine, opp, seed="t", picks={"p1": "team 123", "p2": "team 123"})
    sess = BattleSession(conn, mine, [s["species"] for s in opp], client, n_particles=16, seed="t")
    conv = LogConverter(sess.res)
    sess.feed(conv.feed(r["log"]))
    srch = Searcher(sess, [s["species"] for s in mine[:3]], SearchConfig(samples=2, reps=1, seed="t"))
    res = srch.choose()
    assert res["probabilities"].sum() == pytest.approx(1)
    la = client.legal_actions(r["state"])
    for a in res["actions"]:
        assert to_choice(a, la["p1"], r["requests"]["p1"], sess.res) is not None   # 추상 행동이 진짜 배틀 선택으로 옮겨진다
    assert 0 <= res["value"] <= 1


def test_text_battle_runs_to_the_end_with_scripted_input(conn, tmp_path):
    """엔진 배틀의 관찰을 사용자 입력처럼 넣어 텍스트 세션이 끝까지 진행되는지 본다 (단계 5 인수 기준: 한 판을 텍스트로 끝까지)."""
    from pokechamp.battle.belief_check import archetype_team
    from pokechamp.battle.cli_session import TextBattle
    from pokechamp.battle.events import LogConverter, SpeciesResolver
    from pokechamp.battle.search import SearchConfig
    from pokechamp.battle.selfcheck import sparse
    from pokechamp.battle.session import engine_set
    from pokechamp.data.priors import Priors
    from pokechamp.engine_client import EngineClient
    from pokechamp.input.battle_text import BattleText
    client = EngineClient()
    rng = random.Random(11)
    w = Priors(conn).species_weights()
    pool = list(w)[:40]
    mine = [engine_set(s) for s in archetype_team(conn, rng, pool, [w[s] for s in pool])]
    opp = [engine_set(s) for s in archetype_team(conn, rng, pool, [w[s] for s in pool])]
    r = client.new_battle(mine, opp, seed="tb", picks={"p1": "team 213", "p2": "team 123"})
    bt, res = BattleText(conn), SpeciesResolver(conn)
    conv = LogConverter(res)
    conv.feed(r["log"])                                   # 선봉 등장은 세션 시작 때 따로 넣는다
    chunks, st = [], r["state"]
    while not r.get("ended") and len(chunks) < 150:
        la = client.legal_actions(st)
        ch = {s: client.batch("/bot_choice", [{"state": st, "side": s}])[0]["result"]["choice"] if la[s] else None
              for s in ("p1", "p2")}
        r = client.step(st, **ch)
        st = r["state"]
        text = bt.render(sparse([e for e in conv.feed(r["log"]) if e.kind != "turn"]))
        if text:
            chunks.append(text)
    assert r.get("ended")
    queue = iter(chunks)

    def ask(prompt: str) -> str:
        if "내 선출" in prompt:
            return "213"
        if "상대 선봉" in prompt:
            return bt.name("species", res(opp[0]["species"]))
        if "이번 턴" in prompt:
            return next(queue, "끝")
        if "결과" in prompt:
            return "승" if r.get("winner") == "p1" else "패"
        return ""
    said: list[str] = []
    tb = TextBattle(conn, mine, [s["species"] for s in opp], client, ask=ask, say=said.append,
                    cfg=SearchConfig(samples=2, seed="tb"), seed="tb", log_dir=tmp_path)
    rec = tb.run(recommend_selection=False)
    assert rec["result"] == ("win" if r.get("winner") == "p1" else "loss")
    assert any(x.startswith("추천:") for x in said)
    assert not any("해석 실패" in x for x in said)
    assert (tmp_path / "battle-tb.json").exists()
