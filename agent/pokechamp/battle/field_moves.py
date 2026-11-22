"""기술 → 필드 변화 규칙 (사용자 입력 경로: 실전·복기·텍스트 배틀).

엔진 data/moves.ts를 그대로 옮긴 표다 (챔피언스는 이 기술들을 바꾸지 않는다). 엔진 로그로 도는 자동 대전은
`-sidestart` 같은 실제 로그를 쓰므로 이 규칙을 쓰지 않는다 (BattleState(derive_field=False)).
기술이 맞았다고 본다: 고스트에게 막힌 고속스핀·깨트리기 같은 예외는 화면에서 직접 고친다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pokechamp.battle.events import Event

if TYPE_CHECKING:
    from pokechamp.battle.state import BattleState

HAZARDS = ("spikes", "toxicspikes", "stealthrock", "stickyweb")
SCREENS = ("reflect", "lightscreen", "auroraveil")
SELF_SET = {"reflect", "lightscreen", "auroraveil", "tailwind"}         # 쓴 쪽에 켠다
FOE_SET = set(HAZARDS)                                                  # 상대 쪽에 깐다
FOE_SET_BY_ATTACK = {"ceaselessedge": "spikes", "stoneaxe": "stealthrock"}   # 맞히면 상대 쪽에 까는 공격기
FOE_SCREEN_BREAK = {"brickbreak", "psychicfangs", "ragingbull"}        # 맞는 쪽 벽을 깬다
SELF_HAZARD_CLEAR = {"rapidspin", "mortalspin"}                        # 자기 쪽 설치기를 지운다
DEFOG_FOE = SCREENS + ("safeguard", "mist") + HAZARDS                  # 안개제거: 맞는 쪽
DEFOG_SELF = HAZARDS                                                    # 안개제거: 쓴 쪽


def field_events(side: str, move: str, state: BattleState) -> list[Event]:
    foe = "opp" if side == "me" else "me"
    if move in SELF_SET:
        if move == "auroraveil" and state.weather.kind != "snow":      # 오로라베일은 설경일 때만 성공
            return []
        return [Event("sidestart", side, value=move)]
    if move in FOE_SET:
        return [Event("sidestart", foe, value=move)]
    if move in FOE_SET_BY_ATTACK:
        return [Event("sidestart", foe, value=FOE_SET_BY_ATTACK[move])]
    if move in FOE_SCREEN_BREAK:
        return [Event("sideend", foe, value=c) for c in SCREENS]
    if move in SELF_HAZARD_CLEAR:
        return [Event("sideend", side, value=c) for c in HAZARDS]
    if move == "defog":
        return ([Event("sideend", foe, value=c) for c in DEFOG_FOE] + [Event("sideend", side, value=c) for c in DEFOG_SELF]
                + [Event("terrain", None, value="none")])                # 9세대 안개제거는 필드도 지운다
    if move == "tidyup":
        return [Event("sideend", s, value=c) for s in ("me", "opp") for c in HAZARDS]
    if move == "courtchange":
        return [Event("courtchange", None)]
    if move == "trickroom":
        return [Event("room", None, value="none" if state.trickroom.kind == "trickroom" else "trickroom")]
    return []
