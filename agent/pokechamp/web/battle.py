"""웹 배틀 놀이터: 사용자(p1)가 무작위 봇(p2)과 한 판을 직접 둔다.

엔진 서버의 /new_battle, /step, /legal_actions를 쓴다. 화면에는 플레이어 시점만 보여 준다:
내 쪽은 정확한 HP, 상대 쪽은 게임처럼 % (소수점 버림)와 공개된 정보만.
"""

from __future__ import annotations

import json
import random
import re
import secrets
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from pokechamp.data.db import active_version
from pokechamp.data.static.names import to_id
from pokechamp.engine_client import EngineClient

ME, OPP = "p1", "p2"


def sprite_id(name_en: str) -> str:
    """Showdown 스프라이트 파일 이름: 'Garchomp-Mega-Z' → 'garchomp-megaz', 'Tauros-Paldea-Combat' → 'tauros-paldeacombat'"""
    base, _, forme = name_en.partition("-")
    clean = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())  # noqa: E731
    return clean(base) + (f"-{clean(forme)}" if forme else "")


class Names:
    """Showdown 영어 이름 → 한국어 이름과 타입 (활성 정적 데이터 버전 기준)."""

    def __init__(self, conn: sqlite3.Connection):
        v = active_version(conn)
        vid = v["id"] if v else -1
        self.maps: dict[str, dict[str, str]] = {}
        for table in ("species", "moves", "items", "abilities"):
            rows = conn.execute(f"SELECT id, name_ko FROM {table} WHERE version_id=?", (vid,))
            self.maps[table] = {r["id"]: r["name_ko"] for r in rows if r["name_ko"]}
        self.move_type = {r["id"]: (r["type"], r["category"]) for r in
                          conn.execute("SELECT id, type, category FROM moves WHERE version_id=?", (vid,))}
        self.species_types = {r["id"]: json.loads(r["types_json"]) for r in
                              conn.execute("SELECT id, types_json FROM species WHERE version_id=?", (vid,))}
        # 기본 종 id → 한국어 이름 (허용 목록에 다른 폼만 있는 경우의 대체용, 예: Maushold → 파밀리쥐)
        self.base_ko: dict[str, str] = {}
        for r in conn.execute("SELECT base_species, name_ko FROM species WHERE version_id=? AND is_mega=0"
                              " ORDER BY form_index", (vid,)):
            self.base_ko.setdefault(to_id(r["base_species"]), re.sub(r"\s*\(.*\)$", "", r["name_ko"] or ""))

    def types(self, species: str) -> list[str]:
        sid = to_id(species)
        return self.species_types.get(sid) or self.species_types.get(to_id(species.split("-")[0]), [])

    def get(self, table: str, name: str | None) -> str:
        if not name:
            return ""
        m = self.maps[table]
        if table == "species" and to_id(name) not in m:
            # 외형만 다른 폼(Florges-Blue 등)이나 허용 목록에 다른 폼만 있는 종은 기본 종 이름을 쓴다
            base = to_id(name.split("-")[0])
            return m.get(base) or self.base_ko.get(base) or name
        return m.get(to_id(name), name)


def _ident(s: str) -> tuple[str, str]:
    """'p2a: Garchomp' → ('p2', 'Garchomp')"""
    side, _, name = s.partition(": ")
    return side[:2], name


def _hp(cond: str) -> tuple[int | None, int | None, str]:
    """'93/141 par' → (93, 141, 'par'), '0 fnt' → (0, None, 'fnt')"""
    parts = cond.split()
    status = parts[1] if len(parts) > 1 else ""
    if parts and parts[0] == "0":
        return 0, None, "fnt"
    m = re.match(r"(\d+)/(\d+)", parts[0] if parts else "")
    return (int(m.group(1)), int(m.group(2)), status) if m else (None, None, status)


STATUS_KO = {"brn": "화상", "par": "마비", "slp": "잠듦", "frz": "얼음", "psn": "독", "tox": "맹독", "fnt": "기절"}
STAT_KO = {"atk": "공격", "def": "방어", "spa": "특수공격", "spd": "특수방어", "spe": "스피드",
           "accuracy": "명중률", "evasion": "회피율"}
WEATHER_KO = {"SunnyDay": "쾌청", "RainDance": "비", "Sandstorm": "모래바람", "Snowscape": "설경", "none": "날씨 없음"}


