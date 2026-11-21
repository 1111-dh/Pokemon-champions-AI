"""웹 화면 백엔드 단위 테스트 (엔진 없이)."""

from pokechamp.web.sprites import sprite_id
from pokechamp.web.server import parse_verified


def test_sprite_id_and_verified_parse():
    assert sprite_id("Garchomp-Mega-Z") == "garchomp-megaz"
    assert sprite_id("Tauros-Paldea-Combat") == "tauros-paldeacombat"
    rows = parse_verified("| # | 항목 | 결과 | 상태 |\n|---|---|---|---|\n| 5 | 상성 | **4배** | ✅ 게임 확인 |\n")
    assert rows == [{"no": 5, "item": "상성", "result": "**4배**", "state": "✅ 게임 확인"}]
