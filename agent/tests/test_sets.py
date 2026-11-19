"""세트 원형(결합 분포) 테스트."""

import json
import random
from datetime import date

import pytest

from pokechamp.data.db import connect
from pokechamp.data.priors import Priors
from pokechamp.sets.archetypes import (
    Archetype, build_all, ipf, load_archetypes, load_rules, rule_violations, sample_sets,
)

CFG = {"samples_per_species": 800, "max_archetypes": 30, "ipf_iterations": 40, "rule_generated_weight": 0.5, "seed": 1}
MOVES = [("earthquake", "Ground", 100, "Physical"), ("outrage", "Dragon", 120, "Physical"),
         ("stealthrock", "Rock", 0, "Status"), ("swordsdance", "Normal", 0, "Status"), ("rocktomb", "Rock", 60, "Physical"),
         ("scaleshot", "Dragon", 25, "Physical"), ("firefang", "Fire", 65, "Physical"), ("trick", "Psychic", 0, "Status"),
         ("ironhead", "Steel", 80, "Physical"), ("poisonjab", "Poison", 80, "Physical")]


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.sqlite")
    c.execute("INSERT INTO static_versions (applied_at, regulation, showdown_commit, format, active) VALUES ('x','M-C','c','f',1)")
    c.execute("INSERT INTO species (version_id,id,name_en,num,base_species,is_mega,types_json,base_stats_json,abilities_json)"
              " VALUES (1,'garchomp','Garchomp',445,'Garchomp',0,'[\"Dragon\",\"Ground\"]',"
              "'{\"hp\":108,\"atk\":130,\"def\":95,\"spa\":80,\"spd\":85,\"spe\":102}','{\"0\":\"Sand Veil\",\"H\":\"Rough Skin\"}')")
    c.execute("INSERT INTO species (version_id,id,name_en,num,base_species,is_mega,types_json,base_stats_json,abilities_json)"
              " VALUES (1,'garchompmegaz','Garchomp-Mega-Z',445,'Garchomp',1,'[\"Dragon\"]','{}','{\"0\":\"Levitate\"}')")
    c.execute("INSERT INTO species (version_id,id,name_en,num,base_species,is_mega,types_json,base_stats_json,abilities_json)"
              " VALUES (1,'snorlax','Snorlax',143,'Snorlax',0,'[\"Normal\"]',"
              "'{\"hp\":160,\"atk\":110,\"def\":65,\"spa\":65,\"spd\":110,\"spe\":30}','{\"0\":\"Thick Fat\"}')")
    for m, t, pw, cat in MOVES:
        c.execute("INSERT INTO moves (version_id,id,name_en,type,category,power,pp,max_pp,priority) VALUES (1,?,?,?,?,?,10,12,0)",
                  (m, m, t, cat, pw))
        c.execute("INSERT INTO learnsets VALUES (1,'garchomp',?)", (m,))
    for m in ("earthquake", "ironhead"):
        c.execute("INSERT INTO learnsets VALUES (1,'snorlax',?)", (m,))
    for it in ("garchompitez", "choicescarf", "focussash", "assaultvest", "lifeorb", "charizarditey"):
        c.execute("INSERT INTO items (version_id,id,name_en,is_mega_stone,is_berry) VALUES (1,?,?,0,0)", (it, it))
    c.execute("INSERT INTO mega_forms VALUES (1,'garchomp','garchompmegaz','garchompitez')")
    c.execute("INSERT INTO mega_forms VALUES (1,'charizard','charizardmegay','charizarditey')")
    sid = c.execute("INSERT INTO snapshots (captured_at,source,format,regulation,official_season,page_season,page_updated_at,verified)"
                    " VALUES ('n','s','singles','M-C','M-8','M7','2026-11-19',0)").lastrowid
    for i, (it, r) in enumerate([("garchompitez", 0.4), ("choicescarf", 0.35), ("focussash", 0.25)]):
        c.execute("INSERT INTO usage_items VALUES (?,?,?,?,?)", (sid, "garchomp", it, i + 1, r))
    for i, (m, r) in enumerate([("earthquake", 0.9), ("outrage", 0.6), ("stealthrock", 0.5), ("swordsdance", 0.4),
                                ("rocktomb", 0.35), ("scaleshot", 0.3), ("firefang", 0.25), ("trick", 0.1)]):
        c.execute("INSERT INTO usage_moves VALUES (?,?,?,?,?)", (sid, "garchomp", m, i + 1, r))
    c.execute("INSERT INTO usage_abilities VALUES (?,?,?,?,?)", (sid, "garchomp", "roughskin", 1, 1.0))
    for i, (n, r) in enumerate([("jolly", 0.6), ("adamant", 0.3), ("modest", 0.1)]):
        c.execute("INSERT INTO usage_natures VALUES (?,?,?,?,?)", (sid, "garchomp", n, i + 1, r))
    c.execute("INSERT INTO usage_spreads VALUES (?,?,?,?,?)", (sid, "garchomp", 1, json.dumps({"hp": 2, "atk": 32, "spe": 32}), 0.7))
    c.execute("INSERT INTO usage_spreads VALUES (?,?,?,?,?)", (sid, "garchomp", 2, json.dumps({"hp": 2, "spa": 32, "spe": 32}), 0.3))
    c.commit()
    return c


