# 실전 탭 벽·순풍·트릭룸·설치기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실전 탭이 벽·순풍·트릭룸·설치기를 추적하고, 기술 추천·예상 HP·믿음 상태에 반영하고, 필드 막대로 보여 준다.

**Architecture:** 배틀 상태(`BattleState`)가 사이드 조건과 트릭룸을 Clock/겹 수로 들고 있다. 사용자 입력(실전·복기)은 기술 사건에서 규칙표(`field_moves.py`)로 변화를 만들고, 엔진 로그(자동 대전)는 `-sidestart` 등 실제 로그를 쓴다. 탐색은 `/build_state`에, 예상 HP·데미지 증거는 `/damage` screens에, 등장 효과는 새 `/entry_hazards`에 넘긴다.

**Tech Stack:** Python 3.12(uv, pytest), TS 엔진(Showdown champions, tsx --test), 바닐라 JS

**Spec:** `docs/superpowers/specs/2026-11-22-field-conditions-design.md`

## Global Constraints
- 규칙 값은 엔진 `data/moves.ts`(챔피언스가 바꾸지 않음): 벽 5턴(빛의점토 8), 순풍 4턴, 트릭룸 5턴, 압정 최대 3겹, 독압정 최대 2겹, 오로라베일은 설경일 때만 성공
- 시계 의미는 날씨와 같다: `BattleState.end_turn(n)`, `Clock.remaining(turn)` (끝나는 턴 하한·상한)
- 상대 쪽 벽: 도구를 모르면 5~8, 빛의점토면 8, 다른 도구가 드러났으면 5. 내 쪽은 세트 도구로 확정
- 설치기 등장 자동 HP는 `note="hazard"`를 단다 (세션의 데미지 증거는 note가 있는 hp 사건을 이미 건너뛴다)
- 사용자 문구 한국어, 조건 이름은 DB의 기술 한국어 이름(`ko("moves", id)`)

## Review Focus
- 상대 벽(도구 모름)은 5~8로 보이고 8턴이 지나야 사라진다 (Task 1 `test_opp_screen_range_and_expiry`)
- 트릭룸을 켜진 상태에서 다시 쓰면 끝난다 (Task 2 `test_trickroom_reuse_ends`)
- 비행·부유 포켓몬은 압정·독압정 등장 효과가 없다 (Task 4 엔진 테스트 `entry hazards grounded only`)
- 트릭룸·순풍 아래 자동 대전에서 믿음 모순이 늘지 않는다 (Task 5 `test_order_evidence_under_trick_room`)
- 복기(기록 재생)도 같은 규칙으로 필드를 만든다 (Task 2 규칙이 state에 있어 자동, Task 6 `test_review_replays_field`)

---

### Task 1: 배틀 상태에 사이드 조건·트릭룸

**Files:**
- Modify: `agent/pokechamp/battle/state.py` (Side, BattleState.apply, start_turn, summary)
- Test: `agent/tests/test_field.py` (새 파일)

**Interfaces:**
- Produces:
  - 사건: `Event("sidestart", side, value=cond, amount=n)`, `Event("sideend", side, value=cond)`, `Event("room", None, value="trickroom"|"none", amount=n)`, `Event("courtchange", None)`
    - `amount>0`: 벽·순풍·트릭룸이면 남은 턴을 그 값으로 확정(직접 켜기), 설치기면 겹 수를 그 값으로 확정. `amount=0`이면 규칙대로
  - `Side.timed: dict[str, Clock]` (reflect, lightscreen, auroraveil, tailwind), `Side.hazards: dict[str, int]` (stealthrock, spikes, toxicspikes, stickyweb)
  - `BattleState.trickroom: Clock`
  - 상수 `TIMED = {"reflect": 5, "lightscreen": 5, "auroraveil": 5, "tailwind": 4}`, `HAZARD_MAX = {"stealthrock": 1, "spikes": 3, "toxicspikes": 2, "stickyweb": 1}`, `SCREENS = {"reflect", "lightscreen", "auroraveil"}`

