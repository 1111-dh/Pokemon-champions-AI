"""웹 실전 세션 테스트 (DB와 엔진이 있을 때): 선출 추천 → 선출 → 엔진 배틀의 관찰을 입력처럼 넣어 끝까지 → 저장."""

import random

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


def test_live_battle_full_flow(conn):
    from pokechamp.battle.belief_check import archetype_team
    from pokechamp.battle.events import LogConverter, SpeciesResolver
    from pokechamp.battle.live import LiveBattle
    from pokechamp.battle.search import SearchConfig
    from pokechamp.battle.selfcheck import sparse
    from pokechamp.battle.session import engine_set
    from pokechamp.data.priors import Priors
    from pokechamp.engine_client import EngineClient
    from pokechamp.input.battle_text import BattleText
    client = EngineClient()
    rng = random.Random(21)
    w = Priors(conn).species_weights()
    pool = list(w)[:40]
    mine = [engine_set(s) for s in archetype_team(conn, rng, pool, [w[s] for s in pool])]
    opp = [engine_set(s) for s in archetype_team(conn, rng, pool, [w[s] for s in pool])]
    bt, res = BattleText(conn), SpeciesResolver(conn)
    lb = LiveBattle(conn, client, mine, [res(s["species"]) for s in opp], cfg=SearchConfig(seed="t", samples=2, model_reps=2))
    sel = lb.selection(budget_s=3)
    assert sel["picks"] and abs(sum(p["probability"] for p in sel["picks"]) - 1) < 0.05
    r = client.new_battle(mine, opp, seed="live", picks={"p1": "team 213", "p2": "team 123"})
    conv = LogConverter(res)
    conv.feed(r["log"])
    v = lb.start([2, 1, 3], bt.name("species", res(opp[0]["species"])))
    assert v["recommendation"] and v["mine"][0]["active"]
    st, steps = r["state"], 0
    while not r.get("ended") and steps < 150:
        la = client.legal_actions(st)
        ch = {s: client.batch("/bot_choice", [{"state": st, "side": s}])[0]["result"]["choice"] if la[s] else None
              for s in ("p1", "p2")}
        r = client.step(st, **ch)
        st = r["state"]
        text = bt.render(sparse([e for e in conv.feed(r["log"]) if e.kind != "turn"]))
        steps += 1
        if not text:
            continue
        assert lb.preview(text)["ok"]
        v = lb.apply(text)
    assert r.get("ended") and v["recommendation"] is None
    assert lb.explain()["note"]
    bid = lb.end("win" if r.get("winner") == "p1" else "loss")["battle_id"]
    with conn:
        conn.execute("DELETE FROM my_battles WHERE id=?", (bid,))


def test_live_form_turn_replace_and_abort(conn):
    from pokechamp.battle.live import LiveBattle
    from pokechamp.battle.search import SearchConfig
    from pokechamp.engine_client import EngineClient
    team = [
        {"species": "Garchomp", "item": "Choice Scarf", "ability": "Rough Skin", "nature": "Jolly", "evs": {"atk": 32, "spe": 32},
         "moves": ["Earthquake", "Outrage", "Rock Slide", "Fire Fang"]},
        {"species": "Primarina", "item": "Sitrus Berry", "ability": "Torrent", "nature": "Modest", "evs": {"hp": 32, "spa": 32},
         "moves": ["Moonblast", "Surf", "Ice Beam", "Aqua Jet"]},
        {"species": "Charizard", "item": "Charizardite Y", "ability": "Blaze", "nature": "Timid", "evs": {"spa": 32, "spe": 32},
         "moves": ["Flamethrower", "Solar Beam", "Air Slash", "Roost"]},
    ]
    lb = LiveBattle(conn, EngineClient(), team, ["garchomp", "primarina", "hippowdon", "archaludon", "mimikyu", "lucario"],
                    cfg=SearchConfig(seed="t", samples=2, model_reps=2))
    v = lb.start([1, 2, 3], "루카리오")
    f = v["form"]
    assert [m["name"] for m in f["my_moves"]][0] == "지진" and f["opp_bench"]
    assert v["recommendation"]["options"][0]["action"]["kind"] in ("move", "switch")
    est = lb.estimate({"kind": "move", "move": "earthquake"}, {"kind": "move", "move": f["opp_moves"][0]["id"]})
    assert est["opp_hp"] is not None and est["my_hp"] is not None
    # 칼춤 같은 확정 효과는 자동으로 들어가고, HP 0이면 기절 → 교체 단계
    v = lb.submit_turn({"first": "me", "me": {"kind": "move", "move": "earthquake"}, "opp": {"kind": "move", "move": "swordsdance"},
                        "my_hp": f["my_hp"], "opp_hp": 0})
    assert v["phase"] == "replace" and v["form"]["need_replace"]["opp"]
    with pytest.raises(ValueError):
        lb.submit_turn({"first": "me", "me": {"kind": "none"}, "opp": {"kind": "none"}})   # 교체를 먼저 넣어야 한다
    v = lb.submit_replace(None, "hippowdon")
    assert v["phase"] == "move" and v["turn"] == 2 and any(m["active"] and m["species"] == "hippowdon" for m in v["opp"])
    v = lb.submit_turn({"first": "opp", "me": {"kind": "move", "move": "outrage"}, "opp": {"kind": "move", "move": "지진"},
                        "my_hp": 60, "opp_hp": 55})
    assert v["turn"] == 3 and "상대 지진" in v["history"][-1]
    assert lb.end("aborted", save=False)["battle_id"] is None                          # 기록 없이 중단


