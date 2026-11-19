"""정적 데이터 파이프라인 단위 테스트 (네트워크, 엔진 없이)."""

import json

import pytest

from pokechamp.data.db import active_version, connect
from pokechamp.data.static import build as build_mod
from pokechamp.data.static.build import build_bundle
from pokechamp.data.static.names import mega_name_ko, to_id
from pokechamp.data.static.official_list import OfficialEntry, OfficialList, parse_pokemons, pick_regulation_notice
from pokechamp.data.static.update import (
    activate_version, apply_bundle, champions_max_pp, compute_diff, format_diff, revalidate_my_teams,
)


def sp(id_, name, num, form, types, mega=False, forme="", base=None, abilities=None):
    return {"id": id_, "name": name, "num": num, "base_species": base or name, "forme": forme, "form_index": form,
            "is_mega": mega, "battle_only": None, "required_item": None, "types": types,
            "base_stats": {"hp": 1, "atk": 1, "def": 1, "spa": 1, "spd": 1, "spe": 1},
            "abilities": abilities or {"0": "Rough Skin"}, "weightkg": 1.0}


def mv(id_, name, power=80, acc=100, pp=15):
    return {"id": id_, "name": name, "num": 1, "type": "Dragon", "category": "Physical", "base_power": power,
            "accuracy": acc, "pp": pp, "priority": 0, "target": "normal", "flags": ["contact"],
            "has_secondary": False, "desc": ""}


SD = {
    "showdown_commit": "abc1234def", "format": "gen9championsbssregmc",
    "species": [
        sp("garchomp", "Garchomp", 445, 0, ["Dragon", "Ground"]),
        sp("garchompmegaz", "Garchomp-Mega-Z", 445, 2, ["Dragon"], True, "Mega-Z", "Garchomp", {"0": "Levitate"}),
        sp("slowbro", "Slowbro", 80, 0, ["Water", "Psychic"], abilities={"0": "Oblivious"}),
        sp("slowbrogalar", "Slowbro-Galar", 80, 2, ["Poison", "Psychic"], forme="Galar", base="Slowbro",
           abilities={"0": "Quick Draw"}),
        sp("pikachu", "Pikachu", 25, 0, ["Electric"], abilities={"0": "Static"}),
    ],
    "moves": [mv("dragonclaw", "Dragon Claw"), mv("earthquake", "Earthquake", 100, 100, 10), mv("thunderbolt", "Thunderbolt")],
    "items": [
        {"id": "garchompitez", "name": "Garchompite Z", "num": 1, "mega_stone": {"Garchomp": "Garchomp-Mega-Z"}, "is_berry": False, "desc": ""},
        {"id": "pikachuite", "name": "Pikachuite", "num": 2, "mega_stone": {"Pikachu": "Pikachu-Mega"}, "is_berry": False, "desc": ""},
        {"id": "choicescarf", "name": "Choice Scarf", "num": 3, "mega_stone": None, "is_berry": False, "desc": ""},
    ],
    "abilities": [{"id": a, "name": n, "num": 1, "desc": ""} for a, n in
                  [("roughskin", "Rough Skin"), ("levitate", "Levitate"), ("quickdraw", "Quick Draw"), ("static", "Static"),
                   ("oblivious", "Oblivious")]],
    "learnsets": {"garchomp": ["dragonclaw", "earthquake"], "slowbrogalar": ["earthquake"], "pikachu": ["thunderbolt"]},
    "mega_forms": [
        {"base_species": "garchomp", "mega_species": "garchompmegaz", "stone_item": "garchompitez"},
        {"base_species": "pikachu", "mega_species": "pikachumega", "stone_item": "pikachuite"},
    ],
}
NAMES = {"move": {"dragonclaw": "드래곤클로", "earthquake": "지진"}, "item": {"garchompitez": "한카리아스나이트Z"},
         "ability": {"roughskin": "까칠한피부", "levitate": "부유", "quickdraw": "속사"},
         "nature": {"adamant": "고집"}, "type": {}}
OFFICIAL = OfficialList("M-C", "816", 0, 2_000_000_000, "https://x/", [
    OfficialEntry("0445-000", 445, 0, "한카리아스", "Garchomp"),
    OfficialEntry("0080-002", 80, 2, "야도란 (가라르의 모습)", "Slowbro (Galarian Form)"),
])


@pytest.fixture(autouse=True)
def no_aliases(monkeypatch, tmp_path):
    monkeypatch.setattr(build_mod, "ALIASES_PATH", tmp_path / "none.csv")


