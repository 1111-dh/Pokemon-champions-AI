# 계산기 탭 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 웹에 '계산기' 탭(데미지: 확정/난수 n타, 스피드: 실수치 비교)을 추가한다.

**Architecture:** n타 확률은 순수 함수(`pokechamp/calc/ko.py`)로 계산하고, 웹 서버가 기존 엔진 API(`/damage`, `/stats`, `/moves_info`)를 불러 그 함수에 넣는다. 화면은 기존 선택 상자(picker)와 `/api/builder/*`, `/api/team/show`를 그대로 쓴다.

**Tech Stack:** Python 3.12(uv, pytest), 바닐라 JS, TS 엔진(Showdown champions)

**Spec:** `docs/superpowers/specs/2026-11-22-calculator-design.md`

## Global Constraints
- 사용자 문구는 한국어, 판단 로직에 LLM 없음, 자동 갱신 없음 (CLAUDE.md)
- 레벨 50, Stat Points: 능력치당 최대 32, 합계 66 (기존 팀 만들기와 같음)
- 메가 전후는 별도 폼: 메가 체크 시 엔진 `mega: true`로 계산
- 상대 기본값은 통계 최빈 세트, 화면에 '통계로 채움' 표시 (조용히 채우지 않는다)
- n은 최대 10, 난수 16단계는 매번 독립·균등

## Review Focus
- 방어측 현재 HP% < 100 → 그 HP 기준으로 n타를 판정해야 한다 (Task 1 테스트 `test_ko_uses_current_hp`, Task 2 테스트)
- 무효(땅 기술 → 비행) → '효과 없음', n타 없음 (Task 1 `test_ko_zero_damage`, Task 2 `test_calc_damage_immune`)
- 변화기 → '변화기라 데미지 없음' (Task 2 `test_calc_damage_status_move`)
- 메가스톤 없이 메가 체크 → 엔진 오류를 그대로 한국어 메시지로 (Task 2 `test_calc_mega_without_stone`)
- 연속기 타수 k → 한 번 사용 = k개 난수의 합 (Task 1 `test_ko_multihit`)

---

### Task 1: n타 확률 함수

**Files:**
- Create: `agent/pokechamp/calc/__init__.py` (빈 파일), `agent/pokechamp/calc/ko.py`
- Test: `agent/tests/test_calc.py`

**Interfaces:**
- Produces: `ko_chances(rolls: list[int], hp: int, hits: int = 1, max_n: int = 10) -> dict`
  반환 `{"random_n": int|None, "random_p": float|None, "guaranteed_n": int|None, "per_n": [{"n": int, "p": float}], "label": str}`
  - `random_n`: P(n번 합 ≥ hp) > 0인 가장 작은 n, `guaranteed_n`: P = 1인 가장 작은 n
  - `label`: `"확정 2타"` (random_n == guaranteed_n) / `"난수 2타 (62.5%) · 확정 3타"` / `"난수 9타 (12.3%)"`(확정이 max_n 밖) / `"10타 안에 못 잡음"` / `"데미지 없음"`(모든 값 0)
  - 확률 표시는 소수 첫째 자리 %

- [ ] **Step 1: 실패하는 테스트**
```python
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
```
- [ ] **Step 2: 실행해서 실패 확인** — `cd agent && uv run pytest tests/test_calc.py -q` → ImportError
- [ ] **Step 3: 구현** — 한 번 사용 분포 = 16값 균등 분포를 hits번 합성곱(딕셔너리 합→확률). n번 합 분포를 n=1..max_n으로 늘리며 P(합 ≥ hp)를 기록하고, P=1이 되면 멈춘다. 합이 hp 이상인 값은 hp로 묶어 상태 수를 줄인다.
- [ ] **Step 4: 통과 확인** — 같은 명령, 7 passed
- [ ] **Step 5: 커밋** — `feat(calc): n타 확률 계산`

### Task 2: 계산 API

**Files:**
- Modify: `engine/src/engine.ts` (`movesInfo`에 `hits: number[]` 추가 — `m.multihit`가 숫자면 `[n]`, 배열 `[2,5]`면 `[2,3,4,5]`, 없으면 `[1]`)
- Modify: `agent/pokechamp/web/server.py` (`team_save_sets`의 id→영어 이름 변환을 `_set_from_ids(s: dict) -> dict`로 빼서 함께 쓴다)
- Test: `agent/tests/test_calc.py` (엔진 없으면 skip — `test_live.py`의 `conn` fixture 방식)

**Interfaces:**
- Consumes: `ko_chances` (Task 1)
- Produces:
  - `POST /api/calc/damage` 요청 `{"attacker": Side, "defender": Side, "move": id, "hits": int, "field": {"weather": id|"", "terrain": id|""}}`
    `Side = {"set": {species, item, ability, nature, evs}(id), "mega": bool, "boosts": {atk,def,spa,spd}, "status": ""|"brn"|..., "hp_pct": int}`
    응답 `{"move", "type", "category", "attacker": {species, stats}, "defender": {species, maxhp, hp, stats}, "immune", "status", "effectiveness",
           "normal": {"min", "max", "min_pct", "max_pct", "rolls", "ko"}, "crit": {같은 모양}, "hits", "hit_options", "notes": [str]}`
    (`ko`는 `ko_chances` 결과, `hp = max(1, floor(maxhp*hp_pct/100))`, `notes`에 계산에 넣지 않는 것: 벽·회복·잔여 데미지)
  - `POST /api/calc/stats` 요청 `{"set": Side.set, "mega": bool}` → 엔진 `/stats` 결과 그대로 (hp, atk, def, spa, spd, spe, types, ability, species)
  - 날씨 id: `sunnyday`·`raindance`·`sandstorm`·`snowscape`, 필드 id: `electricterrain`·`grassyterrain`·`psychicterrain`·`mistyterrain`

