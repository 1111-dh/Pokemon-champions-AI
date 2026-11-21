"""복기 테스트 (DB와 엔진이 있을 때): 대본 배틀을 저장하고 턴별 승률 그래프를 만든다 (단계 6 인수 기준)."""

import xml.dom.minidom

import pytest


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


def test_review_builds_turn_win_rate_chart(conn, tmp_path):
    from pokechamp.battle.review import review, text_report
    from pokechamp.battle.scripted import scripted_battle
    from pokechamp.battle.search import SearchConfig
    from pokechamp.battle.store import list_battles
    from pokechamp.engine_client import EngineClient
    rec = scripted_battle(conn, EngineClient(), "pytest-review", log_dir=tmp_path)
    bid = rec["battle_id"]
    try:
        assert any(r["id"] == bid and r["source"] == "sim" for r in list_battles(conn))
        r = review(conn, bid, cfg=SearchConfig(seed="t", samples=2, model_reps=2))
        assert len(r["points"]) == len(rec["turns"])
        assert all(0 <= p["win"] <= 1 for p in r["points"] if "win" in p)
        assert r["final"] == (1.0 if rec["result"] == "win" else 0.0)
        doc = xml.dom.minidom.parseString(r["svg"])
        assert doc.getElementsByTagName("path")                       # 승률 선
        assert "실수 후보 턴" in text_report(r, conn)
    finally:
        with conn:
            conn.execute("DELETE FROM my_battles WHERE id=?", (bid,))
