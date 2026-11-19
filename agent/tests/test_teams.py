"""팀 입력(한국어/영어), 빈 항목 채우기, 저장 테스트."""

import json
from datetime import date

import pytest

from pokechamp.data.db import connect
from pokechamp.data.priors import Priors
from pokechamp.input.team_text import NameIndex, TeamParseError, parse_evs, parse_team
from pokechamp.teams.store import delete_team, export_text, fill_defaults, list_teams, load_team, save_team, validate


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.sqlite")
    c.execute("INSERT INTO static_versions (applied_at, regulation, showdown_commit, format, active) VALUES ('x','M-C','c','f',1)")
    sp = [("garchomp", "Garchomp", "한카리아스", 445, 0, "Garchomp", '{"0":"Rough Skin"}'),
          ("rotomwash", "Rotom-Wash", "로토무 (워시로토무)", 479, 2, "Rotom", '{"0":"Levitate"}'),
          ("ninetalesalola", "Ninetales-Alola", "나인테일 (알로라의 모습)", 38, 1, "Ninetales", '{"0":"Snow Warning"}')]
    for sid, en, ko, num, fi, base, ab in sp:
        c.execute("INSERT INTO species (version_id,id,name_en,name_ko,num,form_index,base_species,is_mega,types_json,base_stats_json,abilities_json)"
                  " VALUES (1,?,?,?,?,?,?,0,'[]','{}',?)", (sid, en, ko, num, fi, base, ab))
    for mid, en, ko, cat in [("earthquake", "Earthquake", "지진", "Physical"), ("outrage", "Outrage", "역린", "Physical"),
                             ("stealthrock", "Stealth Rock", "스텔스록", "Status"), ("hydropump", "Hydro Pump", "하이드로펌프", "Special"),
                             ("voltswitch", "Volt Switch", "볼트체인지", "Special"), ("willowisp", "Will-O-Wisp", "도깨비불", "Status"),
                             ("painsplit", "Pain Split", "아픔나누기", "Status"), ("swordsdance", "Swords Dance", "칼춤", "Status")]:
        c.execute("INSERT INTO moves (version_id,id,name_en,name_ko,type,category,power,pp,max_pp,priority) VALUES (1,?,?,?,'Normal',?,0,10,12,0)",
                  (mid, en, ko, cat))
    for sid, ms in [("garchomp", ["earthquake", "outrage", "stealthrock", "swordsdance"]),
                    ("rotomwash", ["hydropump", "voltswitch", "willowisp", "painsplit"])]:
        for m in ms:
            c.execute("INSERT INTO learnsets VALUES (1,?,?)", (sid, m))
    for iid, en, ko in [("choicescarf", "Choice Scarf", "구애스카프"), ("leftovers", "Leftovers", "먹다남은음식"),
                        ("sitrusberry", "Sitrus Berry", "자뭉열매")]:
        c.execute("INSERT INTO items (version_id,id,name_en,name_ko,is_mega_stone,is_berry) VALUES (1,?,?,?,0,0)", (iid, en, ko))
    for aid, en, ko in [("roughskin", "Rough Skin", "까칠한피부"), ("levitate", "Levitate", "부유"), ("snowwarning", "Snow Warning", "눈퍼뜨리기")]:
        c.execute("INSERT INTO abilities (version_id,id,name_en,name_ko) VALUES (1,?,?,?)", (aid, en, ko))
    for nid, en, ko in [("jolly", "Jolly", "명랑"), ("bold", "Bold", "대담"), ("timid", "Timid", "겁쟁이")]:
        c.execute("INSERT INTO natures (version_id,id,name_en,name_ko) VALUES (1,?,?,?)", (nid, en, ko))
    sid = c.execute("INSERT INTO snapshots (captured_at,source,format,regulation,official_season,page_season,page_updated_at,verified)"
                    " VALUES ('n','s','singles','M-C','M-8','M7','2026-11-19',0)").lastrowid
    for i, (it, r) in enumerate([("leftovers", 0.5), ("choicescarf", 0.3), ("sitrusberry", 0.2)]):
        c.execute("INSERT INTO usage_items VALUES (?,?,?,?,?)", (sid, "rotomwash", it, i + 1, r))
    for i, (m, r) in enumerate([("hydropump", 0.95), ("voltswitch", 0.8), ("willowisp", 0.7), ("painsplit", 0.5)]):
        c.execute("INSERT INTO usage_moves VALUES (?,?,?,?,?)", (sid, "rotomwash", m, i + 1, r))
    c.execute("INSERT INTO usage_abilities VALUES (?,?,?,?,?)", (sid, "rotomwash", "levitate", 1, 1.0))
    c.execute("INSERT INTO usage_natures VALUES (?,?,?,?,?)", (sid, "rotomwash", "bold", 1, 0.6))
    c.execute("INSERT INTO usage_spreads VALUES (?,?,?,?,?)", (sid, "rotomwash", 1, json.dumps({"hp": 32, "def": 32, "spd": 2}), 0.4))
    c.commit()
    return c


