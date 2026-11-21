"""계산기 (데미지 확정/난수 n타, 실수치) 테스트."""

from pokechamp.calc.ko import ko_chances


def test_ko_guaranteed():
    r = ko_chances([50] * 16, 100)
    assert r["guaranteed_n"] == 2 and r["random_n"] == 2 and r["label"] == "확정 2타"


def test_ko_random_probability():
    rolls = list(range(85, 101))             # 85..100
    r = ko_chances(rolls, 96)
    assert r["random_n"] == 1 and abs(r["random_p"] - 5 / 16) < 1e-9 and r["guaranteed_n"] == 2
    assert r["label"] == "난수 1타 (31.2%) · 확정 2타"


def test_ko_two_hit_convolution():
    r = ko_chances([40] * 8 + [60] * 8, 110)   # 2번 합 ≥110: (60,60)만 → 1/4
    assert r["random_n"] == 2 and abs(r["random_p"] - 0.25) < 1e-9 and r["guaranteed_n"] == 3


def test_ko_uses_current_hp():
    assert ko_chances([50] * 16, 40)["label"] == "확정 1타"


def test_ko_multihit():
    r = ko_chances([30] * 16, 100, hits=2)     # 한 번에 60
    assert r["guaranteed_n"] == 2


def test_ko_zero_damage():
    assert ko_chances([0] * 16, 100)["label"] == "데미지 없음"


def test_ko_out_of_range():
    assert ko_chances([1] * 16, 100)["label"] == "10타 안에 못 잡음"


# ---------- 계산 API (DB와 엔진 서버가 있을 때) ----------
import pytest  # noqa: E402


@pytest.fixture(scope="module")
def app():
    try:
        from pokechamp.engine_client import EngineClient
        from pokechamp.web.server import App
        EngineClient(timeout=2).health()
        return App()
    except Exception:  # noqa: BLE001
        pytest.skip("DB나 엔진 서버가 없다")


def _side(species, **kw):
    s = {"species": species, "item": kw.pop("item", ""), "ability": kw.pop("ability", ""), "nature": kw.pop("nature", ""),
         "evs": kw.pop("evs", {})}
    return {"set": s, **kw}


def test_calc_damage_basic(app):
    r = app.calc_damage({"attacker": _side("garchomp", nature="adamant", evs={"atk": 32}), "defender": _side("aggron"),
                         "move": "earthquake"})
    assert len(r["normal"]["rolls"]) == 16 and r["effectiveness"] == 4 and r["normal"]["ko"]["label"]
    assert r["crit"]["max"] > r["normal"]["max"] and r["notes"]


def test_calc_damage_immune(app):
    r = app.calc_damage({"attacker": _side("garchomp"), "defender": _side("corviknight"), "move": "earthquake"})
    assert r["immune"] and r["normal"]["ko"]["label"] == "데미지 없음"


def test_calc_damage_status_move(app):
    assert app.calc_damage({"attacker": _side("garchomp"), "defender": _side("aggron"), "move": "swordsdance"})["status"]


def test_calc_hp_pct_and_multihit(app):
    full = app.calc_damage({"attacker": _side("garchomp"), "defender": _side("aggron"), "move": "earthquake"})
    half = app.calc_damage({"attacker": _side("garchomp"), "defender": _side("aggron", hp_pct=50), "move": "earthquake"})
    assert half["defender"]["hp"] < full["defender"]["hp"] == full["defender"]["maxhp"]
    dw = app.calc_damage({"attacker": _side("aerodactyl"), "defender": _side("garchomp"), "move": "dualwingbeat"})
    assert dw["hit_options"] == [2] and dw["hits"] == 2
    bs = app.calc_damage({"attacker": _side("abomasnow"), "defender": _side("garchomp"), "move": "bulletseed", "hits": 5})
    assert bs["hit_options"] == [2, 3, 4, 5] and bs["hits"] == 5


def test_calc_mega_without_stone(app):
    with pytest.raises(Exception, match="메가진화할 수 없다"):
        app.calc_damage({"attacker": _side("garchomp", mega=True), "defender": _side("aggron"), "move": "earthquake"})


def test_calc_stats_speed(app):
    a = app.calc_stats({"set": {"species": "garchomp", "nature": "jolly", "evs": {"spe": 32}}})
    b = app.calc_stats({"set": {"species": "garchomp", "nature": "adamant", "evs": {"spe": 32}}})
    assert a["spe"] > b["spe"]
    m = app.calc_stats({"set": {"species": "garchomp", "item": "garchompite"}, "mega": True})
    assert m["species"].startswith("Garchomp-Mega")


def test_ko_heterogeneous_hits():
    from pokechamp.calc.ko import ko_chances_hits
    assert ko_chances_hits([[20] * 16, [40] * 16, [60] * 16], 120)["label"] == "확정 1타"   # 타마다 위력이 다른 기술
    assert ko_chances_hits([[20] * 16, [40] * 16, [60] * 16], 121)["label"] == "확정 2타"


