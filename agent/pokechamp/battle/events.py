"""배틀에서 관찰한 사건 (PROJECT_PLAN 9.1).

사용자가 입력한 문장(input/battle_text.py)과 엔진 로그(from_showdown_log, 검증·자기 대전용)가 같은 사건 목록으로 바뀐다.
사건은 일어난 순서대로 둔다. 기술을 쓴 쪽의 순서가 곧 행동 순서다.

side: "me"(나) / "opp"(상대) / None(날씨·필드처럼 양쪽 공통).
포켓몬은 기본 폼 id로 가리킨다(메가진화해도 같은 id). 메가 여부는 상태가 따로 든다.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from pokechamp.data.static.names import to_id

KINDS = ("move", "switch", "mega", "hp", "faint", "status", "cure", "boost", "clearboost", "setboost", "transform", "item", "ability",
         "first", "crit", "miss", "weather", "terrain", "fail", "immune", "turn")
STATUSES = ("brn", "par", "slp", "frz", "psn", "tox")
STATS = ("atk", "def", "spa", "spd", "spe", "accuracy", "evasion")
WEATHERS = {"SunnyDay": "sun", "RainDance": "rain", "Sandstorm": "sand", "Snowscape": "snow", "Snow": "snow",
            "DesolateLand": "sun", "PrimordialSea": "rain", "none": "none"}
TERRAINS = {"Electric Terrain": "electric", "Grassy Terrain": "grassy", "Psychic Terrain": "psychic",
            "Misty Terrain": "misty"}


class SpeciesResolver:
    """엔진·사용자가 쓰는 종 이름 → 허용 목록의 기본 폼 id.
    - 메가 폼 → 메가 전 폼 (Charizard-Mega-Y → charizard)
    - 배틀 중 폼 변화·외형만 다른 폼 → 허용 목록에 있는 같은 종 (Mimikyu-Busted → mimikyu, Vivillon-Sun → vivillonfancy)"""

    def __init__(self, conn):
        from pokechamp.data.db import active_version
        v = active_version(conn)
        vid = v["id"] if v else -1
        self.mega = {r[0]: r[1] for r in conn.execute(
            "SELECT mega_species_id, base_species_id FROM mega_forms WHERE version_id=?", (vid,))}
        self.known: set[str] = set()
        self.by_base: dict[str, str] = {}
        self.families: dict[str, str] = {}
        for r in conn.execute("SELECT id, base_species FROM species WHERE version_id=? AND is_mega=0 ORDER BY form_index",
                              (vid,)):
            self.known.add(r[0])
            self.by_base.setdefault(to_id(r[1]), r[0])
            self.families[r[0]] = to_id(r[1])

    def family(self, sp: str) -> str:
        """같은 종의 폼끼리 같은 값 (시비꼬의 색 폼처럼 한국어 이름이 같아 이름으로 구분할 수 없는 폼을 묶는다)."""
        i = self(sp)
        return self.families.get(i, i)

    def __call__(self, name: str) -> str:
        i = to_id(name)
        if i in self.mega:
            return self.mega[i]
        if i in self.known:
            return i
        base = to_id(name.split("-")[0])
        if base in self.known:
            return base
        if base in self.by_base:
            return self.by_base[base]
        # 하이픈 없는 id(엔진 상태의 'vivillonsun', 'mimikyubusted')는 가장 긴 기본 종 이름 접두어로 찾는다
        cands = [b for b in self.by_base if i.startswith(b)]
        return self.by_base[max(cands, key=len)] if cands else i


@dataclass
class Event:
    kind: str
    side: str | None = None
    species: str = ""          # 기본 폼 id (교체·기절 대상, 비면 그쪽 출전 포켓몬)
    move: str = ""             # 기술 id
    item: str = ""             # 도구 id
    ability: str = ""          # 특성 id
    hp: float | None = None    # HP % (0~100). 상대는 게임처럼 정수 %
    hp_abs: int | None = None  # 내 포켓몬의 정확한 HP (알면)
    hp_max: int | None = None
    status: str = ""
    stat: str = ""
    amount: int = 0
    value: str = ""            # 날씨/필드 id ("none"이면 끝남), 도구 소모면 "consumed"
    note: str = ""             # 원인 등 참고 (예: "[from] item: Leftovers")

    def key(self) -> tuple:
        """파서 왕복 비교용 (참고 note는 뺀다)."""
        d = asdict(self)
        d.pop("note")
        if self.side == "opp" and self.hp is not None:
            d["hp"] = int(self.hp)
        return tuple(sorted(d.items()))

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in ("", None, 0) or k == "kind"}


@dataclass
class Turn:
    number: int
    events: list[Event] = field(default_factory=list)


# ---------------- Showdown 로그 → 사건 (p1 시점) ----------------

def _ident(s: str) -> tuple[str, str]:
    side, _, name = s.partition(": ")
    return side[:2], name


def _hp(cond: str) -> tuple[int | None, int | None, str]:
    parts = cond.split()
    status = parts[1] if len(parts) > 1 else ""
    if parts and parts[0] == "0":
        return 0, None, "fnt"
    m = re.match(r"(\d+)/(\d+)", parts[0] if parts else "")
    return (int(m.group(1)), int(m.group(2)), status) if m else (None, None, status)


def from_showdown_log(lines: list[str], base_of: Callable[[str], str], me: str = "p1") -> list[Turn]:
    """Showdown 프로토콜 로그 전체를 플레이어(me) 시점의 턴별 사건으로 바꾼다."""
    conv = LogConverter(base_of, me)
    turns: list[Turn] = [Turn(0)]
    for e in conv.feed(lines):
        if e.kind == "turn":
            turns.append(Turn(e.amount))
        else:
            turns[-1].events.append(e)
    return turns


class LogConverter:
    """Showdown 프로토콜 로그를 조각 단위로 받아 플레이어(me) 시점의 사건으로 바꾼다. 상대 HP는 공개 값(%)만 쓴다.
    base_of: 엔진 종 이름 → 기본 폼 id (메가 폼 'Charizard-Mega-Y' → 'charizard'). 턴 경계는 Event("turn", amount=턴 번호)."""

    def __init__(self, base_of: Callable[[str], str], me: str = "p1"):
        self.base_of, self.me = base_of, me
        self.names: dict[tuple[str, str], str] = {}     # (side, 별명) → 기본 폼 id

    def feed(self, lines: list[str]) -> list[Event]:  # noqa: C901 (프로토콜 분기)
        me, names, base_of = self.me, self.names, self.base_of
        out: list[Event] = []
        side_of = lambda s: "me" if s == me else "opp"   # noqa: E731

        def mon(ident: str) -> tuple[str, str]:
            side, nick = _ident(ident)
            return side_of(side), names.get((side, nick), to_id(nick))

        add = out.append
        i = 0
        while i < len(lines):
            line = lines[i]
            step = 1
            if line.startswith("|split|"):
                side = line.split("|")[2]
                line = lines[i + 1] if side == me else (lines[i + 2] if i + 2 < len(lines) else "")
                step = 3
            i += step
            p = line.split("|")
            if len(p) < 2:
                continue
            cmd, args = p[1], p[2:]
            src = next((a for a in args if a.startswith("[from]")), "")
            if cmd == "turn":
                add(Event("turn", amount=int(args[0])))
            elif cmd in ("switch", "drag"):
                side, nick = _ident(args[0])
                sp = base_of(args[1].split(",")[0])
                names[(side, nick)] = sp
                hp, mx, status = _hp(args[2])
                e = Event("switch", side_of(side), species=sp)
                add(e)
                add(_hp_event(side_of(side), sp, hp, mx))
            elif cmd == "-mega":
                s, sp = mon(args[0])
                add(Event("mega", s, species=sp))       # 메가스톤은 종으로 정해진다 (mega_forms)
            elif cmd == "move":
                if src:                                  # 매직미러·댄서 등으로 대신 나간 기술은 기술 목록의 증거가 아니다
                    continue
                s, sp = mon(args[0])
                add(Event("move", s, species=sp, move=to_id(args[1])))
            elif cmd in ("-damage", "-heal", "-sethp"):
                s, sp = mon(args[0])
                hp, mx, status = _hp(args[1])
                if status == "fnt":
                    hp = 0
                e = _hp_event(s, sp, hp, mx)
                e.note = src
                add(e)
                # 도구·특성의 주인: '[of]'가 있으면 그쪽 (울퉁불퉁멧·까칠한피부 피해는 맞은 쪽이 아니라 상대의 것)
                of = next((a for a in args if a.startswith("[of] ")), "")
                os_, osp = mon(of[5:]) if of else (s, sp)
                m = re.match(r"\[from\] item: (.+)", src)
                if m:
                    add(Event("item", os_, species=osp, item=to_id(m.group(1))))
                m = re.match(r"\[from\] ability: (.+)", src)
                if m:
                    add(Event("ability", os_, species=osp, ability=to_id(m.group(1))))
            elif cmd == "faint":
                s, sp = mon(args[0])
                add(Event("faint", s, species=sp))
            elif cmd == "-status":
                s, sp = mon(args[0])
                add(Event("status", s, species=sp, status=args[1]))
            elif cmd == "-curestatus":
                s, sp = mon(args[0])
                add(Event("cure", s, species=sp, status=args[1]))
            elif cmd in ("-boost", "-unboost"):
                s, sp = mon(args[0])
                amt = int(args[2]) * (1 if cmd == "-boost" else -1)
                if amt:
                    add(Event("boost", s, species=sp, stat=args[1], amount=amt))
            elif cmd == "-clearnegativeboost":
                s, sp = mon(args[0])
                add(Event("clearboost", s, species=sp, value="negative"))
            elif cmd == "-clearboost":
                s, sp = mon(args[0])
                add(Event("clearboost", s, species=sp, value="all"))
            elif cmd == "-clearallboost":
                add(Event("clearboost", "me", value="all"))
                add(Event("clearboost", "opp", value="all"))
            elif cmd == "-transform":
                s, sp = mon(args[0])
                add(Event("transform", s, species=sp))
            elif cmd == "-setboost":
                s, sp = mon(args[0])
                add(Event("setboost", s, species=sp, stat=args[1], amount=int(args[2])))
            elif cmd == "replace":                       # 일루전이 풀림: 실제 포켓몬으로 바꾼다
                side, nick = _ident(args[0])
                sp = base_of(args[1].split(",")[0])
                names[(side, nick)] = sp
                add(Event("switch", side_of(side), species=sp, value="replace"))
            elif cmd in ("-item", "-enditem"):
                s, sp = mon(args[0])
                add(Event("item", s, species=sp, item=to_id(args[1]), value="consumed" if cmd == "-enditem" else "",
                          note=src))
            elif cmd == "-ability":
                s, sp = mon(args[0])
                add(Event("ability", s, species=sp, ability=to_id(args[1]), note=src))
            elif cmd == "-crit":
                s, sp = mon(args[0])                    # 급소를 맞은 쪽 → 공격한 쪽은 반대편
                add(Event("crit", "opp" if s == "me" else "me"))
            elif cmd == "-miss":
                s, sp = mon(args[0])
                add(Event("miss", s, species=sp))
            elif cmd == "-weather":
                if "[upkeep]" in args:
                    continue
                _source_ability(args, src, mon, add)
                add(Event("weather", None, value=WEATHERS.get(args[0], to_id(args[0]))))
            elif cmd == "-fieldstart":
                t = args[0].replace("move: ", "")
                if t in TERRAINS:
                    _source_ability(args, src, mon, add)
                    add(Event("terrain", None, value=TERRAINS[t]))
            elif cmd == "-fieldend":
                t = args[0].replace("move: ", "")
                if t in TERRAINS:
                    add(Event("terrain", None, value="none"))
            elif cmd == "-fail":
                s, sp = mon(args[0])
                add(Event("fail", s, species=sp))
            elif cmd == "-immune":
                s, sp = mon(args[0])
                add(Event("immune", s, species=sp))
            elif cmd == "detailschange":
                side, nick = _ident(args[0])
                names.setdefault((side, nick), base_of(args[1].split(",")[0]))
        return out


def _source_ability(args: list[str], src: str, mon, add) -> None:
    """'[from] ability: Sand Stream|[of] p1a: Tyranitar' → 특성 사건을 먼저 낸다 (날씨 지속 턴 추적에 쓴다)."""
    m = re.match(r"\[from\] ability: (.+)", src)
    of = next((a for a in args if a.startswith("[of] ")), "")
    if m and of:
        s, sp = mon(of[5:])
        add(Event("ability", s, species=sp, ability=to_id(m.group(1))))


def _hp_event(side: str, sp: str, hp: int | None, mx: int | None) -> Event:
    if hp is None:
        return Event("hp", side, species=sp)
    if side == "me" and mx and mx != 100:
        return Event("hp", side, species=sp, hp=round(100 * hp / mx, 1), hp_abs=hp, hp_max=mx)
    return Event("hp", side, species=sp, hp=float(hp if mx in (100, None) else int(100 * hp / mx)))