- [ ] **Step 1: 실패하는 테스트**
```python
def _side(species, **kw):
    s = {"species": species, "item": kw.pop("item", ""), "ability": kw.pop("ability", ""), "nature": kw.pop("nature", ""),
         "evs": kw.pop("evs", {})}
    return {"set": s, **kw}

def test_calc_damage_basic(app):
    r = app.calc_damage({"attacker": _side("garchomp", nature="adamant", evs={"atk": 32}), "defender": _side("heatran"),
                         "move": "earthquake"})
    assert len(r["normal"]["rolls"]) == 16 and r["effectiveness"] == 4 and r["normal"]["ko"]["label"]

def test_calc_damage_immune(app):
    r = app.calc_damage({"attacker": _side("garchomp"), "defender": _side("corviknight"), "move": "earthquake"})
    assert r["immune"] and r["normal"]["ko"]["label"] == "데미지 없음"

def test_calc_damage_status_move(app):
    assert app.calc_damage({"attacker": _side("garchomp"), "defender": _side("heatran"), "move": "swordsdance"})["status"]

def test_calc_hp_pct_and_multihit(app):
    full = app.calc_damage({"attacker": _side("garchomp"), "defender": _side("heatran"), "move": "earthquake"})
    half = app.calc_damage({"attacker": _side("garchomp"), "defender": _side("heatran", hp_pct=50), "move": "earthquake"})
    assert half["defender"]["hp"] < full["defender"]["hp"]
    dw = app.calc_damage({"attacker": _side("dragonite"), "defender": _side("garchomp"), "move": "dualwingbeat"})
    assert dw["hit_options"] == [2] and dw["hits"] == 2

def test_calc_mega_without_stone(app):
    with pytest.raises(Exception, match="메가진화할 수 없다"):
        app.calc_damage({"attacker": _side("garchomp", mega=True), "defender": _side("heatran"), "move": "earthquake"})

def test_calc_stats_speed(app):
    a = app.calc_stats({"set": {"species": "garchomp", "nature": "jolly", "evs": {"spe": 32}}})
    b = app.calc_stats({"set": {"species": "garchomp", "nature": "adamant", "evs": {"spe": 32}}})
    assert a["spe"] > b["spe"]
```
  (`app` fixture: 엔진이 없으면 skip, `App()` 반환. 위 종이 현재 허용 목록에 없으면 목록에 있는 종으로 바꾼다)
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_calc.py -q` → AttributeError `calc_damage`
- [ ] **Step 3: 구현** — `App.calc_damage(b)`, `App.calc_stats(b)`, `post_routes`에 두 경로 등록. `hits`가 없으면 `hit_options[0]`. 엔진 서버 재시작(`npm run server`) 후 확인
- [ ] **Step 4: 통과 확인** — 전체 `uv run pytest -q`, 엔진 `npm run typecheck`·`npm test`
- [ ] **Step 5: 커밋** — `feat(calc): 데미지·실수치 계산 API`

### Task 3: 계산기 탭 화면

**Files:**
- Modify: `agent/pokechamp/web/static/index.html` (탭 버튼 `data-tab="calc"`를 실전 다음에, `<section id="tab-calc">`)
- Modify: `agent/pokechamp/web/static/app.js` (`// ---------------- 계산기 ----------------` 구역, `showTab` 목록 불러오기에 `calc: loadCalc`)
- Modify: `agent/pokechamp/web/static/style.css` (`.calc-*`)

**Interfaces:**
- Consumes: `/api/builder/options`, `/api/builder/species`(popular: items/abilities/natures/spreads/moves), `/api/teams`, `/api/team/show`(sets), `/api/calc/damage`, `/api/calc/stats`, 기존 `pickerBtn`·`openPicker`·`withPopular`·`speciesData`·`builderOptions`
- 상태: `calc = { sides: {me: Side, opp: Side}, dir: "me"|"opp", move, hits, weather, terrain, filled: {me: bool, opp: bool} }`

- [ ] **Step 1: 화면** — 양쪽 카드(포켓몬·성격·도구·특성 picker, SP H·A·B·C·D·S 숫자칸 + 실수치, 메가 체크(메가스톤을 들었을 때만), 랭크 공·방·특공·특방(−6~+6 select), 상태이상, HP%). 내 쪽 '내 팀에서'(팀 select → 멤버 버튼). 상대 쪽은 포켓몬을 고르면 popular 첫 값과 spreads[0]으로 채우고 '통계로 채움' 표시(직접 바꾸면 표시를 끔). 가운데 '⇄ 공격 방향 바꾸기'.
- [ ] **Step 2: 결과** — 위: 스피드 한 줄(`내 OO 152 vs 상대 OO 145 → 내가 빠름`/`동속`). 아래: 기술 picker(공격측 세트 기술 먼저, 배울 수 있는 기술 전체), 타수(select, `hit_options`가 2개 이상일 때만), 날씨·필드 select, 결과(`85~101 (48.3~57.3%)`, ko.label, 급소 한 줄, `<details>` 16단계 난수, notes). 입력이 바뀌면 300ms 뒤 다시 계산.
- [ ] **Step 3: 확인** — `node --check`, 웹 서버 재시작, 헤드리스 Chrome으로 1400px·390px 화면을 찍어 확인
- [ ] **Step 4: 문서와 커밋** — `docs/KNOWN_GAPS.md`에 '계산기 범위'(벽·n타 사이 회복·잔여 데미지 미반영) 한 줄, `docs/progress.json` 단계 5에 계산기 항목 추가, `feat(web): 계산기 탭 (데미지 확정/난수 n타, 스피드 비교)`