def test_team_and_battle_delete_api(conn):
    from pokechamp.battle.store import list_battles
    from pokechamp.teams.store import list_teams, save_team
    from pokechamp.web.server import App
    a = App()
    save_team(conn, "_pytest_삭제팀", [{"species": "Garchomp", "item": "", "ability": "", "nature": "", "evs": {}, "moves": []}],
              replace=True)
    assert a.team_show({"name": "_pytest_삭제팀"})["members"][0]["name"] == "한카리아스"
    a.team_delete({"name": "_pytest_삭제팀"})
    assert not any(t["name"] == "_pytest_삭제팀" for t in list_teams(conn))
    with pytest.raises(ValueError):
        a.team_delete({"name": "_pytest_삭제팀"})
    with conn:
        bid = conn.execute("INSERT INTO my_battles (played_at, source, my_team_json, opp_team_json, log_json) VALUES "
                           "('2026-01-01', 'sim', '[]', '[]', '{}')").lastrowid
    assert a.battle_delete({"id": bid})["deleted"]
    assert not any(r["id"] == bid for r in list_battles(conn))


def test_live_entry_abilities_and_air_balloon(conn):
    from pokechamp.battle.live import LiveBattle
    from pokechamp.battle.search import SearchConfig
    from pokechamp.engine_client import EngineClient
    team = [
        {"species": "Incineroar", "item": "Sitrus Berry", "ability": "Intimidate", "nature": "Careful", "evs": {"hp": 32, "spd": 32},
         "moves": ["Flare Blitz", "Knock Off", "Fake Out", "Parting Shot"]},
        {"species": "Rotom-Wash", "item": "Air Balloon", "ability": "Levitate", "nature": "Modest", "evs": {"hp": 32, "spa": 32},
         "moves": ["Hydro Pump", "Volt Switch", "Will-O-Wisp", "Protect"]},
        {"species": "Garchomp", "item": "Choice Scarf", "ability": "Rough Skin", "nature": "Jolly", "evs": {"atk": 32, "spe": 32},
         "moves": ["Earthquake", "Outrage", "Rock Slide", "Fire Fang"]},
    ]
    lb = LiveBattle(conn, EngineClient(), team, ["incineroar", "tyranitar", "lucario", "garchomp", "primarina", "dragonite"],
                    cfg=SearchConfig(seed="t", samples=2, model_reps=2))
    opts = lb.entry_options("incineroar")
    assert any(o["key"] == "ability:intimidate" and o["default"] for o in opts)
    v = lb.start([1, 2, 3], "어흥염", ["ability:intimidate"])
    me = next(m for m in v["mine"] if m["active"])
    opp = next(m for m in v["opp"] if m["active"])
    assert me["boosts"].get("공격") == -1 and opp["boosts"].get("공격") == -1      # 양쪽 위협이 서로에게
    # 상대가 마기라스로 교체하며 모래날림 → 모래바람
    f = v["form"]
    v = lb.submit_turn({"first": "me", "me": {"kind": "switch", "species": "rotomwash"},
                        "opp": {"kind": "switch", "species": "tyranitar", "entry": ["ability:sandstream"]}, "my_hp": None, "opp_hp": None})
    assert v["weather"] and v["weather"]["name"] == "모래바람"
    assert lb.sess.state.me.mons["rotomwash"].item == "airballoon" and not lb.sess.state.me.mons["rotomwash"].item_consumed
    # 공격을 맞아 HP가 줄면 풍선이 터진다
    hpmax = v["form"]["my_hp_max"]
    v = lb.submit_turn({"first": "opp", "me": {"kind": "move", "move": "hydropump"}, "opp": {"kind": "move", "move": "crunch"},
                        "my_hp": hpmax - 40, "opp_hp": 50})
    assert lb.sess.state.me.mons["rotomwash"].item_consumed
    # 상대 출전이 위협 면역일 가능성이 높으면(루카리오: 정신력) 공격 하락을 넣지 않는다
    lb.end("aborted", save=False)
    lb2 = LiveBattle(conn, EngineClient(), team, ["lucario", "tyranitar", "incineroar", "garchomp", "primarina", "dragonite"],
                     cfg=SearchConfig(seed="t", samples=2, model_reps=2))
    v = lb2.start([1, 2, 3], "루카리오", [])
    opp = next(m for m in v["opp"] if m["active"])
    if lb2._immune_prob("opp") >= 0.5:
        assert not opp["boosts"].get("공격") and any("위협이 통하지 않는" in w for w in v["warnings"])
    else:
        assert opp["boosts"].get("공격") == -1
    lb2.end("aborted", save=False)


