"""배틀 입력 파서와 상태 추적 테스트. (DB가 있으면) 파서, (엔진도 있으면) 엔진 배틀로 자동 검증."""

import pytest

from pokechamp.battle.events import Event
from pokechamp.battle.state import BattleState
from pokechamp.input.battle_text import split_segments


def test_split_segments_keeps_hp_and_parenthesized_names():
    assert split_segments("상대 지진/내 HP 120/181 / 상대 HP 40%") == ["상대 지진", "내 HP 120/181", "상대 HP 40%"]
    assert split_segments("내 교체 켄타로스 (팔데아의 모습/워터종)\n상대 선공") == ["내 교체 켄타로스 (팔데아의 모습/워터종)", "상대 선공"]


TEAM = [{"species": "Tyranitar", "item": "Smooth Rock", "ability": "Sand Stream", "moves": ["Stone Edge", "Crunch"]},
        {"species": "Garchomp", "item": "Choice Scarf", "ability": "Rough Skin", "moves": ["Earthquake"]},
        {"species": "Primarina", "item": "", "ability": "Torrent", "moves": ["Moonblast"]}]


def test_state_weather_clock_and_boosts():
    s = BattleState(TEAM, ["charizard", "lucario", "gholdengo"])
    s.apply([Event("switch", "me", species="tyranitar"), Event("switch", "opp", species="lucario"),
             Event("ability", "me", species="tyranitar", ability="sandstream"), Event("weather", value="sand")])
    assert s.weather.kind == "sand" and s.weather.remaining(1) == (8, 8)      # 보송송바위 → 8턴 (선봉이라 턴 1부터)
    s.apply([Event("turn", amount=1), Event("move", "opp", move="swordsdance"), Event("boost", "opp", stat="atk", amount=2)])
    assert s.opp.mons["lucario"].boosts["atk"] == 2
    w = s.apply([Event("switch", "opp", species="gholdengo"), Event("move", "me", move="earthquake")])
    assert s.opp.mons["lucario"].boosts["atk"] == 0                           # 교체하면 랭크가 풀린다
    assert any("없는 기술" in x for x in w)                                    # 마기라스는 지진이 없다


def test_state_opponent_weather_unknown_rock_and_mega():
    s = BattleState(TEAM, ["ninetales", "charizard", "lucario"])
    s.apply([Event("switch", "opp", species="ninetales"), Event("ability", "opp", species="ninetales", ability="drought"),
             Event("weather", value="sun")])
    assert s.weather.remaining(1) == (5, 8)                                   # 상대 도구를 모르면 5~8턴
    s.apply([Event("turn", amount=1), Event("switch", "opp", species="charizard"), Event("mega", "opp")])
    w = s.apply([Event("switch", "opp", species="lucario"), Event("mega", "opp")])
    assert s.opp.mega_used and any("이미 메가진화" in x for x in w)


def _conn():
    try:
        from pokechamp.data.db import connect
        conn = connect()
        if not conn.execute("SELECT COUNT(*) FROM species").fetchone()[0]:
            return None
        return conn
    except Exception:  # noqa: BLE001
        return None


@pytest.fixture(scope="module")
def bt():
    c = _conn()
    if c is None:
        pytest.skip("DB(정적 데이터)가 없다")
    from pokechamp.input.battle_text import BattleText
    return BattleText(c)


def test_parse_examples(bt):
    r = bt.parse("상대 선공 / 상대 지진 / 상대 HP 40% / 내 HP 55%")
    assert r.ok
    assert [(e.kind, e.side) for e in r.events] == [("first", "opp"), ("move", "opp"), ("hp", "opp"), ("hp", "me")]
    assert r.events[1].move == "earthquake" and r.events[2].hp == 40
    r = bt.parse("상대 메가진화 / 상대 용성군 / 내 누리레느 기절 / 상대 한카리아스로 교체 / 날씨 모래바람")
    assert r.ok and r.events[2].species == "primarina" and r.events[3].kind == "switch" and r.events[4].value == "sand"
    r = bt.parse("내 HP 120/181 / 상대 공격 +2 / 상대 화상 / 상대 도구 자뭉열매 소모 / 상대 특성 위협 / 상대 기합의띠 발동")
    assert r.ok
    assert r.events[0].hp_abs == 120 and r.events[1].amount == 2 and r.events[2].status == "brn"
    assert r.events[3].value == "consumed" and r.events[4].ability == "intimidate" and r.events[5].item == "focussash"


def test_parse_toxic_move_vs_status_and_errors(bt):
    r = bt.parse("상대 맹독 / 내 맹독 상태 / 상대 메가리자몽Y 화염방사")
    assert r.ok
    assert (r.events[0].kind, r.events[0].move) == ("move", "toxic")      # 맨 단어 '맹독'은 기술
    assert (r.events[1].kind, r.events[1].status) == ("status", "tox")    # '상태'를 붙이면 상태이상
    assert r.events[2].species == "charizard"                             # 메가 폼 이름은 기본 폼으로
    r = bt.parse("상대 지진진 / 상대 HP 40%")
    assert len(r.errors) == 1 and len(r.events) == 1


def test_render_roundtrip(bt):
    text = "상대 한카리아스 지진 / 내 누리레느 HP 52/170 / 상대 한카리아스 공격 -1 / 필드 그래스필드 / 내 누리레느 맹독 상태"
    r = bt.parse(text)
    assert r.ok and bt.render(r.events) == text


def test_selfcheck_engine_battles():
    c = _conn()
    if c is None:
        pytest.skip("DB가 없다")
    from pokechamp.engine_client import EngineClient
    try:
        EngineClient(timeout=2).health()
    except Exception:  # noqa: BLE001
        pytest.skip("엔진 서버가 없다")
    from pokechamp.battle.selfcheck import run
    for mode in ("full", "sparse"):
        st = run(c, EngineClient(), 8, "pytest", mode=mode)["stats"]
        assert st.get("parse_error", 0) == 0 and st.get("roundtrip", 0) == 0
        assert sum(v for k, v in st.items() if k.startswith("state_")) == 0, st
