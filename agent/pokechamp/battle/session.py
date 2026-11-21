"""배틀 세션: 관찰 사건을 하나씩 처리해 상태 추적(state.py)과 믿음 상태(belief.py)를 함께 갱신한다 (PROJECT_PLAN 9.1~9.2).

사건마다 "그 사건 직전 상태"로 증거를 계산한 뒤 상태에 적용한다.
- 기술 사건: 상대 기술이면 기술 증거. 같은 턴에 상대가 먼저 같은 우선도 기술을 썼으면(둘 다 그 턴 출전 그대로) 행동 순서 증거.
- 기술 직후 맞은 쪽의 HP 사건: 데미지 증거 (상대가 때렸으면 상대 파티클이 공격자, 내가 때렸으면 상대 파티클이 방어자).
- 도구·특성·메가 사건: 그대로 증거.
"""

from __future__ import annotations

import json
from typing import Any

from pokechamp.battle.belief import SPEED_ABILITY, Belief, stage_mul
from pokechamp.battle.events import Event, SpeciesResolver
from pokechamp.battle.state import BattleState
from pokechamp.data.db import active_version
from pokechamp.data.static.names import to_id
from pokechamp.engine_client import EngineClient

MULTIHIT = {"bonerush", "bulletseed", "doublehit", "dragondarts", "dualwingbeat", "iciclespear", "pinmissile",
            "populationbomb", "rockblast", "scaleshot", "tailslap", "tripleaxel", "twinbeam", "watershuriken"}
WEATHER_ENGINE = {"sun": "SunnyDay", "rain": "RainDance", "sand": "Sandstorm", "snow": "Snowscape"}
TERRAIN_ENGINE = {"electric": "Electric Terrain", "grassy": "Grassy Terrain", "psychic": "Psychic Terrain",
                  "misty": "Misty Terrain"}
ENGINE_KEYS = ("species", "item", "ability", "nature", "evs", "moves")


def engine_set(st: dict) -> dict:
    return {k: st[k] for k in ENGINE_KEYS if st.get(k) not in (None, "")}