def test_entry_options_follow_species_and_balloon_rules(conn):
    from pokechamp.battle.events import Event
    from pokechamp.battle.live import LiveBattle
    from pokechamp.battle.search import SearchConfig
    from pokechamp.engine_client import EngineClient
    team = [{"species": "Garchomp", "item": "Choice Scarf", "ability": "Rough Skin", "nature": "Jolly", "evs": {"atk": 32, "spe": 32},
             "moves": ["Earthquake", "Outrage", "Rock Slide", "Fire Fang"]},
            {"species": "Primarina", "item": "Sitrus Berry", "ability": "Torrent", "nature": "Modest", "evs": {"hp": 32, "spa": 32},
             "moves": ["Moonblast", "Surf", "Ice Beam", "Aqua Jet"]},
            {"species": "Corviknight", "item": "Leftovers", "ability": "Pressure", "nature": "Impish", "evs": {"hp": 32, "def": 32},
             "moves": ["Brave Bird", "Roost", "U-turn", "Defog"]}]
    lb = LiveBattle(conn, EngineClient(), team, ["incineroar", "tyranitar", "lucario", "garchomp", "primarina", "excadrill"],
                    cfg=SearchConfig(seed="t", samples=2, model_reps=2))
    keys = lambda sp: [o["key"] for o in lb.entry_options(sp)]   # noqa: E731
    assert "ability:intimidate" in keys("incineroar") and "ability:intimidate" not in keys("lucario")   # 가질 수 있는 특성만
    assert "ability:unnerve" in keys("tyranitar")                     # 숨겨진 특성(긴장감)도 가질 수 있으면 보인다
    assert all("item:airballoon" in keys(sp) for sp in ("incineroar", "lucario", "excadrill"))         # 풍선은 누구나
    lb.start([1, 2, 3], "excadrill", ["item:airballoon"])
    assert "item:airballoon" not in keys("lucario")                    # 다른 포켓몬이 이미 풍선 → 도구 중복 금지
    lb.sess.feed([Event("item", "opp", species="tyranitar", item="leftovers")])
    assert "item:airballoon" not in keys("tyranitar")                  # 도구가 이미 다른 것으로 드러남
    lb.end("aborted", save=False)


def test_trick_swaps_items_and_knock_off(conn):
    from pokechamp.battle.live import LiveBattle
    from pokechamp.battle.search import SearchConfig
    from pokechamp.engine_client import EngineClient
    team = [{"species": "Garchomp", "item": "Life Orb", "ability": "Rough Skin", "nature": "Jolly", "evs": {"atk": 32, "spe": 32},
             "moves": ["Earthquake", "Dragon Claw", "Rock Slide", "Fire Fang"]},
            {"species": "Primarina", "item": "Sitrus Berry", "ability": "Torrent", "nature": "Modest", "evs": {"hp": 32, "spa": 32},
             "moves": ["Moonblast", "Surf", "Ice Beam", "Aqua Jet"]},
            {"species": "Incineroar", "item": "Leftovers", "ability": "Blaze", "nature": "Careful", "evs": {"hp": 32, "spd": 32},
             "moves": ["Flare Blitz", "Knock Off", "Fake Out", "Parting Shot"]}]
    lb = LiveBattle(conn, EngineClient(), team, ["gholdengo", "tyranitar", "lucario", "garchomp", "primarina", "dragonite"],
                    cfg=SearchConfig(seed="t", samples=2, model_reps=2))
    v = lb.start([1, 2, 3], "타부자고", [])
    assert v["form"]["items_all"] and v["form"]["my_item"] == "생명의구슬"
    # 상대가 먼저 트릭: 상대가 구애스카프를 주고 내 생명의구슬을 가져갔다
    v = lb.submit_turn({"first": "opp", "me": {"kind": "move", "move": "earthquake"}, "opp": {"kind": "move", "move": "트릭"},
                        "my_hp": v["form"]["my_hp"], "opp_hp": 40, "swap": {"given": "구애스카프"}})
    st = lb.sess.state
    assert st.me.mons["garchomp"].held == "choicescarf" and st.opp.mons["gholdengo"].held == "lifeorb"
    assert lb.sess.belief.mon("gholdengo").item == "choicescarf"         # 상대의 원래 도구는 구애스카프였다는 증거
    assert "교환" in next(m for m in v["mine"] if m["active"])["item"]
    # 다음 턴에 지진을 쓰면 그 기술로 고정된다 → 그다음 추천에서 기술은 지진뿐
    v = lb.submit_turn({"first": "me", "me": {"kind": "move", "move": "earthquake"}, "opp": {"kind": "move", "move": "shadowball"},
                        "my_hp": 60, "opp_hp": 10})
    moves = {o["action"]["move"] for o in v["recommendation"]["options"] if o["action"]["kind"] == "move"}
    assert moves == {"earthquake"}
    lb.end("aborted", save=False)
    # 내 탁쳐서떨구기: 떨어뜨린 도구가 상대의 원래 도구 증거가 된다
    lb2 = LiveBattle(conn, EngineClient(), team, ["gholdengo", "tyranitar", "lucario", "garchomp", "primarina", "dragonite"],
                     cfg=SearchConfig(seed="t", samples=2, model_reps=2))
    v = lb2.start([3, 1, 2], "마기라스", [])
    lb2.submit_turn({"first": "opp", "me": {"kind": "move", "move": "knockoff"}, "opp": {"kind": "move", "move": "stoneedge"},
                     "my_hp": 100, "opp_hp": 70, "knocked": "먹다남은음식"})
    m = lb2.sess.state.opp.mons["tyranitar"]
    assert m.held == "" and m.item == "leftovers" and lb2.sess.belief.mon("tyranitar").item == "leftovers"
    lb2.end("aborted", save=False)