- [ ] **Step 1: 실패하는 테스트** (`BattleState(my_team, opp_species)` 직접 만들고 `apply`로 사건을 넣는다)
```python
def test_my_reflect_five_turns_light_clay_eight(): ...
    # 턴 1에 내 쪽(도구 없음) sidestart reflect → me.timed["reflect"].remaining(1) == (5, 5); 턴 6 시작 뒤 사라짐
    # 도구 lightclay인 세트면 remaining(1) == (8, 8)
def test_opp_screen_range_and_expiry():
    # 상대(도구 모름) lightscreen 턴 1 → remaining(1) == (5, 8); 턴 6 시작 뒤에도 남음(0~3), 턴 9 시작 뒤 사라짐
def test_same_screen_twice_ignored(): ...   # 켜진 상태의 같은 벽 sidestart는 시계를 바꾸지 않는다
def test_hazard_layers_capped():
    # spikes 4번 → 3, toxicspikes 3번 → 2, stealthrock 2번 → 1; sideend spikes → 키 없음
def test_manual_turns_amount():   # sidestart tailwind amount=2 → remaining == (2, 2)
def test_trickroom_clock_and_end():  # room trickroom → remaining == (5, 5); room none → kind "none"
def test_courtchange_swaps_sides():  # me reflect + opp spikes 2 → courtchange → opp reflect, me spikes 2
```
- [ ] **Step 2:** `cd agent && uv run pytest tests/test_field.py -q` → 실패(속성 없음)
- [ ] **Step 3:** 구현. 시계는 `Clock(kind=cond, lo, hi)`, 길이는 `end_turn(n)`. 벽 길이는 sidestart를 받은 편의 출전 포켓몬 도구로 정한다(날씨 바위와 같은 방식: `known = m.item if (sd is self.me or m.item) else ""`; held가 있으면 held 우선). `start_turn`에서 `hi < 새 턴`인 시계를 지운다. summary에 `timed`, `hazards`, `trickroom` 추가
- [ ] **Step 4:** 같은 명령 → 통과, 전체 `uv run pytest -q` 통과
- [ ] **Step 5:** (커밋은 사용자 승인 뒤 한 번에) 원장에 기록

### Task 2: 기술 → 필드 변화 규칙 (사용자 입력 경로)

**Files:**
- Create: `agent/pokechamp/battle/field_moves.py`
- Modify: `agent/pokechamp/battle/state.py` (move 사건에서 규칙 적용), `agent/pokechamp/battle/session.py` (`BattleSession(..., derive_field: bool = True)` → `BattleState`), `agent/pokechamp/battle/agent_play.py`, `agent/pokechamp/battle/belief_check.py` (엔진 로그 경로는 `derive_field=False`)
- Test: `agent/tests/test_field.py`

**Interfaces:**
- Produces: `field_events(side: str, move: str, state: BattleState) -> list[Event]` — 규칙표:
  - 자기 쪽 켜기: reflect, lightscreen, tailwind, auroraveil(날씨가 snow일 때만)
  - 상대 쪽 깔기: stealthrock, spikes, toxicspikes, stickyweb
  - 상대 쪽 벽 지우기: brickbreak, psychicfangs, ragingbull
  - 자기 쪽 설치기 지우기: rapidspin, mortalspin
  - defog: 상대 쪽 벽·설치기 + 자기 쪽 설치기 / tidyup: 양쪽 설치기 / courtchange: `Event("courtchange")`
  - trickroom: 켜져 있으면 `room none`, 아니면 `room trickroom`
- `BattleState(..., derive_field=True)`: move 사건을 처리한 직후 `field_events` 결과를 같은 apply 안에서 처리한다

- [ ] **Step 1: 실패하는 테스트**
```python
def test_move_input_sets_conditions():   # me reflect → me.timed; opp stealthrock → me.hazards["stealthrock"] == 1
def test_removal_moves():                # brickbreak(me) → opp 벽 사라짐; rapidspin(me) → me 설치기 사라짐; defog(me) → opp 벽·설치기 + me 설치기 사라짐
def test_auroraveil_needs_snow():        # 날씨 없음이면 무시, snow면 켜짐
def test_trickroom_reuse_ends():         # trickroom 두 번 → kind "none"
def test_engine_log_path_does_not_derive():  # derive_field=False면 move 사건만으로는 아무것도 안 켜진다
```
- [ ] **Step 2:** 실패 확인
- [ ] **Step 3:** 구현 (규칙표는 모듈 상수 dict로, 엔진 `data/moves.ts`의 대상 목록 그대로)
- [ ] **Step 4:** 통과 + 전체 테스트