@dataclass
class OppMon:
    species: str
    revealed: bool = False
    hp_pct: int | None = 100
    status: str = ""
    fainted: bool = False
    moves: list[str] = field(default_factory=list)
    item: str = ""
    ability: str = ""
    mega: str = ""


@dataclass
class Session:
    id: str
    seed: str
    state: Any
    my_team: list[dict]
    rng: random.Random
    turn: int = 0
    ended: bool = False
    winner: str | None = None
    events: list[dict] = field(default_factory=list)
    opp: dict[str, OppMon] = field(default_factory=dict)     # 상대 6마리 (이름 → 공개 정보)
    opp_active: str | None = None
    requests: dict = field(default_factory=dict)
    my_actions: list[dict] = field(default_factory=list)


class LogReader:
    """Showdown 프로토콜 로그를 플레이어(p1) 시점의 한국어 이벤트로 바꾸고 상대 공개 정보를 추적한다."""

    def __init__(self, session: Session, names: Names):
        self.s = session
        self.n = names

    def mon(self, ident: str) -> str:
        side, name = _ident(ident)
        who = "" if side == ME else "상대 "
        return f"{who}{self.n.get('species', name)}"

    def feed(self, lines: list[str]) -> None:
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("|split|"):
                side = line.split("|")[2]
                # split 다음 줄은 그 편만 보는 정확한 값, 그다음 줄은 공개 값(%)
                chosen = lines[i + 1] if side == ME else lines[i + 2] if i + 2 < len(lines) else ""
                self.line(chosen)
                i += 3
                continue
            self.line(line)
            i += 1

    SOURCE_KO = {"drain": "흡수", "recoil": "반동", "psn": "독", "tox": "맹독", "brn": "화상", "Sandstorm": "모래바람",
                 "confusion": "혼란", "Stealth Rock": "스텔스록", "Spikes": "압정뿌리기"}

    def source(self, src: str) -> str:
        """'[from] item: Leftovers' 같은 원인 표기를 한국어로."""
        src = src.replace("[from] ", "")
        kind, _, name = src.partition(": ")
        if not name:
            return self.SOURCE_KO.get(kind, self.n.get("moves", kind))
        table = {"item": "items", "ability": "abilities", "move": "moves"}.get(kind)
        return self.n.get(table, name) if table else name

    def add(self, kind: str, text: str) -> None:
        self.s.events.append({"turn": self.s.turn, "kind": kind, "text": text})

    def track_hp(self, ident: str, cond: str) -> None:
        side, name = _ident(ident)
        if side != OPP or name not in self.s.opp:
            return
        hp, mx, status = _hp(cond)
        o = self.s.opp[name]
        if status == "fnt":
            o.hp_pct, o.fainted = 0, True
        elif hp is not None:
            o.hp_pct = hp if mx == 100 else int(100 * hp / mx)
            o.status = status

    def line(self, line: str) -> None:  # noqa: C901 (프로토콜 분기)
        p = line.split("|")
        if len(p) < 2:
            return
        cmd, args = p[1], p[2:]
        n = self.n
        if cmd == "poke" and args[0] == OPP:
            sp = args[1].split(",")[0]
            self.s.opp[sp] = OppMon(species=sp)
        elif cmd == "turn":
            self.s.turn = int(args[0])
            self.add("turn", f"{self.s.turn}턴")
        elif cmd in ("switch", "drag"):
            side, name = _ident(args[0])
            sp = args[1].split(",")[0]
            if side == OPP:
                self.s.opp_active = name
                o = self.s.opp.setdefault(name, OppMon(species=sp))
                o.revealed = True
                self.track_hp(args[0], args[2])
            verb = "내보냈다" if cmd == "switch" else "끌려 나왔다"
            self.add("switch", f"{'나' if side == ME else '상대'}는 {n.get('species', sp)}을(를) {verb}")
        elif cmd == "detailschange":
            side, name = _ident(args[0])
            if side == OPP and name in self.s.opp:
                self.s.opp[name].species = args[1].split(",")[0]
        elif cmd == "-mega":
            side, name = _ident(args[0])
            if side == OPP and name in self.s.opp:
                self.s.opp[name].mega = args[2] if len(args) > 2 else "?"
                self.s.opp[name].item = args[2] if len(args) > 2 else ""
            self.add("mega", f"{self.mon(args[0])}이(가) 메가진화했다! ({n.get('items', args[2] if len(args) > 2 else '')})")
        elif cmd == "move":
            side, name = _ident(args[0])
            if side == OPP and name in self.s.opp and args[1] not in self.s.opp[name].moves:
                self.s.opp[name].moves.append(args[1])
            self.add("move", f"{self.mon(args[0])}의 {n.get('moves', args[1])}!")
        elif cmd in ("-damage", "-heal", "-sethp"):
            self.track_hp(args[0], args[1])
            hp, mx, status = _hp(args[1])
            side, _ = _ident(args[0])
            how = ""
            src = next((a for a in args[2:] if a.startswith("[from]")), "")
            if src:
                how = f" ({self.source(src)})"
            if status == "fnt" or hp == 0:
                val = "0"
            elif side == ME:
                val = f"{hp}/{mx}"
            else:
                val = f"{hp}%"
            verb = "회복" if cmd == "-heal" else "HP"
            self.add("hp", f"{self.mon(args[0])} {verb} → {val}{how}")
        elif cmd == "faint":
            side, name = _ident(args[0])
            if side == OPP and name in self.s.opp:
                self.s.opp[name].fainted, self.s.opp[name].hp_pct = True, 0
            self.add("faint", f"{self.mon(args[0])}은(는) 쓰러졌다")
        elif cmd == "-supereffective":
            self.add("info", "효과가 굉장했다!")
        elif cmd == "-resisted":
            self.add("info", "효과가 별로인 듯하다…")
        elif cmd == "-immune":
            self.add("info", f"{self.mon(args[0])}에게는 효과가 없다")
        elif cmd == "-crit":
            self.add("info", "급소에 맞았다!")
        elif cmd == "-miss":
            self.add("info", f"{self.mon(args[0])}의 공격이 빗나갔다")
        elif cmd == "-fail":
            self.add("info", "그러나 실패했다")
        elif cmd == "-status":
            side, name = _ident(args[0])
            if side == OPP and name in self.s.opp:
                self.s.opp[name].status = args[1]
            self.add("status", f"{self.mon(args[0])}은(는) {STATUS_KO.get(args[1], args[1])} 상태가 됐다")
        elif cmd == "-curestatus":
            self.add("status", f"{self.mon(args[0])}의 {STATUS_KO.get(args[1], args[1])}이(가) 나았다")
        elif cmd in ("-boost", "-unboost"):
            stat, amt = STAT_KO.get(args[1], args[1]), int(args[2])
            self.add("boost", f"{self.mon(args[0])}의 {stat} {'+' if cmd == '-boost' else '-'}{amt}")
        elif cmd == "cant":
            self.add("info", f"{self.mon(args[0])}은(는) 움직일 수 없다 ({STATUS_KO.get(args[1], args[1])})")
        elif cmd == "-weather" and args[0] != "none" and "[upkeep]" not in args:
            self.add("field", f"날씨: {WEATHER_KO.get(args[0], args[0])}")
        elif cmd == "-ability":
            side, name = _ident(args[0])
            if side == OPP and name in self.s.opp:
                self.s.opp[name].ability = args[1]
            self.add("info", f"{self.mon(args[0])}의 {n.get('abilities', args[1])}")
        elif cmd in ("-item", "-enditem"):
            side, name = _ident(args[0])
            if side == OPP and name in self.s.opp:
                self.s.opp[name].item = args[1]
            if cmd == "-enditem":
                self.add("info", f"{self.mon(args[0])}의 {n.get('items', args[1])} 사용")
        elif cmd == "win":
            self.s.ended, self.s.winner = True, args[0]
            self.add("end", "승리!" if args[0] == ME else "패배…")
        elif cmd == "tie":
            self.s.ended = True
            self.add("end", "무승부")


