"""실전 필드 조건: 벽·순풍·트릭룸·설치기 (추적, 규칙, 로그 변환)."""

from pokechamp.battle.events import Event
from pokechamp.battle.state import BattleState


def _state(my_item: str = "", **kw) -> BattleState:
    st = BattleState([{"species": "garchomp", "item": my_item, "moves": ["earthquake"]},
                      {"species": "dragonite", "moves": ["extremespeed"]}], ["corviknight", "aggron"], **kw)
    st.apply([Event("switch", "me", species="garchomp"), Event("switch", "opp", species="corviknight"), Event("turn")])
    return st


def _turns(st: BattleState, n: int) -> None:
    for _ in range(n):
        st.apply([Event("turn")])


def test_my_reflect_five_turns_light_clay_eight():
    st = _state()
    st.apply([Event("sidestart", "me", value="reflect")])
    assert st.me.timed["reflect"].remaining(st.turn) == (5, 5)
    _turns(st, 4)
    assert "reflect" in st.me.timed
    _turns(st, 1)                                   # 턴 6 시작: 턴 1~5 적용 뒤 끝
    assert "reflect" not in st.me.timed
    clay = _state("lightclay")
    clay.apply([Event("sidestart", "me", value="reflect")])
    assert clay.me.timed["reflect"].remaining(clay.turn) == (8, 8)


def test_opp_screen_range_and_expiry():
    st = _state()
    st.apply([Event("sidestart", "opp", value="lightscreen")])
    assert st.opp.timed["lightscreen"].remaining(st.turn) == (5, 8)
    _turns(st, 5)
    assert st.opp.timed["lightscreen"].remaining(st.turn) == (0, 3)
    _turns(st, 3)
    assert "lightscreen" not in st.opp.timed


def test_same_screen_twice_ignored():
    st = _state()
    st.apply([Event("sidestart", "me", value="reflect")])
    _turns(st, 2)
    st.apply([Event("sidestart", "me", value="reflect")])
    assert st.me.timed["reflect"].remaining(st.turn) == (3, 3)


def test_hazard_layers_capped():
    st = _state()
    st.apply([Event("sidestart", "opp", value="spikes")] * 4 + [Event("sidestart", "opp", value="toxicspikes")] * 3
             + [Event("sidestart", "opp", value="stealthrock")] * 2)
    assert st.opp.hazards == {"spikes": 3, "toxicspikes": 2, "stealthrock": 1}
    st.apply([Event("sideend", "opp", value="spikes")])
    assert "spikes" not in st.opp.hazards


def test_manual_turns_amount():
    st = _state()
    st.apply([Event("sidestart", "opp", value="tailwind", amount=2), Event("sidestart", "me", value="spikes", amount=2)])
    assert st.opp.timed["tailwind"].remaining(st.turn) == (2, 2) and st.me.hazards["spikes"] == 2


def test_trickroom_clock_and_end():
    st = _state()
    st.apply([Event("room", None, value="trickroom")])
    assert st.trickroom.remaining(st.turn) == (5, 5)
    st.apply([Event("room", None, value="none")])
    assert st.trickroom.kind == "none"


def test_courtchange_swaps_sides():
    st = _state()
    st.apply([Event("sidestart", "me", value="reflect"), Event("sidestart", "opp", value="spikes"),
              Event("sidestart", "opp", value="spikes"), Event("courtchange", None)])
    assert "reflect" in st.opp.timed and st.me.hazards == {"spikes": 2} and not st.opp.hazards and not st.me.timed


def _mv(side: str, move: str) -> Event:
    return Event("move", side, move=move)


def test_move_input_sets_conditions():
    st = _state()
    st.apply([_mv("me", "reflect"), _mv("opp", "stealthrock"), _mv("opp", "spikes"), _mv("me", "tailwind")])
    assert set(st.me.timed) == {"reflect", "tailwind"} and st.me.hazards == {"stealthrock": 1, "spikes": 1}


def test_removal_moves():
    st = _state()
    st.apply([_mv("opp", "reflect"), _mv("opp", "lightscreen"), _mv("me", "brickbreak")])
    assert not st.opp.timed
    st.apply([_mv("opp", "stealthrock"), _mv("me", "rapidspin")])
    assert not st.me.hazards
    st.apply([_mv("opp", "reflect"), _mv("me", "spikes"), _mv("opp", "stealthrock"), _mv("me", "defog")])
    assert not st.opp.timed and not st.opp.hazards and not st.me.hazards
    st.apply([_mv("me", "spikes"), _mv("opp", "spikes"), _mv("opp", "tidyup")])
    assert not st.opp.hazards and not st.me.hazards


def test_auroraveil_needs_snow():
    st = _state()
    st.apply([_mv("me", "auroraveil")])
    assert "auroraveil" not in st.me.timed
    st.apply([Event("weather", None, value="snow"), _mv("me", "auroraveil")])
    assert "auroraveil" in st.me.timed


def test_trickroom_reuse_ends():
    st = _state()
    st.apply([_mv("opp", "trickroom")])
    assert st.trickroom.kind == "trickroom"
    st.apply([Event("turn"), _mv("me", "trickroom")])
    assert st.trickroom.kind == "none"


def test_engine_log_path_does_not_derive():
    st = _state(derive_field=False)
    st.apply([_mv("me", "reflect"), _mv("opp", "trickroom")])
    assert not st.me.timed and st.trickroom.kind == "none"


