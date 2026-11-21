"""능력치 표기: 항상 H·A·B·C·D·S 순서 (데이터의 키 순서와 상관없이)."""

from pokechamp.stat_order import STAT_ORDER, sp_text, stat_letter


def test_order_and_letters():
    assert STAT_ORDER == ("hp", "atk", "def", "spa", "spd", "spe")
    assert [stat_letter(k) for k in STAT_ORDER] == ["H", "A", "B", "C", "D", "S"]


def test_sp_text_follows_habcds_even_if_dict_is_alphabetical():
    assert sp_text({"atk": 32, "def": 0, "hp": 2, "spa": 0, "spd": 0, "spe": 32}) == "H2 A32 S32"
    assert sp_text({"spe": 32, "spd": 4, "hp": 30}) == "H30 D4 S32"
    assert sp_text({}) == ""


def test_export_text_orders_evs():
    from pokechamp.teams.store import export_text
    t = export_text([{"species": "Garchomp", "evs": {"spe": 32, "atk": 32, "hp": 2}, "moves": ["Earthquake"]}])
    assert "EVs: 2 HP / 32 Atk / 32 Spe" in t


def test_team_line_orders_sp():
    from pokechamp.teambuild.search import Names
    n = Names.__new__(Names)
    n.ko = lambda table, x: x          # 이름 변환 없이 줄 형식만 본다
    line = n.line_ko({"species": "garchomp", "item": "", "ability": "", "nature": "", "evs": {"spe": 32, "atk": 32, "hp": 2},
                      "moves": []})
    assert "SP H2 A32 S32" in line
