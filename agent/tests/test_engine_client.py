"""엔진 서버가 떠 있을 때만 도는 통합 테스트. 서버가 없으면 건너뛴다."""

import pytest

from pokechamp.engine_client import EngineClient, EngineError

client = EngineClient()


def _server_up() -> bool:
    try:
        client.health()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _server_up(), reason="엔진 서버가 실행 중이 아님 (cd engine && npm run server)")

TEAM = """Garchomp @ Garchompite Z
Ability: Rough Skin
EVs: 32 Atk / 2 HP / 32 Spe
Jolly Nature
- Dragon Claw
- Earthquake
- Rock Slide
- Swords Dance"""


def test_validate_reports_problems_without_blocking():
    r = client.validate_team(TEAM)
    assert r["valid"] is False
    assert any("at least 6" in p for p in r["problems"])


def test_unknown_team_text_is_an_error_not_a_crash():
    results = client.batch("/validate_team", [{"team": TEAM}, {"team": 123}])
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False


def test_bad_url_raises_engine_error():
    with pytest.raises(EngineError):
        EngineClient("http://127.0.0.1:1").validate_team(TEAM)