def test_log_converter_field_lines():
    from pokechamp.battle.events import LogConverter
    conv = LogConverter(lambda s: s.lower(), me="p1")
    ev = conv.feed(["|-sidestart|p1: Garchomp|Reflect", "|-sidestart|p2|move: Stealth Rock", "|-sideend|p2: Corviknight|Spikes",
                    "|-fieldstart|move: Trick Room|[of] p2a: Bronzong", "|-fieldend|move: Trick Room",
                    "|-swapsideconditions", "|-sidestart|p2: Corviknight|move: Light Screen"])
    got = [(e.kind, e.side, e.value) for e in ev]
    assert got == [("sidestart", "me", "reflect"), ("sidestart", "opp", "stealthrock"), ("sideend", "opp", "spikes"),
                   ("room", None, "trickroom"), ("room", None, "none"), ("courtchange", None, ""),
                   ("sidestart", "opp", "lightscreen")]


# ---------- 탐색·믿음·예상 HP (DB와 엔진이 있을 때) ----------
import pytest  # noqa: E402


@pytest.fixture(scope="module")
def sess_factory():
    try:
        from pokechamp.battle.session import BattleSession
        from pokechamp.data.db import connect
        from pokechamp.engine_client import EngineClient
        conn = connect()
        client = EngineClient(timeout=2)
        client.health()
    except Exception:  # noqa: BLE001
        pytest.skip("DB나 엔진 서버가 없다")
    mine = [{"species": "Garchomp", "item": "Choice Scarf", "ability": "Rough Skin", "nature": "Jolly",
             "evs": {"atk": 32, "spe": 32}, "moves": ["Earthquake", "Dragon Claw"]},
            {"species": "Dragonite", "item": "Leftovers", "ability": "Multiscale", "nature": "Adamant",
             "evs": {"atk": 32, "hp": 32}, "moves": ["Extreme Speed", "Earthquake"]}]

    def make():
        s = BattleSession(conn, mine, ["corviknight", "aggron", "garchomp"], EngineClient(), seed="field")
        s.feed([Event("switch", "me", species="garchomp"), Event("switch", "opp", species="corviknight"), Event("turn")])
        return s
    return make


def test_determinize_passes_field_conditions(sess_factory):
    from pokechamp.battle.search import Searcher, SearchConfig
    s = sess_factory()
    s.feed([Event("sidestart", "me", value="reflect"), Event("sidestart", "opp", value="spikes"),
            Event("sidestart", "opp", value="spikes"), Event("room", None, value="trickroom")])
    req = Searcher(s, ["garchomp", "dragonite"], SearchConfig(seed="f", samples=1)).determinize(0)
    assert req["sideConditions"] == {"p1": {"reflect": 5}, "p2": {"spikes": 2}} and req["trickRoom"] == 5


def _alive(mb) -> float:
    return sum(p.w for p in mb.particles)


def test_order_evidence_under_trick_room(sess_factory):
    s = sess_factory()
    # 상대(아머까오)가 내 300보다 느린데 먼저 움직였다: 트릭룸이면 맞는 관찰, 아니면 모순
    s.belief.observe_order("corviknight", opp_first=True, my_speed=300, mega=False, boosts=0, status="", weather="none",
                           trick_room=True)
    assert s.belief.contradictions == [] and _alive(s.belief.mon("corviknight")) > 0.99


def test_order_evidence_tailwind(sess_factory):
    s = sess_factory()
    mb = s.belief.mon("corviknight")
    spe = max(s.belief.speed_of(p, False, 0, "", "none") or 0 for p in mb.particles)
    s.belief.observe_order("corviknight", opp_first=True, my_speed=spe * 1.5, mega=False, boosts=0, status="",
                           weather="none", opp_tailwind=True)
    assert s.belief.contradictions == []


def test_damage_evidence_uses_screens(sess_factory):
    s = sess_factory()
    s.feed([Event("sidestart", "opp", value="reflect"), Event("move", "me", move="earthquake")])
    assert s.pending and s.pending["field"].get("screens") == ["reflect"]


def test_hazard_estimated_opp_hp_skips_damage_evidence(sess_factory):
    s = sess_factory()
    s.feed([Event("hp", "opp", hp=88, note="hazard"), Event("move", "me", move="earthquake")])
    assert s.pending is None                       # 추정 HP에서 시작한 데미지는 증거로 쓰지 않는다
    s.feed([Event("hp", "opp", hp=60), Event("move", "me", move="earthquake")])
    assert s.pending is not None                   # 관찰한 HP가 들어오면 다시 쓴다


def test_possibly_ended_opp_screen_is_not_assumed(sess_factory):
    from pokechamp.battle.search import Searcher, SearchConfig
    s = sess_factory()
    s.feed([Event("sidestart", "opp", value="reflect")] + [Event("turn")] * 5)     # 빛의점토를 모르면 5~8턴: 이제 0~3
    assert s.state.opp.timed["reflect"].remaining(s.state.turn)[0] == 0
    assert "screens" not in s._field("opp")
    s.feed([Event("move", "me", move="earthquake")])
    assert s.pending is None                       # 벽이 있었는지 모르니 데미지 증거로 쓰지 않는다
    req = Searcher(s, ["garchomp", "dragonite"], SearchConfig(seed="f", samples=1)).determinize(0)
    assert "reflect" not in req.get("sideConditions", {}).get("p2", {})


def test_attacking_hazard_setters_and_defog_terrain():
    st = _state()
    st.apply([_mv("opp", "ceaselessedge"), _mv("opp", "stoneaxe")])
    assert st.me.hazards == {"spikes": 1, "stealthrock": 1}
    st.apply([Event("terrain", None, value="grassy"), _mv("me", "defog")])
    assert st.terrain.kind == "none" and not st.me.hazards
