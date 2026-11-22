"""배틀 상태 추적 (PROJECT_PLAN 9.1): 관찰한 사건을 순서대로 적용해 양쪽 상태를 유지한다.

- 내 쪽은 세트를 다 안다. 사용자가 입력하지 않아도 되는 값(최대 HP 등)은 세트에서 구한다.
- 상대 쪽은 공개된 것만 든다(종, HP %, 상태이상, 랭크, 쓴 기술, 드러난 도구·특성, 메가 여부). 세트 분포는 믿음 상태(다음 단계)가 든다.
- 날씨·필드는 남은 턴을 함께 추적한다. 특성으로 깐 날씨는 5턴(바위류 도구 8턴), 필드는 5턴(그라운드코트 8턴)이다
  (docs/VERIFIED.md 빠른 상성 v3). 상대가 바위를 들었는지 모르면 남은 턴의 상한·하한을 함께 든다.

apply()는 모순이나 의심스러운 입력을 경고 목록으로 돌려준다(입력을 막지는 않는다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pokechamp.battle.events import Event, Turn
from pokechamp.battle.field_moves import field_events
from pokechamp.data.static.names import to_id

WEATHER_ROCK = {"sun": "heatrock", "rain": "damprock", "sand": "smoothrock", "snow": "icyrock"}
WEATHER_ABILITY = {"drought": "sun", "drizzle": "rain", "sandstream": "sand", "snowwarning": "snow", "orichalcumpulse": "sun",
                   "megasol": None}
TERRAIN_ABILITY = {"electricsurge": "electric", "psychicsurge": "psychic", "grassysurge": "grassy", "mistysurge": "misty",
                   "hadronengine": "electric", "seedsower": "grassy"}
BOOST_KEYS = ("atk", "def", "spa", "spd", "spe", "accuracy", "evasion")
# 사이드 조건 (엔진 data/moves.ts, 챔피언스는 바꾸지 않음): 벽 5턴(빛의점토 8), 순풍 4턴, 트릭룸 5턴, 설치기는 겹 수
TIMED = {"reflect": 5, "lightscreen": 5, "auroraveil": 5, "tailwind": 4}
SCREENS = {"reflect", "lightscreen", "auroraveil"}
HAZARD_MAX = {"stealthrock": 1, "spikes": 3, "toxicspikes": 2, "stickyweb": 1}
TRICK_ROOM_TURNS = 5


@dataclass
class Mon:
    species: str
    set: dict[str, Any] | None = None
    hp: float = 100.0                 # %
    hp_abs: int | None = None
    hp_max: int | None = None
    status: str = ""
    fainted: bool = False
    boosts: dict[str, int] = field(default_factory=lambda: {k: 0 for k in BOOST_KEYS})
    moves: list[str] = field(default_factory=list)       # 상대: 공개된 기술
    item: str = ""                    # 알려진 도구 (내 쪽은 세트의 도구)
    item_consumed: bool = False
    ability: str = ""
    mega: bool = False
    seen: bool = False                # 배틀에 나왔다
    transformed: bool = False         # 변신 중 (상대의 기술을 쓴다)
    last_move: str = ""               # 출전 중 마지막으로 쓴 기술 (구애 고정 판단)
    held: str | None = None           # 트릭·바꿔치기·도둑질 등으로 바뀐 지금 도구 (None = 원래 도구 그대로, "" = 없음)
    hp_estimated: bool = False        # HP가 관찰이 아니라 추정(설치기 등장 데미지)에서 왔다 → 데미지 증거로 쓰지 않는다

    def summary(self) -> dict[str, Any]:
        return {"species": self.species, "hp": self.hp, "hp_abs": self.hp_abs, "status": self.status, "fainted": self.fainted,
                "boosts": {k: v for k, v in self.boosts.items() if v}, "moves": list(self.moves), "item": self.item, "held": self.held,
                "item_consumed": self.item_consumed, "ability": self.ability, "mega": self.mega}


@dataclass
class Clock:
    """날씨·필드가 끝나는 턴(그 턴까지 적용, 그 턴 끝에 끝남)의 하한·상한.
    Showdown: 턴 t에 지속 n턴으로 깔리면 턴 t부터 적용되고 턴 t+n−1 끝에 끝난다. 선봉 등장(0턴)에 깔리면 턴 1부터 센다."""
    kind: str = "none"
    lo: int | None = None
    hi: int | None = None

    def remaining(self, turn: int) -> tuple[int, int] | None:
        """turn 시작 시점의 남은 턴 수(이번 턴 포함). 엔진의 weatherState.duration과 같은 뜻."""
        if self.kind == "none" or self.lo is None:
            return None
        return max(0, self.lo - turn + 1), max(0, (self.hi or self.lo) - turn + 1)



@dataclass
class Side:
    name: str                         # "me" / "opp"
    mons: dict[str, Mon] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)       # 팀 순서 (기본 폼 id)
    active: str | None = None
    mega_used: bool = False
    timed: dict[str, Clock] = field(default_factory=dict)    # 벽·순풍: 조건 id → 끝나는 턴
    hazards: dict[str, int] = field(default_factory=dict)    # 설치기: 조건 id → 겹 수

    def get(self, species: str, create: bool = False) -> Mon | None:
        if species in self.mons:
            return self.mons[species]
        if create:
            self.mons[species] = Mon(species)
            self.order.append(species)
            return self.mons[species]
        return None

    def alive(self) -> list[str]:
        return [s for s in self.order if self.mons[s].seen and not self.mons[s].fainted]


class BattleState:
    def __init__(self, my_team: list[dict[str, Any]], opp_species: list[str], base_of=None, derive_field: bool = True):
        """base_of: 종 이름 → 기본 폼 id 함수(battle.events.SpeciesResolver) 또는 메가 → 기본 폼 사전.
        derive_field: 기술 사건에서 벽·설치기·트릭룸 변화를 만든다 (사용자 입력 경로). 엔진 로그 경로는 False(로그가 알려 준다)."""
        self.base_of = base_of or {}
        self.derive_field = derive_field
        self.me = Side("me")
        for st in my_team:
            sp = self.base(st["species"])
            m = self.me.get(sp, create=True)
            m.set, m.item, m.ability = st, to_id(st.get("item") or ""), to_id(st.get("ability") or "")
        self.opp = Side("opp")
        for sp in opp_species:
            self.opp.get(self.base(sp), create=True)
        self.weather = Clock()
        self.terrain = Clock()
        self.trickroom = Clock()
        self.turn = 0
        self.log: list[Turn] = []
        # 이번 턴에 기절한 편 → 그 편의 다음 교체는 턴 끝 처리 뒤의 교체(9세대는 기절 교체를 턴 끝에 한다)
        self.fainted_this_turn: set[str] = set()
        self.after_residual = False      # 턴 끝 교체로 나온 포켓몬이 까는 날씨·필드는 다음 턴부터 센다
        self.clock_from_ability = False
        self._next_clock_flag = False

    def base(self, sp: str) -> str:
        return self.base_of(sp) if callable(self.base_of) else self.base_of.get(to_id(sp), to_id(sp))

    def side(self, s: str | None) -> Side:
        return self.me if s == "me" else self.opp

    def find(self, sd: Side, sp: str) -> str:
        """입력된 종이 그 편에 없으면 같은 종 계열의 팀원으로 잇는다 (이름이 같은 폼은 입력으로 구분할 수 없다)."""
        if sp in sd.mons or not hasattr(self.base_of, "family"):
            return sp
        fam = self.base_of.family(sp)
        cands = [s for s in sd.mons if self.base_of.family(s) == fam]
        return cands[0] if len(cands) == 1 else sp

    # ---------- 사건 적용 ----------
    def start_turn(self) -> None:
        self.turn += 1
        self.fainted_this_turn.clear()
        self.after_residual = False
        # 시계가 다 된 벽·순풍·트릭룸을 끈다 (끝나는 턴 상한이 지났을 때. 상대 벽은 그 전까지 0~n턴으로 보인다)
        for sd in (self.me, self.opp):
            for k in [k for k, c in sd.timed.items() if (c.hi or c.lo or 0) < self.turn]:
                del sd.timed[k]
        if self.trickroom.kind != "none" and (self.trickroom.hi or self.trickroom.lo or 0) < self.turn:
            self.set_clock(self.trickroom, "none", None, None)

    def _field_event(self, e: Event) -> bool:
        """벽·순풍·설치기·트릭룸 사건이면 적용하고 True."""
        if e.kind == "room":
            n = e.amount or TRICK_ROOM_TURNS
            self.set_clock(self.trickroom, e.value, *((None, None) if e.value == "none" else (self.end_turn(n), self.end_turn(n))))
        elif e.kind == "courtchange":
            self.me.timed, self.opp.timed = self.opp.timed, self.me.timed
            self.me.hazards, self.opp.hazards = self.opp.hazards, self.me.hazards
        elif e.kind in ("sidestart", "sideend"):
            self._side_condition(self.side(e.side), e)
        else:
            return False
        return True

    def _side_condition(self, sd: Side, e: Event) -> None:
        cond = e.value
        if e.kind == "sideend":
            sd.timed.pop(cond, None)
            sd.hazards.pop(cond, None)
            return
        if cond in HAZARD_MAX:
            sd.hazards[cond] = min(HAZARD_MAX[cond], e.amount if e.amount else sd.hazards.get(cond, 0) + 1)
            return
        if cond not in TIMED:
            return
        if e.amount:                                   # 직접 켜기: 남은 턴 확정
            sd.timed[cond] = Clock(cond, self.end_turn(e.amount), self.end_turn(e.amount))
            return
        if cond in sd.timed:                           # 같은 벽·순풍이 켜져 있으면 기술이 실패한다
            return
        lo = hi = TIMED[cond]
        if cond in SCREENS:
            m = sd.mons.get(sd.active or "")
            item = (m.held if m and m.held is not None else (m.item if m else "")) or ""
            known = item if (sd is self.me or item) else ""
            lo = 8 if known == "lightclay" else 5
            hi = 8 if (known == "lightclay" or (sd is self.opp and not known)) else 5
        sd.timed[cond] = Clock(cond, self.end_turn(lo), self.end_turn(hi))

    def apply(self, events: list[Event]) -> list[str]:  # noqa: C901 (사건 분기)
        warns: list[str] = []
        self.log.append(Turn(self.turn, list(events)))
        for idx, e in enumerate(events):
            nxt = events[idx + 1] if idx + 1 < len(events) else None
            self.clock_from_ability = self._next_clock_flag
            self._next_clock_flag = False
            if e.kind == "turn":
                self.start_turn()
                continue
            if e.kind == "weather":
                self.set_clock(self.weather, e.value, None, None)
                continue
            if e.kind == "terrain":
                self.set_clock(self.terrain, e.value, None, None)
                continue
            if self._field_event(e):
                continue
            sd = self.side(e.side)
            sp = self.find(sd, self.base(e.species)) if e.species else sd.active
            if e.kind == "switch":
                m = sd.get(sp)
                if m is None:
                    if sd is self.me:
                        warns.append(f"내 팀에 없는 포켓몬으로 교체: {sp}")
                    m = sd.get(sp, create=True)
                    if sd is self.opp and len(sd.order) > 6:
                        warns.append(f"상대가 보여 준 6마리에 없는 포켓몬: {sp}")
                if m.fainted:
                    warns.append(f"{sp}은(는) 이미 기절했다")
                if sd.active and sd.active in sd.mons and e.value != "replace":
                    sd.mons[sd.active].boosts = {k: 0 for k in BOOST_KEYS}   # 교체하면 랭크가 풀린다
                    sd.mons[sd.active].transformed = False
                    sd.mons[sd.active].last_move = ""
                if e.value == "replace":                               # 일루전 해제: 랭크는 그대로 이어진다
                    if sd.active and sd.active in sd.mons and sd.active != sp:
                        m.boosts = dict(sd.mons[sd.active].boosts)
                if sd.name in self.fainted_this_turn and e.value != "replace":
                    self.after_residual = True
                sd.active, m.seen = sp, True
                continue
            if sp is None:
                warns.append(f"누구에 대한 입력인지 모른다 ({e.kind}): 아직 출전 포켓몬이 없다")
                continue
            m = sd.get(sp)
            if m is None:
                if sd is self.me:
                    warns.append(f"내 팀에 없는 포켓몬: {sp}")
                m = sd.get(sp, create=True)
            if e.kind == "move":
                if (sd is self.me and m.set and e.move != "struggle" and not m.transformed
                        and to_id(e.move) not in {to_id(x) for x in m.set.get("moves", [])}):
                    warns.append(f"내 {sp}에게 없는 기술: {e.move}")
                if e.move != "struggle":
                    m.last_move = e.move
                if e.move not in m.moves and e.move != "struggle" and not m.transformed:
                    m.moves.append(e.move)
                    if sd is self.opp and len(m.moves) > 4:
                        warns.append(f"상대 {sp}의 기술이 5개 이상 입력됐다: {', '.join(m.moves)}")
                if m.fainted:
                    warns.append(f"기절한 {sp}이(가) 기술을 썼다")
                if self.derive_field and sd.name in ("me", "opp"):
                    for fe in field_events(sd.name, to_id(e.move), self):
                        if fe.kind == "terrain":
                            self.set_clock(self.terrain, fe.value, None, None)
                        else:
                            self._field_event(fe)
            elif e.kind == "mega":
                if sd.mega_used and not m.mega:
                    warns.append(f"{'상대' if sd is self.opp else '내'} 쪽은 이미 메가진화했다 (배틀당 1회)")
                m.mega, sd.mega_used = True, True
            elif e.kind == "hp":
                m.hp_estimated = e.note == "hazard"
                if e.hp is not None:
                    if m.fainted and e.hp > 0:
                        warns.append(f"기절한 {sp}의 HP가 {e.hp}%로 입력됐다")
                    m.hp = float(e.hp)
                    m.hp_abs, m.hp_max = e.hp_abs, e.hp_max or m.hp_max
                    if e.hp <= 0:
                        m.fainted = True
                m.seen = True
            elif e.kind == "faint":
                self.fainted_this_turn.add(sd.name)
                m.fainted, m.hp = True, 0.0
                m.hp_abs = 0 if m.hp_max else m.hp_abs
            elif e.kind == "status":
                m.status = e.status
            elif e.kind == "cure":
                if m.status and m.status != e.status:
                    warns.append(f"{sp}의 상태이상은 {m.status}인데 {e.status} 회복이 입력됐다")
                m.status = ""
            elif e.kind == "boost":
                m.boosts[e.stat] = max(-6, min(6, m.boosts.get(e.stat, 0) + e.amount))
            elif e.kind == "transform":                    # 변신: 상대(대상)의 랭크를 복사한다
                other = self.opp if sd is self.me else self.me
                tgt = other.mons.get(other.active or "")
                if tgt:
                    m.boosts = dict(tgt.boosts)
                m.transformed = True
            elif e.kind == "setboost":
                m.boosts[e.stat] = max(-6, min(6, e.amount))
            elif e.kind == "clearboost":
                m.boosts = {k: (0 if (e.value == "all" or v < 0) else v) for k, v in m.boosts.items()}
            elif e.kind == "item":
                if e.value == "received":                                 # 트릭·바꿔치기로 받은 도구: 지금 드는 도구가 바뀐다
                    m.held, m.item_consumed = e.item, False
                    m.last_move = ""                                       # 구애 고정은 받은 뒤 처음 쓰는 기술부터
                    continue
                if e.value == "lost":                                      # 탁쳐서떨구기 등으로 잃음
                    if e.item and not m.item:
                        m.item = e.item
                    m.held = ""
                    continue
                swapped = "move:" in e.note or "ability:" in e.note       # 트릭·바꿔치기·매지션 등으로 실제로 바뀜
                if sd is self.me and m.item and e.item != m.item and not m.mega and not swapped and m.held is None:
                    warns.append(f"내 {sp}의 도구는 {m.item}인데 {e.item}이(가) 입력됐다")
                if m.held is None:
                    m.item = e.item
                if e.value == "consumed":
                    m.item_consumed = True
                    if m.held is not None:
                        m.held = ""
            elif e.kind == "ability":
                m.ability = e.ability
                w = WEATHER_ABILITY.get(e.ability)
                # 날씨가 실제로 (다시) 시작될 때만 시계를 센다: 뒤에 날씨 사건이 오거나, 지금 날씨가 다를 때.
                # (트레이스로 얻은 모래날림처럼 이미 같은 날씨면 다시 시작하지 않는다)
                if w and not ((nxt and nxt.kind == "weather" and nxt.value == w) or self.weather.kind != w):
                    w = None
                if w:
                    rock = WEATHER_ROCK[w]
                    known = m.item if (sd is self.me or m.item) else ""
                    lo = 8 if known == rock else 5
                    hi = 8 if (known == rock or (sd is self.opp and not known)) else 5
                    self.set_clock(self.weather, w, self.end_turn(lo), self.end_turn(hi))
                    self._next_clock_flag = True
                t = TERRAIN_ABILITY.get(e.ability)
                if t and not ((nxt and nxt.kind == "terrain" and nxt.value == t) or self.terrain.kind != t):
                    t = None
                if t:
                    known = m.item if (sd is self.me or m.item) else ""
                    lo = 8 if known == "terrainextender" else 5
                    hi = 8 if (known == "terrainextender" or (sd is self.opp and not known)) else 5
                    self.set_clock(self.terrain, t, self.end_turn(lo), self.end_turn(hi))
                    self._next_clock_flag = True
        return warns

    def end_turn(self, duration: int) -> int:
        start = self.turn + 1 if self.after_residual else max(self.turn, 1)
        return start + duration - 1

    def set_clock(self, c: Clock, kind: str, lo: int | None, hi: int | None) -> None:
        if kind == "none":
            c.kind, c.lo, c.hi = "none", None, None
            return
        if lo is None and c.kind == kind and self.clock_from_ability:
            return                     # 바로 앞 특성 사건이 이미 시계를 정했다 (이어지는 날씨 사건은 표시일 뿐)
        c.kind = kind
        if lo is not None:
            c.lo, c.hi = lo, hi
        else:
            # 원인을 모르는 시작(기술, 특성 입력 없음): 9세대 날씨·필드는 5턴, 바위류·그라운드코트면 8턴
            c.lo, c.hi = self.end_turn(5), self.end_turn(8)

    def summary(self) -> dict[str, Any]:
        return {"turn": self.turn,
                "me": {"active": self.me.active, "mega_used": self.me.mega_used,
                       "mons": [self.me.mons[s].summary() for s in self.me.order]},
                "opp": {"active": self.opp.active, "mega_used": self.opp.mega_used,
                        "mons": [self.opp.mons[s].summary() for s in self.opp.order]},
                "weather": vars(self.weather), "terrain": vars(self.terrain), "trickroom": vars(self.trickroom),
                "side_conditions": {n: {"timed": {k: vars(c) for k, c in sd.timed.items()}, "hazards": dict(sd.hazards)}
                                    for n, sd in (("me", self.me), ("opp", self.opp))}}