### Task 3: 엔진 로그 → 사건 (LogConverter)

**Files:**
- Modify: `agent/pokechamp/battle/events.py`
- Test: `agent/tests/test_field.py`

**Interfaces:**
- Consumes: Task 1 사건
- `-sidestart|p1: …|Reflect` (또는 `move: Reflect`) → `sidestart`, `-sideend` → `sideend`, `-fieldstart|move: Trick Room` → `room trickroom`, `-fieldend|move: Trick Room` → `room none`, `-swapsideconditions` → `courtchange`. side는 LogConverter의 내 쪽(`me`) 기준으로 me/opp. 조건 이름은 `to_id`

- [ ] **Step 1:** `test_log_converter_field_lines` — 위 로그 줄들을 넣어 사건 종류·편·값 확인
- [ ] **Step 2:** 실패 확인 → **Step 3:** 구현 → **Step 4:** 통과 + 전체 테스트

### Task 4: 엔진 — `/build_state` 조건 주입, `/entry_hazards`

**Files:**
- Modify: `engine/src/engine.ts` (BuildStateReq, buildState, 새 entryHazards), `engine/src/server.ts` (경로 등록)
- Test: `engine/test/field.test.ts` (새 파일, 기존 test 파일 방식)

**Interfaces:**
- `BuildStateReq.sideConditions?: { p1?: Record<string, number>; p2?: Record<string, number> }` — 벽·순풍은 남은 턴, 설치기는 겹 수. `BuildStateReq.trickRoom?: number` (남은 턴)
- `POST /entry_hazards` batch 항목 `{ set, mega?: boolean, hazards: Record<string, number> }` → `{ damage: number, maxhp: number, status: string, boosts: Record<string, number> }` (그 포켓몬이 그 설치기 위로 나왔을 때)

- [ ] **Step 1: 실패하는 테스트**
  - `build_state side conditions`: sideConditions {p2: {reflect: 3, spikes: 2}}, trickRoom 2 → 직렬화 상태에서 p2 reflect duration 3, spikes layers 2, trickroom duration 2
  - `entry hazards stealth rock`: 리자몽(불꽃·비행) stealthrock 1 → damage == floor(maxhp/2)
  - `entry hazards grounded only`: 비행 타입 spikes 3 → damage 0, 땅 위 포켓몬 spikes 1 → floor(maxhp/8)
  - `entry hazards toxic spikes`: toxicspikes 2 → status "tox", 독 타입이면 status "" (흡수)
  - `entry hazards sticky web`: stickyweb → boosts.spe == -1
- [ ] **Step 2:** `cd engine && npm test` → 실패
- [ ] **Step 3:** 구현. 사이드 조건은 `side.addSideCondition(id, side.active[0])`(설치기는 겹 수만큼 반복), 시계는 `side.sideConditions[id].duration = n`. 트릭룸은 `field.addPseudoWeather('trickroom', source)` 뒤 duration. 등장 효과는 2마리 팀(앞: 아무 포켓몬, 뒤: 대상)에 설치기를 깔고 대상을 실제 교체로 내보낸 뒤 HP 차이·상태·랭크를 잰다
- [ ] **Step 4:** `npm test`, `npm run typecheck` 통과, 엔진 서버 재시작

### Task 5: 탐색·믿음·예상 HP에 반영

**Files:**
- Modify: `agent/pokechamp/battle/search.py` (determinize 상태에 sideConditions·trickRoom), `agent/pokechamp/battle/session.py` (`my_speed`에 순풍, `_field(defender_side)`에 screens, observe_order에 tailwind·trick room), `agent/pokechamp/battle/belief.py` (`observe_order(..., opp_tailwind: bool = False, my_tailwind: bool = False, trick_room: bool = False)`), `agent/pokechamp/battle/live.py` (estimate에 맞는 쪽 screens)
- Test: `agent/tests/test_field.py`, 엔진 필요한 것은 `conn`/`app` fixture로 skip

**Interfaces:**
- Consumes: Task 1 상태, Task 4 API
- `BattleSession._field(defender: str | None = None) -> dict` — defender("me"|"opp")가 있으면 그 쪽 벽을 `screens`로 추가
- 남은 턴 전달은 하한(`remaining()[0]`, 최소 1)