class BattleSession:
    def __init__(self, conn, my_team: list[dict[str, Any]], opp_species: list[str], client: EngineClient | None = None,
                 n_particles: int = 48, seed: str = "session", use_damage: bool = True):
        self.conn = conn
        self.client = client or EngineClient()
        self.res = SpeciesResolver(conn)
        self.my_team = my_team
        opp = [self.res(s) for s in opp_species]
        self.state = BattleState(my_team, opp, self.res)
        v = active_version(conn)
        self.belief = Belief(conn, opp, self.client, n=n_particles, seed=seed, regulation=v["regulation"] if v else None)
        self.use_damage = use_damage
        vid = v["id"] if v else -1
        self.moves = {r[0]: {"priority": r[1], "category": r[2]} for r in conn.execute(
            "SELECT id, priority, category FROM moves WHERE version_id=?", (vid,))}
        # 내 포켓몬의 실제 능력치 (기본·메가)
        self.my_stats: dict[tuple[str, bool], dict] = {}
        reqs, keys = [], []
        for st in my_team:
            sp = self.res(st["species"])
            reqs.append({"set": engine_set(st)})
            keys.append((sp, False))
            if self.mega_of(st):
                reqs.append({"set": engine_set(st), "mega": True})
                keys.append((sp, True))
        for k, r in zip(keys, self.client.batch("/stats", reqs)):
            if r.get("ok"):
                self.my_stats[k] = r["result"]
        self.turn_moves: list[tuple[str, str, int]] = []     # 이번 턴 (편, 종, 우선도), 교체가 끼면 끊는다
        self.pending: dict[str, Any] | None = None           # 직전 공격 (데미지 증거 대기)
        self.evidence = {"move": 0, "order": 0, "damage": 0, "item": 0, "ability": 0, "mega": 0}

    def mega_of(self, st: dict) -> str | None:
        stones = getattr(self, "_stones", None)
        if stones is None:
            v = active_version(self.conn)
            stones = self._stones = {(r[0], r[2]): r[1] for r in self.conn.execute(
                "SELECT base_species_id, mega_species_id, stone_item_id FROM mega_forms WHERE version_id=?", (v["id"],))}
        return stones.get((self.res(st["species"]), to_id(st.get("item") or "")))

    # ---------- 속도 ----------
    def my_speed(self) -> float | None:
        sp = self.state.me.active
        if not sp:
            return None
        m = self.state.me.mons[sp]
        st = self.my_stats.get((sp, m.mega)) or self.my_stats.get((sp, False))
        if not st:
            return None
        s = st["spe"] * stage_mul(m.boosts.get("spe", 0))
        if m.item == "choicescarf" and not m.mega:
            s *= 1.5
        if m.status == "par":
            s *= 0.5
        if SPEED_ABILITY.get(to_id(st.get("ability") or "")) == self.state.weather.kind:
            s *= 2
        return s

    # ---------- 사건 처리 ----------
    def feed(self, events: list[Event]) -> list[str]:
        warns: list[str] = []
        for e in events:
            try:
                self._evidence(e)
            except Exception as ex:  # noqa: BLE001  증거 계산 실패는 추적을 막지 않는다
                warns.append(f"증거 계산 건너뜀 ({e.kind}): {ex}")
            warns += self.state.apply([e])
        warns += self.belief.contradictions
        self.belief.contradictions = []
        return warns

    def _field(self) -> dict:
        f = {}
        if self.state.weather.kind in WEATHER_ENGINE:
            f["weather"] = WEATHER_ENGINE[self.state.weather.kind]
        if self.state.terrain.kind in TERRAIN_ENGINE:
            f["terrain"] = TERRAIN_ENGINE[self.state.terrain.kind]
        return f

    def _evidence(self, e: Event) -> None:  # noqa: C901 (사건 분기)
        st = self.state
        if e.kind == "turn":
            self.turn_moves, self.pending = [], None
            return
        if e.kind in ("switch", "faint"):
            self.turn_moves = [x for x in self.turn_moves if x[0] != e.side] if e.kind == "switch" else self.turn_moves
            self.pending = None
            if e.kind == "switch":
                self.turn_moves.append((e.side or "", "__switch__", 99))
            return
        if e.kind == "crit" and self.pending:
            self.pending["crit"] = True
            return
        sd = st.side(e.side)
        sp = st.find(sd, st.base(e.species)) if e.species else sd.active
        if e.kind == "move":
            self.pending = None
            mv = self.moves.get(e.move, {"priority": 0, "category": "Status"})
            opp_mon = st.opp.mons.get(st.opp.active or "")
            if e.side == "opp" and sp and not (opp_mon and opp_mon.transformed):
                self.belief.observe_move(sp, e.move)
                self.evidence["move"] += 1
            # 행동 순서: 이번 턴 다른 편이 먼저 같은 우선도 기술을 썼고, 그 사이 교체가 없었다
            prev = [x for x in self.turn_moves if x[0] != e.side]
            if (prev and prev[-1][1] != "__switch__" and prev[-1][2] == mv["priority"] and len(self.turn_moves) == 1
                    and st.opp.active and st.me.active):
                ms = self.my_speed()
                om = st.opp.mons[st.opp.active]
                if ms is not None and not om.transformed:
                    self.belief.observe_order(st.opp.active, opp_first=(e.side == "me"), my_speed=ms, mega=om.mega,
                                              boosts=om.boosts.get("spe", 0), status=om.status, weather=st.weather.kind)
                    self.evidence["order"] += 1
            self.turn_moves.append((e.side or "", sp or "", mv["priority"]))
            if self.use_damage and mv["category"] != "Status" and e.move not in MULTIHIT and st.me.active and st.opp.active:
                me, op = st.me.mons[st.me.active], st.opp.mons[st.opp.active]
                defender = op if e.side == "me" else me
                self.pending = {"side": e.side, "move": e.move, "crit": False, "me": st.me.active, "opp": st.opp.active,
                                "before": (defender.hp_abs if e.side == "opp" else defender.hp),
                                "my_boosts": {k: v for k, v in me.boosts.items() if v}, "opp_boosts": {k: v for k, v in op.boosts.items() if v},
                                "my_status": me.status, "opp_status": op.status, "my_mega": me.mega, "opp_mega": op.mega,
                                "field": self._field(), "my_maxhp": me.hp_max}
            return
        if e.kind == "hp" and self.pending:
            p, self.pending = self.pending, None
            defender_side = "opp" if p["side"] == "me" else "me"
            if e.side != defender_side or (e.species and st.base(e.species) not in (p["me"], p["opp"])) or e.note:
                return
            before = p["before"]
            after = e.hp_abs if defender_side == "me" else e.hp
            if before is None or after is None or after >= before:
                return
            my_set = st.me.mons[p["me"]].set
            if not my_set:
                return
            self.belief.observe_damage(p["opp"], attacker_is_opp=(p["side"] == "opp"), my_set=engine_set(my_set),
                                       move=p["move"], my_mega=p["my_mega"], opp_mega=p["opp_mega"],
                                       my_boosts=p["my_boosts"], opp_boosts=p["opp_boosts"], my_status=p["my_status"],
                                       opp_status=p["opp_status"], field=p["field"], crit=p["crit"], before=before,
                                       after=after, my_maxhp=p["my_maxhp"])
            self.evidence["damage"] += 1
            return
        if e.side != "opp" or not sp:
            return
        om = st.opp.mons.get(sp)
        if e.kind == "item":
            if e.value == "received":            # 트릭 등으로 받은 도구는 원래 도구의 증거가 아니다 (이후 도구 증거도 끈다)
                self.belief.mon(sp).item_swapped = True
                return
            if e.value == "lost" and (not e.item or (om and om.held is not None)):
                return                           # 떨어뜨린 도구를 모르거나, 이미 바뀐 도구였다
            self.belief.observe_item(sp, e.item, note="" if e.value == "lost" else e.note,
                                     consumed=e.value in ("consumed", "lost"), mega=bool(om and om.mega))
            self.evidence["item"] += 1
        elif e.kind == "ability":
            if om and om.transformed:            # 변신은 상대의 특성을 복사한다
                return
            self.belief.observe_ability(sp, e.ability, note=e.note, mega=bool(om and om.mega))
            self.evidence["ability"] += 1
        elif e.kind == "mega":
            self.belief.observe_mega(sp)
            self.evidence["mega"] += 1

    def summary(self) -> dict[str, Any]:
        return {"state": self.state.summary(), "evidence": dict(self.evidence),
                "belief": {sp: self.belief.summary(sp) for sp in self.state.opp.order if self.state.opp.mons[sp].seen}}


def dumps(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)