def test_persistent_and_consumable_items_in_estimates(conn):
    from pokechamp.battle.live import LiveBattle
    from pokechamp.battle.search import SearchConfig
    from pokechamp.engine_client import EngineClient
    team = [{"species": "Primarina", "item": "Sitrus Berry", "ability": "Torrent", "nature": "Modest", "evs": {"hp": 32, "spa": 32},
             "moves": ["Moonblast", "Surf", "Ice Beam", "Aqua Jet"]},
            {"species": "Garchomp", "item": "Life Orb", "ability": "Rough Skin", "nature": "Jolly", "evs": {"atk": 32, "spe": 32},
             "moves": ["Earthquake", "Dragon Claw", "Rock Slide", "Fire Fang"]},
            {"species": "Incineroar", "item": "Leftovers", "ability": "Blaze", "nature": "Careful", "evs": {"hp": 32, "spd": 32},
             "moves": ["Flare Blitz", "Knock Off", "Fake Out", "Parting Shot"]}]
    lb = LiveBattle(conn, EngineClient(), team, ["gholdengo", "tyranitar", "lucario", "garchomp", "primarina", "dragonite"],
                    cfg=SearchConfig(seed="t", samples=2, model_reps=2))
    v = lb.start([1, 2, 3], "타부자고", [])
    assert v["form"]["my_item_consumable"] and lb.is_consumable("sitrusberry") and not lb.is_consumable("leftovers")
    # 상대 먹다남은음식: 한 번 넣으면 기억하고 다음 턴 예상에 회복이 들어간다
    lb.submit_turn({"first": "opp", "me": {"kind": "move", "move": "surf"}, "opp": {"kind": "move", "move": "shadowball"},
                    "my_hp": 120, "opp_hp": 60, "extras": {"opp_item": "먹다남은음식"}})
    assert lb.sess.state.opp.mons["gholdengo"].item == "leftovers" and not lb.sess.state.opp.mons["gholdengo"].item_consumed
    e = lb.estimate({"kind": "move", "move": "moonblast"}, {"kind": "move", "move": "makeitrain"})
    assert any("먹다남은음식" in n for n in e["notes"])
    # 자뭉열매: HP가 절반 아래로 내려가면 발동해 '사용'으로 처리
    if e.get("my_item_used"):
        lb.submit_turn({"first": "opp", "me": {"kind": "move", "move": "surf"}, "opp": {"kind": "move", "move": "makeitrain"},
                        "my_hp": e["my_hp"], "opp_hp": e["opp_hp"], "extras": {"my_item_used": True}})
        assert lb.sess.state.me.mons["primarina"].item_consumed
    # 상대 열매는 넣으면 자동으로 '사용' 처리
    lb.submit_turn({"first": "opp", "me": {"kind": "move", "move": "surf"}, "opp": {"kind": "move", "move": "shadowball"},
                    "my_hp": 40, "opp_hp": 30, "extras": {"opp_item": "자뭉열매"}}) if not lb.sess.state.opp.mons["gholdengo"].fainted else None
    lb.end("aborted", save=False)