- [ ] **Step 1: 실패하는 테스트**
  - `test_determinize_passes_field_conditions`: 상태에 me reflect·opp spikes 2·trickroom → Searcher가 만든 build 요청에 sideConditions/trickRoom
  - `test_order_evidence_under_trick_room`: 트릭룸 아래 느린 상대가 먼저 움직였다 → 실제 스피드 파티클이 지워지지 않는다(가중치 > 0)
  - `test_order_evidence_tailwind`: 상대 순풍 → 상대 스피드 2배로 비교
  - `test_damage_evidence_uses_screens`: 상대 쪽 리플렉터일 때 pending field에 screens ["reflect"]
  - `test_estimate_applies_defender_screens` (엔진): 상대 리플렉터면 예상 데미지가 줄어든다
- [ ] **Step 2:** 실패 확인 → **Step 3:** 구현 → **Step 4:** 통과 + 전체 테스트
- [ ] **Step 5:** 자동 대전 회귀 확인: `uv run python -c` 로 `agent_play.evaluate(conn, client, 4, "fieldcheck")` 실행 → errors 0, 모순 경고 없음

### Task 6: 실전 — 등장 효과 자동, 직접 켜기 API, view

**Files:**
- Modify: `agent/pokechamp/battle/live.py` (`_switch_in`에서 설치기 등장 효과, `set_field`, `view()["field"]`), `agent/pokechamp/web/server.py` (`POST /api/live/field`)
- Test: `agent/tests/test_live.py`

**Interfaces:**
- `LiveBattle.set_field(side: str, cond: str, on: bool, turns: int = 0, layers: int = 0) -> dict` (view 반환). side: "me"|"opp"|"field"(트릭룸)
- `view()["field"] = {"me": [Chip], "opp": [Chip], "center": [Chip]}`, `Chip = {"id", "name", "remaining": [lo, hi] | None, "layers": int | None}` — center에 날씨·필드·트릭룸
- 등장 효과: 설치기가 있는 쪽에 교체로 나오면 `/entry_hazards` 결과로 `Event("hp", side, hp=…, note="hazard")`, 상태 `Event("status", …)`, 스피드 `Event("boost", stat="spe", amount=-1, note="hazard")`. 상대는 믿음 가중치 1위 파티클 세트

- [ ] **Step 1: 실패하는 테스트**
  - `test_live_hazard_entry_damage`: 상대가 스텔스록을 깔고 내가 교체 → 내 HP가 자동으로 줄고 view field.me에 stealthrock
  - `test_live_set_field_manual`: set_field("opp", "reflect", True, turns=3) → view field.opp reflect remaining [3, 3]; on=False → 사라짐
  - `test_review_replays_field`: 리플렉터를 쓴 기록을 복기 재생 → 상태에 reflect
- [ ] **Step 2:** 실패 확인 → **Step 3:** 구현 → **Step 4:** 통과 + 전체 테스트

### Task 7: 필드 막대 화면

**Files:**
- Modify: `agent/pokechamp/web/static/app.js` (renderLive의 `field` 한 줄 → 필드 막대, 칩 클릭 끄기, [+ 추가]), `agent/pokechamp/web/static/style.css` (`.field-bar`, `.field-chip`)
- Docs: `docs/KNOWN_GAPS.md`(자동 처리 범위: 기술 외 원인은 직접), `docs/progress.json`

- [ ] **Step 1:** 막대: 왼쪽 내 쪽 칩, 가운데 날씨·필드·트릭룸, 오른쪽 상대 칩. 칩 글자 `리플렉터 3턴` / `빛의장막 4~7턴` / `압정뿌리기 ×2`. 칩을 누르면 `confirm` 뒤 `/api/live/field` on=false. [+ 추가]는 쪽·종류 select + 남은 턴(기본 처음 지속 시간)/겹 수 입력
- [ ] **Step 2:** `node --check`, 웹 서버 재시작, 헤드리스 Chrome으로 칩이 있는 실전 화면(1400px·390px) 확인
- [ ] **Step 3:** 문서 갱신, 전체 테스트(`uv run pytest -q`, 엔진 `npm test`)