class Playground:
    def __init__(self, conn: sqlite3.Connection, client: EngineClient | None = None):
        self.conn = conn
        self.client = client or EngineClient()
        self.sessions: dict[str, Session] = {}

    def new(self, seed: str | None = None) -> dict:
        seed = seed or secrets.token_hex(4)
        teams = self.client.batch("/random_team", [{"seed": f"{seed}-me"}, {"seed": f"{seed}-opp"}])
        me, opp = (t["result"]["team"] for t in teams)
        b = self.client.new_battle(me, opp, seed=f"{seed}-battle")
        s = Session(id=secrets.token_hex(6), seed=seed, state=b["state"], my_team=me, rng=random.Random(seed))
        self.sessions[s.id] = s
        self._absorb(s, b)
        return self.view(s)

    def act(self, sid: str, choice: str) -> dict:
        s = self.sessions[sid]
        if s.ended:
            raise ValueError("이미 끝난 배틀이다")
        if choice not in {a["choice"] for a in s.my_actions}:
            raise ValueError(f"지금 고를 수 없는 선택: {choice}")
        self._step(s, choice)
        # 상대만 골라야 하는 상황(상대 교체 등)은 봇이 계속 진행한다
        guard = 0
        while not s.ended and not s.my_actions and guard < 20:
            self._step(s, None)
            guard += 1
        return self.view(s)

    def _step(self, s: Session, my_choice: str | None) -> None:
        la = self.client.legal_actions(s.state)
        opp_choice = s.rng.choice(la[OPP])["choice"] if la[OPP] else None
        r = self.client.step(s.state, p1=my_choice if la[ME] else None, p2=opp_choice)
        if r.get("errors"):
            raise ValueError(f"엔진이 선택을 거부했다: {r['errors']}")
        self._absorb(s, r)

    def _absorb(self, s: Session, r: dict) -> None:
        s.state = r["state"]
        s.requests = r["requests"]
        LogReader(s, Names(self.conn)).feed(r["log"])
        s.ended = s.ended or bool(r.get("ended"))
        if r.get("ended") and not s.winner:
            s.winner = r.get("winner")
        s.my_actions = [] if s.ended else self.client.legal_actions(s.state)[ME]

    def view(self, s: Session) -> dict:
        names = Names(self.conn)
        req = s.requests.get(ME) or {}
        side = (req.get("side") or {}).get("pokemon", [])
        by_species = {to_id(t["species"]): t for t in s.my_team}
        mine = []
        for i, pm in enumerate(side):
            sp = pm["details"].split(",")[0]
            hp, mx, status = _hp(pm["condition"])
            base = by_species.get(to_id(pm["ident"].split(": ")[1]), {})
            mine.append({
                "slot": i + 1, "species": sp, "name_ko": names.get("species", sp), "hp": hp, "max_hp": mx,
                "status": STATUS_KO.get(status, status), "active": pm.get("active", False),
                "item": names.get("items", pm.get("item")), "ability": names.get("abilities", pm.get("baseAbility")),
                "moves": [names.get("moves", m) for m in pm.get("moves", [])], "stats": pm.get("stats", {}),
                "sp": base.get("evs", {}), "nature": base.get("nature", ""),
                "types": names.types(sp), "sprite": sprite_id(sp),
            })
        active_req = (req.get("active") or [{}])[0] if req.get("active") else {}
        move_info = {i + 1: m for i, m in enumerate(active_req.get("moves", []))}
        actions = []
        for a in s.my_actions:
            label = a["choice"]
            if a["kind"] == "move":
                idx = int(a["choice"].split()[1])
                mi = move_info.get(idx, {})
                label = f"{names.get('moves', mi.get('move', a.get('move')))}" + (" + 메가진화" if a.get("mega") else "")
                mtype, mcat = names.move_type.get(a.get("move"), ("", ""))
                a = {**a, "pp": mi.get("pp"), "maxpp": mi.get("maxpp"), "type": mtype, "category": mcat}
            elif a["kind"] == "switch":
                label = f"{mine[a['slot'] - 1]['name_ko']}(으)로 교체" if a["slot"] <= len(mine) else label
            actions.append({**a, "label": label})
        phase = ("ended" if s.ended else "team" if req.get("teamPreview") else
                 "switch" if req.get("forceSwitch") else "move" if s.my_actions else "wait")
        opp = [{"species": o.species, "name_ko": names.get("species", o.species), "revealed": o.revealed,
                "hp_pct": o.hp_pct, "status": STATUS_KO.get(o.status, o.status), "fainted": o.fainted,
                "moves": [names.get("moves", m) for m in o.moves], "item": names.get("items", o.item),
                "ability": names.get("abilities", o.ability), "active": key == s.opp_active, "mega": bool(o.mega),
                "types": names.types(o.species), "sprite": sprite_id(o.species)}
               for key, o in s.opp.items()]
        return {"id": s.id, "seed": s.seed, "turn": s.turn, "phase": phase, "ended": s.ended,
                "winner": s.winner, "mine": mine, "opp": opp, "actions": actions, "events": s.events[-200:]}
