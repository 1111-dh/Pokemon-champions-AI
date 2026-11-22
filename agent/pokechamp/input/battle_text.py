"""배틀 중 텍스트 입력 규칙 파서 (PROJECT_PLAN 9.1).

한 턴의 입력은 "/" 또는 줄바꿈으로 구분한 문장들이다. 문장마다 "상대"/"내"로 시작해 누구의 일인지 밝힌다.
예: "상대 선공 / 상대 지진 / 상대 HP 40% / 내 HP 55%", "상대 메가진화 / 상대 용성군 / 내 누리레느 기절".

지원하는 문장 (포켓몬 이름은 생략하면 그쪽 출전 포켓몬):
  기술        상대 지진                      교체   상대 교체 한카리아스 · 상대 한카리아스로 교체 · 상대 한카리아스 등장
  HP          상대 HP 40% · 내 HP 120/181 · 상대 한카리아스 40%
  메가진화    상대 메가진화                  기절   상대 기절 · 내 누리레느 기절
  상태이상    상대 화상 · 상대 맹독 상태 · 상대 화상 회복   랭크   상대 공격 +1 · 내 스피드 -1
              (기술과 이름이 같은 '맹독'은 '상태'를 붙여야 상태이상으로 읽고, 맨 단어는 기술로 읽는다)
  도구·특성   상대 도구 먹다남은음식 · 상대 도구 자뭉열매 소모 · 상대 특성 위협 · 상대 기합의띠 발동
  랭크 설정   상대 공격 =6 (배북 등) · 상대 하락 랭크 회복 (하얀허브) · 상대 랭크 초기화 (흑안개 등)
  기타        상대 선공 · 상대 급소 · 상대 빗나감 · 상대 실패 · 상대 효과 없음
  날씨·필드   날씨 모래바람 · 날씨 끝 · 필드 그래스필드 · 필드 끝
  벽·설치기   내 쪽 리플렉터 깔림 3턴 · 상대 쪽 압정뿌리기 깔림 2겹 · 내 쪽 독압정 사라짐 · 트릭룸 시작 4턴 · 트릭룸 끝
              · 벽·설치기 맞바꿈 (기술 입력에서 자동으로 생기므로 보통은 직접 쓰지 않는다)

해석하지 못한 문장은 errors로 돌려준다(이 문장만 LLM으로 구조화하는 것은 다음 단계, 스키마 검증 필수).
render()는 사건을 위 형식의 한 줄로 되돌린다 (해석 결과를 사용자에게 다시 보여 줄 때 쓴다).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from pokechamp.battle.events import Event
from pokechamp.data.db import active_version
from pokechamp.data.static.names import to_id
from pokechamp.input.team_text import NameIndex

SIDE_WORDS = {"상대": "opp", "적": "opp", "내": "me", "나": "me", "우리": "me", "아군": "me"}
SIDE_KO = {"opp": "상대", "me": "내"}
STATUS_KO = {"brn": "화상", "par": "마비", "slp": "잠듦", "frz": "얼음", "psn": "독", "tox": "맹독"}
STATUS_ALIASES = {**{v: k for k, v in STATUS_KO.items()}, "잠": "slp", "수면": "slp", "잠듬": "slp", "동상": "frz",
                  "얼음상태": "frz", "맹독상태": "tox"}
STAT_KO = {"atk": "공격", "def": "방어", "spa": "특공", "spd": "특방", "spe": "스피드", "accuracy": "명중", "evasion": "회피"}
STAT_ALIASES = {**{v: k for k, v in STAT_KO.items()}, "특수공격": "spa", "특수방어": "spd", "스핏": "spe", "속도": "spe",
                "명중률": "accuracy", "회피율": "evasion"}
WEATHER_KO = {"sun": "쾌청", "rain": "비", "sand": "모래바람", "snow": "설경", "none": "끝"}
WEATHER_ALIASES = {**{v: k for k, v in WEATHER_KO.items()}, "맑음": "sun", "햇살": "sun", "모래": "sand", "눈": "snow",
                   "설경": "snow", "없음": "none", "끝남": "none", "종료": "none"}
TERRAIN_KO = {"electric": "일렉트릭필드", "grassy": "그래스필드", "psychic": "사이코필드", "misty": "미스트필드", "none": "끝"}
TERRAIN_ALIASES = {**{v: k for k, v in TERRAIN_KO.items()}, "없음": "none", "끝남": "none", "종료": "none"}

_ST = "|".join(sorted(STATUS_ALIASES, key=len, reverse=True))
_STAT = "|".join(sorted(STAT_ALIASES, key=len, reverse=True))


def split_segments(text: str) -> list[str]:
    """줄바꿈, ';', '/'로 문장을 나눈다. 단 "120/181"처럼 숫자 사이의 '/'(HP)와
    "켄타로스 (팔데아의 모습/워터종)"처럼 괄호 안의 '/'(이름)는 자르지 않는다."""
    out, cur, depth = [], [], 0
    for i, ch in enumerate(text):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        cut = ch in "\n;" or (ch == "/" and depth == 0
                               and not (i > 0 and text[i - 1].isdigit() and i + 1 < len(text) and text[i + 1].isdigit()))
        if cut:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [x.strip() for x in out if x.strip()]


@dataclass
class ParseResult:
    events: list[Event]
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


class BattleText:
    def __init__(self, conn: sqlite3.Connection):
        self.idx = NameIndex(conn)
        v = active_version(conn)
        vid = v["id"] if v else -1
        self.ko: dict[str, dict[str, str]] = {}
        for table in ("species", "moves", "items", "abilities"):
            self.ko[table] = {r[0]: r[1] for r in conn.execute(
                f"SELECT id, name_ko FROM {table} WHERE version_id=? AND name_ko IS NOT NULL", (vid,))}
        # 배울 수 없어 기술 표에 없지만 배틀에 나오는 기술
        self.special_moves = {"struggle": "발버둥"}
        self.ko["moves"].update(self.special_moves)
        self.base_of = {r[0]: r[1] for r in conn.execute(
            "SELECT mega_species_id, base_species_id FROM mega_forms WHERE version_id=?", (vid,))}
        self.mega_names: dict[str, str] = {}
        for r in conn.execute("SELECT id, name_en, name_ko FROM species WHERE version_id=? AND is_mega=1", (vid,)):
            for key in (r[0], to_id(r[1]), self.idx.norm(r[2] or "")):
                if key:
                    self.mega_names[key] = self.base_of.get(r[0], r[0])

    # ---------- 이름 ----------
    def lookup(self, table: str, name: str) -> str | None:
        """정확히 일치하는 이름만 받는다 (기술과 포켓몬처럼 범주를 가려야 하므로 비슷한 이름으로 추측하지 않는다)."""
        m = self.idx.maps[table]
        for key in (self.idx.norm(name), to_id(name)):
            if key in m:
                return m[key][0]
        if table == "species":
            return self.mega_names.get(self.idx.norm(name)) or self.mega_names.get(to_id(name))
        if table == "moves":
            return next((k for k, v in self.special_moves.items() if name.strip() in (v, k)), None)
        return None

    def suggest(self, table: str, name: str) -> str:
        try:
            self.idx.find(table, name)
        except Exception as e:  # noqa: BLE001
            m = re.search(r"\(비슷한 이름: (.+)\)", str(e))
            return f" (비슷한 이름: {m.group(1)})" if m else ""
        return ""

    def name(self, table: str, i: str) -> str:
        return self.ko[table].get(i) or i

    # ---------- 파싱 ----------
    def parse(self, text: str) -> ParseResult:
        events: list[Event] = []
        errors: list[str] = []
        for seg in split_segments(text):
            try:
                e = self.parse_segment(seg)
            except ValueError as ex:
                errors.append(str(ex))
                continue
            events.append(e)
        return ParseResult(events, errors)

    def _species(self, s: str | None, seg: str) -> str:
        if not s:
            return ""
        sp = self.lookup("species", s.strip())
        if not sp:
            raise ValueError(f"'{seg}': 포켓몬 '{s.strip()}'을(를) 모른다{self.suggest('species', s.strip())}")
        return sp

    def parse_segment(self, seg: str) -> Event:  # noqa: C901 (문장 형식 분기)
        parts = seg.split(None, 1)
        side = SIDE_WORDS.get(parts[0]) if parts else None
        rest = (parts[1] if len(parts) > 1 else "").strip() if side else seg.strip()
        if side is None:
            m = re.fullmatch(r"트릭룸\s*(시작|끝)(?:\s*(\d+)\s*턴)?", rest)
            if m:
                return Event("room", None, value="trickroom" if m.group(1) == "시작" else "none", amount=int(m.group(2) or 0))
            if re.fullmatch(r"벽\s*·?\s*설치기\s*맞바꿈", rest):
                return Event("courtchange", None)
            m = re.fullmatch(r"날씨\s*(.+)", rest)
            if m:
                w = WEATHER_ALIASES.get(m.group(1).strip())
                if not w:
                    raise ValueError(f"'{seg}': 날씨 '{m.group(1)}'을(를) 모른다 (쾌청·비·모래바람·설경·끝)")
                return Event("weather", None, value=w)
            m = re.fullmatch(r"필드\s*(.+)", rest)
            if m:
                t = TERRAIN_ALIASES.get(m.group(1).strip().replace(" ", ""))
                if not t:
                    raise ValueError(f"'{seg}': 필드 '{m.group(1)}'을(를) 모른다")
                return Event("terrain", None, value=t)
            raise ValueError(f"'{seg}': '상대' 또는 '내'로 시작해야 한다")
        if not rest:
            raise ValueError(f"'{seg}': 무엇이 일어났는지 없다")
        m = re.fullmatch(r"쪽\s+(.+?)\s+(깔림|사라짐)(?:\s+(\d+)\s*(?:턴|겹))?", rest)
        if m:
            cond = self.lookup("moves", m.group(1)) or to_id(m.group(1))
            return Event("sidestart" if m.group(2) == "깔림" else "sideend", side, value=cond, amount=int(m.group(3) or 0))
        sp_re = r"(?:(.+?)\s+)?"
        simple = {"선공": "first", "급소": "crit", "빗나감": "miss", "빗나갔다": "miss", "실패": "fail",
                  "효과없음": "immune", "메가진화": "mega", "메가": "mega"}
        m = re.fullmatch(sp_re + r"(선공|급소|빗나감|빗나갔다|실패|효과\s*없음|메가진화|메가)", rest)
        if m:
            return Event(simple[m.group(2).replace(" ", "")], side, species=self._species(m.group(1), seg))
        m = re.fullmatch(sp_re + r"(?:HP|hp|체력)\s*(\d+(?:\.\d+)?)\s*%", rest) or re.fullmatch(sp_re + r"(\d+(?:\.\d+)?)\s*%", rest)
        if m:
            return Event("hp", side, species=self._species(m.group(1), seg), hp=float(m.group(2)))
        m = re.fullmatch(sp_re + r"(?:HP|hp|체력)\s*(\d+)\s*/\s*(\d+)", rest)
        if m:
            a, b = int(m.group(2)), int(m.group(3))
            if b <= 0 or a > b:
                raise ValueError(f"'{seg}': HP {a}/{b}가 이상하다")
            return Event("hp", side, species=self._species(m.group(1), seg), hp=round(100 * a / b, 1), hp_abs=a, hp_max=b)
        m = re.fullmatch(sp_re + r"기절", rest)
        if m:
            return Event("faint", side, species=self._species(m.group(1), seg))
        m = re.fullmatch(r"일루전\s*해제\s+(.+)", rest)
        if m:
            return Event("switch", side, species=self._species(m.group(1), seg), value="replace")
        m = (re.fullmatch(r"교체\s+(.+)", rest) or re.fullmatch(r"(.+?)\s*(?:으로|로)\s*교체", rest)
             or re.fullmatch(r"(.+?)\s+(?:등장|나옴|내보냄)", rest))
        if m:
            return Event("switch", side, species=self._species(m.group(1), seg))
        m = re.fullmatch(sp_re + rf"({_STAT})\s*([+-]\s*\d)", rest)
        if m:
            return Event("boost", side, species=self._species(m.group(1), seg), stat=STAT_ALIASES[m.group(2)],
                         amount=int(m.group(3).replace(" ", "")))
        m = re.fullmatch(sp_re + rf"({_STAT})\s*=\s*([+-]?\d)", rest)
        if m:
            return Event("setboost", side, species=self._species(m.group(1), seg), stat=STAT_ALIASES[m.group(2)],
                         amount=int(m.group(3)))
        m = re.fullmatch(sp_re + r"(?:하락\s*랭크\s*회복|랭크\s*하락\s*회복)", rest)
        if m:
            return Event("clearboost", side, species=self._species(m.group(1), seg), value="negative")
        m = re.fullmatch(sp_re + r"변신", rest)
        if m:
            return Event("transform", side, species=self._species(m.group(1), seg))
        m = re.fullmatch(sp_re + r"랭크\s*초기화", rest)
        if m:
            return Event("clearboost", side, species=self._species(m.group(1), seg), value="all")
        m = re.fullmatch(sp_re + rf"({_ST})\s*(?:회복|나음|풀림)", rest)
        if m:
            return Event("cure", side, species=self._species(m.group(1), seg), status=STATUS_ALIASES[m.group(2)])
        m = re.fullmatch(sp_re + rf"({_ST})(\s*(?:상태|걸림|됨|이 됨))?", rest)
        # "맹독"처럼 기술(Toxic)과 이름이 같은 상태이상은 '상태'를 붙였을 때만 상태이상으로 읽는다
        if m and (m.group(3) or not self.lookup("moves", m.group(2))):
            return Event("status", side, species=self._species(m.group(1), seg), status=STATUS_ALIASES[m.group(2)])
        m = re.fullmatch(sp_re + r"도구\s+(.+?)(\s+(?:소모|사용|발동))?", rest)
        if m:
            it = self.lookup("items", m.group(2))
            if not it:
                raise ValueError(f"'{seg}': 도구 '{m.group(2)}'을(를) 모른다{self.suggest('items', m.group(2))}")
            consumed = bool(m.group(3)) and "소모" in m.group(3)
            return Event("item", side, species=self._species(m.group(1), seg), item=it, value="consumed" if consumed else "")
        m = re.fullmatch(sp_re + r"특성\s+(.+?)(?:\s+발동)?", rest)
        if m:
            ab = self.lookup("abilities", m.group(2))
            if not ab:
                raise ValueError(f"'{seg}': 특성 '{m.group(2)}'을(를) 모른다{self.suggest('abilities', m.group(2))}")
            return Event("ability", side, species=self._species(m.group(1), seg), ability=ab)
        m = re.fullmatch(sp_re + r"(.+?)\s+(?:발동|소모)", rest)
        if m:
            it = self.lookup("items", m.group(2))
            if it:
                return Event("item", side, species=self._species(m.group(1), seg), item=it,
                             value="consumed" if rest.endswith("소모") else "")
            ab = self.lookup("abilities", m.group(2))
            if ab:
                return Event("ability", side, species=self._species(m.group(1), seg), ability=ab)
        mv = self.lookup("moves", rest)
        if mv:
            return Event("move", side, move=mv)
        # "상대 한카리아스 지진": 포켓몬 이름에 공백이 있을 수 있어 모든 공백 위치에서 나눠 본다
        for cut in [i for i, ch in enumerate(rest) if ch == " "]:
            sp, mv = self.lookup("species", rest[:cut]), self.lookup("moves", rest[cut + 1:])
            if sp and mv:
                return Event("move", side, species=sp, move=mv)
        raise ValueError(f"'{seg}': 해석하지 못했다{self.suggest('moves', rest)}")

    # ---------- 되돌리기 ----------
    def render_event(self, e: Event) -> str:
        s = SIDE_KO.get(e.side or "", "")
        sp = f"{self.name('species', e.species)} " if e.species else ""
        k = e.kind
        if k == "move":
            return f"{s} {sp}{self.name('moves', e.move)}"
        if k == "switch":
            if e.value == "replace":
                return f"{s} 일루전 해제 {self.name('species', e.species)}"
            return f"{s} 교체 {self.name('species', e.species)}"
        if k == "hp":
            if e.hp_abs is not None and e.hp_max:
                return f"{s} {sp}HP {e.hp_abs}/{e.hp_max}"
            v = int(e.hp) if e.hp is not None and float(e.hp).is_integer() else e.hp
            return f"{s} {sp}HP {v}%"
        if k == "faint":
            return f"{s} {sp}기절"
        if k == "status":
            return f"{s} {sp}{STATUS_KO.get(e.status, e.status)} 상태"
        if k == "cure":
            return f"{s} {sp}{STATUS_KO.get(e.status, e.status)} 회복"
        if k == "boost":
            return f"{s} {sp}{STAT_KO.get(e.stat, e.stat)} {e.amount:+d}"
        if k == "transform":
            return f"{s} {sp}변신"
        if k == "setboost":
            return f"{s} {sp}{STAT_KO.get(e.stat, e.stat)} ={e.amount}"
        if k == "clearboost":
            return f"{s} {sp}" + ("하락 랭크 회복" if e.value == "negative" else "랭크 초기화")
        if k == "item":
            return f"{s} {sp}도구 {self.name('items', e.item)}" + (" 소모" if e.value == "consumed" else "")
        if k == "ability":
            return f"{s} {sp}특성 {self.name('abilities', e.ability)}"
        if k == "weather":
            return f"날씨 {WEATHER_KO.get(e.value, e.value)}"
        if k == "terrain":
            return f"필드 {TERRAIN_KO.get(e.value, e.value)}"
        if k in ("sidestart", "sideend"):
            unit = "겹" if e.value in ("spikes", "toxicspikes", "stealthrock", "stickyweb") else "턴"
            n = f" {e.amount}{unit}" if k == "sidestart" and e.amount else ""
            return f"{s} 쪽 {self.name('moves', e.value)} {'깔림' if k == 'sidestart' else '사라짐'}{n}"
        if k == "room":
            return f"트릭룸 {'끝' if e.value == 'none' else '시작'}" + (f" {e.amount}턴" if e.value != "none" and e.amount else "")
        if k == "courtchange":
            return "벽·설치기 맞바꿈"
        word = {"first": "선공", "crit": "급소", "miss": "빗나감", "fail": "실패", "immune": "효과 없음", "mega": "메가진화"}
        return f"{s} {sp}{word.get(k, k)}"

    def render(self, events: list[Event]) -> str:
        return " / ".join(self.render_event(e) for e in events if e.kind != "turn")