TEXT = """한카리아스 @ 구애스카프
특성: 까칠한피부
명랑 성격
SP: 2 HP / 32 공격 / 32 스피드
- 지진
- 역린

워시로토무

Ninetales-Alola @ Sitrus Berry
Ability: Snow Warning
EVs: 2 HP / 32 SpA / 32 Spe
Timid Nature"""


def test_parse_korean_english_and_forms(conn):
    sets = parse_team(TEXT, NameIndex(conn))
    g, r, n = sets
    assert (g.species, g.item, g.ability, g.nature) == ("Garchomp", "Choice Scarf", "Rough Skin", "Jolly")
    assert g.evs == {"hp": 2, "atk": 32, "spe": 32} and g.moves == ["Earthquake", "Outrage"]
    assert g.missing == ["moves"]
    assert r.species == "Rotom-Wash" and set(r.missing) == {"item", "ability", "nature", "evs", "moves"}
    assert n.species == "Ninetales-Alola" and n.evs["spa"] == 32
    # "알로라 나인테일" 같은 어순도 받는다
    assert parse_team("알로라 나인테일", NameIndex(conn))[0].species == "Ninetales-Alola"


def test_parse_errors_suggest_names(conn):
    with pytest.raises(TeamParseError, match="한카리아스"):
        parse_team("한카리아수 @ 구애스카프", NameIndex(conn))
    with pytest.raises(TeamParseError, match="능력치"):
        parse_evs("32 행운")
    assert parse_evs("252 Atk / 4 HP") == {"atk": 252, "hp": 4}


def test_fill_defaults_marks_and_avoids_duplicate_items(conn):
    sets = parse_team("한카리아스 @ 먹다남은음식\n- 지진\n\n워시로토무", NameIndex(conn))
    notes = fill_defaults(conn, sets, Priors(conn, today=date(2026, 11, 19)))
    r = sets[1]
    assert r.item == "Choice Scarf"                      # 먹다남은음식은 이미 한카리아스가 들었다 → 다음 후보
    assert r.ability == "Levitate" and r.nature == "Bold" and r.evs == {"hp": 32, "def": 32, "spd": 2}
    assert set(r.moves) == {"Hydro Pump", "Volt Switch"}  # 구애스카프 + 변화기 금지 → 쓸 수 있는 공격기만 채운다
    assert set(r.filled) == {"item", "ability", "nature", "evs", "moves"} and r.to_set()["filled"]
    assert any("Rotom-Wash" in n for n in notes)


class DownClient:
    def validate_team(self, team):
        raise ConnectionError("down")


def test_save_list_show_delete(conn):
    sets = [p.to_set() for p in parse_team(TEXT, NameIndex(conn))]
    valid, problems = validate(sets, DownClient())
    assert valid is None and "건너뛰었다" in problems[0]
    save_team(conn, "A", sets, "manual", valid, [])
    with pytest.raises(ValueError):
        save_team(conn, "A", sets)
    save_team(conn, "A", sets, replace=True)
    teams = list_teams(conn)
    assert [t["name"] for t in teams] == ["A"] and teams[0]["source"] == "manual"
    text = export_text(load_team(conn, "A")["team"])
    assert "Garchomp @ Choice Scarf" in text and "EVs: 2 HP / 32 Atk / 32 Spe" in text
    again = parse_team(text, NameIndex(conn))                # 내보낸 텍스트를 다시 읽을 수 있다
    assert again[0].species == "Garchomp" and again[0].evs == {"hp": 2, "atk": 32, "spe": 32}
    assert delete_team(conn, "A") and list_teams(conn) == []