def test_to_id_and_mega_names():
    assert to_id("Garchomp-Mega-Z") == "garchompmegaz"
    assert mega_name_ko("한카리아스", "Mega-Z") == "메가한카리아스Z"
    assert mega_name_ko("리자몽", "Mega-X") == "메가리자몽X"
    assert mega_name_ko("냐오닉스 (수컷의 모습)", "M-Mega") == "메가냐오닉스 (수컷의 모습)"
    assert champions_max_pp(5) == 8 and champions_max_pp(10) == 12 and champions_max_pp(20) == 20


def test_parse_pokemons_and_pick_regulation():
    assert parse_pokemons('<script>const pokemons = [["0003-000",1,"이상해꽃"]];</script>') == [["0003-000", 1, "이상해꽃"]]
    with pytest.raises(ValueError):
        parse_pokemons("<html></html>")
    news = {"data": [
        {"id": "751", "title": "「레귤레이션 M-A」에 대해", "infoKind": 12, "stAt": 100, "endAt": "200", "linkRom": ["CHP"]},
        {"id": "816", "title": "「레귤레이션 M-C」에 대해", "infoKind": 12, "stAt": 300, "endAt": "400", "linkRom": ["CHP"]},
        {"id": "999", "title": "레귤레이션 M-Z", "infoKind": 12, "stAt": 350, "endAt": "450", "linkRom": ["SC"]},
    ]}
    assert pick_regulation_notice(news, now=150)["id"] == "751"
    assert pick_regulation_notice(news, now=350)["id"] == "816"
    assert pick_regulation_notice(news, now=1000)["id"] == "816"  # 유효한 공지가 없으면 최근 것


def test_build_filters_to_official_list_and_their_megas():
    b = build_bundle(OFFICIAL, SD, NAMES)
    assert {s["id"] for s in b.species} == {"garchomp", "garchompmegaz", "slowbrogalar"}  # 피카츄, 야도란 원종 제외
    assert {m["id"] for m in b.moves} == {"dragonclaw", "earthquake"}                     # 허용 종이 배우는 기술만
    assert {i["id"] for i in b.items} == {"garchompitez", "choicescarf"}                  # 허용 안 된 메가스톤 제외
    names = {s["id"]: s["name_ko"] for s in b.species}
    assert names["slowbrogalar"] == "야도란 (가라르의 모습)" and names["garchompmegaz"] == "메가한카리아스Z"
    assert any("Choice Scarf" in w for w in b.warnings)  # 한국어 이름 없는 항목은 경고


def test_build_fails_loudly_when_official_entry_unmapped():
    bad = OfficialList("M-C", "816", 0, 1, "u", [OfficialEntry("0999-000", 999, 0, "없는포켓몬", "Nope")])
    with pytest.raises(ValueError, match="0999-000"):
        build_bundle(bad, SD, NAMES)


def test_apply_diff_and_rollback(tmp_path):
    conn = connect(tmp_path / "t.sqlite")
    b1 = build_bundle(OFFICIAL, SD, NAMES)
    assert compute_diff(conn, b1).first_version
    v1 = apply_bundle(conn, b1)
    assert compute_diff(conn, b1).empty

    sd2 = json.loads(json.dumps(SD))
    sd2["moves"][1]["accuracy"] = 90                     # 지진 명중 변경
    sd2["learnsets"]["garchomp"] = ["dragonclaw"]         # 학습 기술 제거
    b2 = build_bundle(OFFICIAL, sd2, NAMES)
    d = compute_diff(conn, b2)
    assert any("accuracy: 100 → 90" in x for x in d.changed["moves"])
    assert any(x.startswith("garchomp:") for x in d.changed["learnsets"])
    assert "지진" in format_diff(d, b2)
    v2 = apply_bundle(conn, b2)
    assert active_version(conn)["id"] == v2
    activate_version(conn, v1)
    assert active_version(conn)["id"] == v1
    assert conn.execute("SELECT COUNT(*) FROM static_versions").fetchone()[0] == 2  # 이전 버전 보관


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail

    def batch(self, path, items):
        if self.fail:
            raise ConnectionError("down")
        return [{"ok": True, "result": {"valid": False, "problems": ["Item Clause"]}} for _ in items]


def test_revalidate_my_teams(tmp_path):
    conn = connect(tmp_path / "t.sqlite")
    assert revalidate_my_teams(conn, FakeClient()) == []
    conn.execute("INSERT INTO my_teams (name, created_at, source, team_json) VALUES ('A', 'now', 'manual', '[]')")
    conn.commit()
    assert "건너뛰었다" in revalidate_my_teams(conn, FakeClient(fail=True))[0]
    msgs = revalidate_my_teams(conn, FakeClient())
    assert "Item Clause" in msgs[0]
    assert conn.execute("SELECT valid FROM my_teams").fetchone()[0] == 0