def test_calc_parental_bond_counts_second_hit(app):
    r = app.calc_damage({"attacker": _side("kangaskhan", item="kangaskhanite", mega=True), "defender": _side("garchomp"),
                         "move": "doubleedge"})
    first = r["per_hit"][0]["rolls"]
    assert r["hits"] == 2 and r["normal"]["max"] > first[-1] and r["normal"]["max"] < 2 * first[-1]


def test_calc_triple_axel_rising_power(app):
    r = app.calc_damage({"attacker": _side("froslass"), "defender": _side("garchomp"), "move": "tripleaxel"})
    first = r["per_hit"][0]["rolls"][-1]
    assert r["hits"] == 3 and r["normal"]["max"] > 3 * first


def test_calc_absorb_ability_is_immune(app):
    r = app.calc_damage({"attacker": _side("garchomp"), "defender": _side("jolteon", ability="voltabsorb"), "move": "thunderfang"})
    assert r["immune"] and r["normal"]["ko"]["label"] == "데미지 없음"


def test_calc_skill_link_defaults_to_max_hits(app):
    r = app.calc_damage({"attacker": _side("heracross", item="heracronite", mega=True), "defender": _side("garchomp"),
                         "move": "pinmissile"})
    assert r["hits"] == 5



def test_calc_burned_attacker_halves_physical(app):
    plain = app.calc_damage({"attacker": _side("garchomp"), "defender": _side("aggron"), "move": "dragonclaw"})
    brn = app.calc_damage({"attacker": _side("garchomp", status="brn"), "defender": _side("aggron"), "move": "dragonclaw"})
    assert brn["normal"]["max"] <= plain["normal"]["max"] // 2 + 1
    sp = {"attacker": _side("garchomp", status="brn"), "defender": _side("aggron"), "move": "dracometeor"}
    assert app.calc_damage(sp)["normal"]["max"] == app.calc_damage({**sp, "attacker": _side("garchomp")})["normal"]["max"]

def test_calc_reflect_halves_physical(app):
    req = {"attacker": _side("garchomp"), "defender": _side("aggron"), "move": "dragonclaw"}
    plain = app.calc_damage(req)
    wall = app.calc_damage({**req, "field": {"screens": ["reflect"]}})
    assert wall["normal"]["max"] < plain["normal"]["max"] * 0.6
    assert wall["crit"]["max"] == plain["crit"]["max"]                  # 급소는 벽을 무시
    sp = app.calc_damage({**req, "move": "dracometeor", "field": {"screens": ["reflect"]}})
    assert sp["normal"]["max"] == app.calc_damage({**req, "move": "dracometeor"})["normal"]["max"]


def test_calc_stats_paralysis_halves_speed(app):
    a = app.calc_stats({"set": {"species": "garchomp", "nature": "jolly", "evs": {"spe": 32}}})
    p = app.calc_stats({"set": {"species": "garchomp", "nature": "jolly", "evs": {"spe": 32}}, "status": "par"})
    assert p["spe"] == a["spe"] and p["spe_status"] == a["spe"] // 2


def test_builder_options_lists_mega_forms(app):
    megas = {m["id"]: m for m in app.builder_options({})["megas"]}
    assert megas["garchompmega"]["base"] == "garchomp" and megas["garchompmega"]["stone"] == "garchompite"
    assert megas["garchompmegaz"]["stone"] == "garchompitez" and megas["garchompmega"]["name"]


def test_ko_label_clamps_tiny_and_huge_probabilities():
    tiny = ko_chances([1] * 15 + [100], 100)               # 1/16 → 이후 n에서도 작음
    assert tiny["label"].startswith("난수 1타 (6.2%)")
    r = ko_chances([10] * 15 + [11], 110, max_n=10)       # 10타 합 ≥110: 열 번 모두 11이어야 → (1/16)^10
    assert "(<0.1%)" in r["label"]


def test_calc_mega_entry_weather_not_applied_unless_chosen(app):
    """메가리자몽Y의 가뭄은 메가진화 때 쾌청을 깔지만, 계산기는 사용자가 고른 날씨만 쓴다."""
    att = _side("charizard", item="charizarditey", mega=True)
    none = app.calc_damage({"attacker": att, "defender": _side("garchomp"), "move": "flamethrower"})
    sun = app.calc_damage({"attacker": att, "defender": _side("garchomp"), "move": "flamethrower",
                           "field": {"weather": "sunnyday"}})
    assert sun["normal"]["max"] > none["normal"]["max"] * 1.4


def test_calc_stats_hints_weather_from_ability(app):
    y = app.calc_stats({"set": {"species": "charizard", "item": "charizarditey"}, "mega": True})
    assert y["field_hint"] == {"weather": "sunnyday", "ability": "drought"}
    assert app.calc_stats({"set": {"species": "garchomp"}})["field_hint"] is None