def test_rules(conn):
    r = load_rules(conn, 1)["garchomp"]
    ok = ("earthquake", "outrage", "rocktomb", "scaleshot")
    assert rule_violations(r, "choicescarf", "roughskin", ok) == []
    assert rule_violations(r, "choicescarf", "roughskin", ("earthquake", "outrage", "rocktomb", "trick")) == []  # 구애 트릭 허용
    assert any("변화기" in x for x in rule_violations(r, "choicescarf", "roughskin", ("earthquake", "outrage", "rocktomb", "swordsdance")))
    assert any("변화기" in x for x in rule_violations(r, "assaultvest", "roughskin", ("earthquake", "outrage", "rocktomb", "trick")))
    assert any("메가스톤" in x for x in rule_violations(r, "charizarditey", "roughskin", ok))
    assert any("학습 불가" in x for x in rule_violations(r, "lifeorb", "roughskin", ("earthquake", "outrage", "rocktomb", "hyperbeam")))
    assert any("특성" in x for x in rule_violations(r, "lifeorb", "levitate", ok))
    assert any("Stat Points" in x for x in rule_violations(r, "lifeorb", "roughskin", ok, {"hp": 32, "atk": 32, "spe": 32}))


def test_ipf_matches_margins():
    archs = [Archetype("x", "a", None, ("m1", "m2"), 1.0), Archetype("x", "a", None, ("m1", "m3"), 1.0),
             Archetype("x", "b", None, ("m2", "m3"), 1.0), Archetype("x", "b", None, ("m1", "m2"), 1.0)]
    ipf(archs, {"a": 0.7, "b": 0.3}, {}, {"m1": 0.8, "m2": 0.6}, 200)
    assert sum(a.weight for a in archs) == pytest.approx(1.0)
    assert sum(a.weight for a in archs if a.item == "a") == pytest.approx(0.7, abs=0.01)
    assert sum(a.weight for a in archs if "m1" in a.moves) == pytest.approx(0.8, abs=0.01)


def test_build_calibrates_and_respects_rules(conn):
    r = build_all(conn, cfg=CFG, priors=Priors(conn, today=date(2026, 11, 19)))
    assert r["fallback"] == ["snorlax"]                   # 통계 없는 포켓몬은 기본 세트
    archs = load_archetypes(conn, "garchomp")
    rules = load_rules(conn, 1)["garchomp"]
    assert archs and all(not rule_violations(rules, a.item, a.ability, a.moves) for a in archs)
    assert sum(a.weight for a in archs) == pytest.approx(1.0)
    assert sum(a.weight for a in archs if a.item == "choicescarf") == pytest.approx(0.35, abs=0.03)
    assert sum(a.weight for a in archs if "earthquake" in a.moves) == pytest.approx(0.9, abs=0.05)
    assert all(a.mega_species == "garchompmegaz" for a in archs if a.item == "garchompitez")
    scarf = [a for a in archs if a.item == "choicescarf"]
    assert all("swordsdance" not in a.moves and "stealthrock" not in a.moves for a in scarf)
    # 물리 원형에는 특공을 올리는 성격(조심)이 거의 없다
    phys = archs[0]
    assert phys.nature_dist.get("modest", 0) < 0.05


def test_samples_have_no_violations(conn):
    build_all(conn, cfg=CFG, priors=Priors(conn, today=date(2026, 11, 19)))
    rules = load_rules(conn, 1)
    rng = random.Random(3)
    for sp in ("garchomp", "snorlax"):
        for s in sample_sets(load_archetypes(conn, sp), 3000, rng):
            assert not rule_violations(rules[sp], s["item"] or None, s["ability"] or None, tuple(s["moves"]), s["evs"]), s


def test_team_samples_become_supported_archetypes(conn):
    team = [{"species": "Garchomp", "item": "Focus Sash", "ability": "Rough Skin", "nature": "Jolly",
             "evs": {"hp": 2, "atk": 32, "spe": 32}, "moves": ["Earthquake", "Outrage", "Stealth Rock", "Rock Tomb"]}]
    for i in range(3):
        conn.execute("INSERT INTO team_samples (source, source_team_id, kind, regulation, team_json) VALUES ('t',?, 'replica','M-C',?)",
                     (str(i), json.dumps(team)))
    conn.commit()
    build_all(conn, cfg=CFG, priors=Priors(conn, today=date(2026, 11, 19)))
    sup = [a for a in load_archetypes(conn, "garchomp") if not a.rule_generated]
    assert len(sup) == 1 and sup[0].n_support == 3 and sup[0].item == "focussash"
