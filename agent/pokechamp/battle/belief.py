"""상대 세트의 믿음 상태 (PROJECT_PLAN 9.2): 포켓몬마다 세트 원형 분포(결합 분포)에서 뽑은 파티클과 가중치.

증거별 우도:
- 쓴 기술: 그 기술이 없는 파티클은 0 (발버둥, 변신 중 제외).
- 드러난 도구·특성: 맞지 않으면 0. 트릭·바꿔치기로 받은 도구, 트레이스·미라 등으로 바뀐 특성, 메가진화 뒤의 특성은 증거로 쓰지 않는다.
- 메가진화: 그 종의 메가스톤을 들지 않은 파티클은 0.
- 행동 순서: 같은 우선도로 양쪽이 기술을 썼을 때 실제 스피드(랭크·스카프·마비·날씨 스피드 특성)로 순서가 맞는지.
  모델 밖 효과(선제의손톱, 순풍, 트릭룸 등)를 위해 ε을 섞는다.
- 데미지: 기술 바로 뒤의 상대 HP 변화를 /damage(16단계 난수 + 급소)로 계산한 분포와 맞춰 본다.
  내 HP는 정확한 값(±1), 상대 HP는 % 반올림을 고려해 ±1.5%p를 허용한다. 연속기·고정 데미지 기술은 쓰지 않는다. ε을 섞는다.
유효 파티클 수가 기준 아래로 떨어지면 재샘플링하고, 모든 가중치가 0이 되면 갱신을 되돌리고 모순 입력으로 알린다.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

from pokechamp.battle.events import Event
from pokechamp.data.static.names import to_id
from pokechamp.engine_client import EngineClient
from pokechamp.sets.archetypes import load_archetypes, sample_sets

SPEED_ABILITY = {"chlorophyll": "sun", "swiftswim": "rain", "sandrush": "sand", "slushrush": "snow"}
SKIP_DAMAGE_MOVES = {"seismictoss", "nightshade", "superfang", "ruination", "finalgambit", "endeavor", "counter",
                     "mirrorcoat", "metalburst", "bide", "psywave", "dragonrage", "sonicboom"}
ABILITY_CHANGERS = ("trace", "mummy", "lingeringaroma", "wanderingspirit", "skillswap", "entrainment", "roleplay",
                    "worryseed", "simplebeam", "doodle", "receiver", "powerofalchemy")


def stage_mul(s: int) -> float:
    return (2 + s) / 2 if s >= 0 else 2 / (2 - s)


@dataclass
class Particle:
    set: dict[str, Any]
    w: float = 1.0
    stats: dict[str, Any] | None = None        # /stats (기본 폼)
    mega_stats: dict[str, Any] | None = None   # /stats (메가 폼)


@dataclass
class MonBelief:
    species: str
    particles: list[Particle] = field(default_factory=list)
    # 확정 증거 (재충전할 때 새 파티클을 거르는 데 쓴다)
    moves: set[str] = field(default_factory=set)
    item: str | None = None
    ability: str | None = None
    mega: bool = False
    rejuvenated: int = 0
    item_swapped: bool = False      # 트릭·바꿔치기로 도구가 바뀌었을 수 있다 → 이후 도구 증거는 쓰지 않는다

    def consistent(self, s: dict) -> bool:
        if self.moves and not self.moves <= {to_id(m) for m in s.get("moves", [])}:
            return False
        if self.item is not None and to_id(s.get("item") or "") != self.item:
            return False
        if self.ability is not None and to_id(s.get("ability") or "") != self.ability:
            return False
        return not (self.mega and not s.get("mega_species"))

    def normalize(self) -> float:
        tot = sum(p.w for p in self.particles)
        if tot > 0:
            for p in self.particles:
                p.w /= tot
        return tot

    def ess(self) -> float:
        s2 = sum(p.w * p.w for p in self.particles)
        return 1.0 / s2 if s2 > 0 else 0.0

    def sample(self, rng: random.Random) -> Particle:
        return rng.choices(self.particles, weights=[p.w for p in self.particles])[0]

    def marginal(self, key: str) -> list[tuple[Any, float]]:
        out: dict[Any, float] = {}
        for p in self.particles:
            v = p.set.get(key)
            v = tuple(sorted(v)) if isinstance(v, list) else v
            out[v] = out.get(v, 0.0) + p.w
        return sorted(out.items(), key=lambda kv: -kv[1])


class Belief:
    def __init__(self, conn, opp_species: list[str], client: EngineClient | None = None, n: int = 48, seed: str = "belief",
                 regulation: str | None = None, eps_speed: float = 0.03, eps_damage: float = 0.05):
        self.client = client or EngineClient()
        self.rng = random.Random(f"{seed}-belief")
        self.n, self.eps_speed, self.eps_damage = n, eps_speed, eps_damage
        self.mons: dict[str, MonBelief] = {}
        self.conn, self.regulation = conn, regulation
        self.contradictions: list[str] = []
        for sp in opp_species:
            self.mons[sp] = MonBelief(sp, self._draw(sp, n))
        self._fill_stats([p for mb in self.mons.values() for p in mb.particles])

    # ---------- 파티클 ----------
    def _draw(self, sp: str, n: int) -> list[Particle]:
        archs = load_archetypes(self.conn, sp, self.regulation)
        if not archs:
            raise ValueError(f"세트 원형이 없다: {sp} (`pokechamp build-sets`)")
        return [Particle(s, 1.0 / n) for s in sample_sets(archs, n, self.rng)]

    def _fill_stats(self, parts: list[Particle]) -> None:
        def eset(s):
            return {k: s[k] for k in ("species", "item", "ability", "nature", "evs", "moves") if s.get(k) not in (None, "")}
        res = self.client.batch("/stats", [{"set": eset(p.set)} for p in parts])
        for p, r in zip(parts, res):
            p.stats = r["result"] if r.get("ok") else None
        megas = [p for p in parts if p.set.get("mega_species")]
        if megas:
            res = self.client.batch("/stats", [{"set": eset(p.set), "mega": True} for p in megas])
            for p, r in zip(megas, res):
                p.mega_stats = r["result"] if r.get("ok") else None

    def mon(self, sp: str) -> MonBelief:
        if sp not in self.mons:                      # 6마리에 없던 포켓몬(입력 오류일 수도): 그때 만든다
            self.mons[sp] = MonBelief(sp, self._draw(sp, self.n))
            self._fill_stats(self.mons[sp].particles)
        return self.mons[sp]

    def reweight(self, sp: str, like: list[float], why: str) -> bool:
        """우도를 곱한다. 모든 가중치가 0이 되면 결합 분포에서 새로 뽑아 확정 증거로 거른다(재충전, 9.2).
        그래도 맞는 세트가 없으면 되돌리고 False (모순 입력)."""
        mb = self.mon(sp)
        old = [p.w for p in mb.particles]
        for p, l in zip(mb.particles, like):
            p.w *= l
        if mb.normalize() <= 0:
            if self._rejuvenate(mb):
                return True
            for p, w in zip(mb.particles, old):
                p.w = w
            self.contradictions.append(f"{sp}: {why} — 어떤 세트 후보와도 맞지 않는다 (입력을 확인해 주세요)")
            return False
        if mb.ess() < self.n / 3:
            self._resample(mb)
        return True

    def _rejuvenate(self, mb: MonBelief, tries: int = 40) -> bool:
        """결합 분포에서 n × tries개를 새로 뽑아 확정 증거와 맞는 것만 남긴다 (데미지·순서 같은 확률 증거는 다시 적용하지 않는다)."""
        archs = load_archetypes(self.conn, mb.species, self.regulation)
        cands = [s for s in sample_sets(archs, self.n * tries, self.rng) if mb.consistent(s)]
        if not cands:
            return False
        picked = [Particle(dict(s), 1.0 / self.n) for s in self.rng.choices(cands, k=self.n)]
        self._fill_stats(picked)
        mb.particles = picked
        mb.rejuvenated += 1
        return True

    def _resample(self, mb: MonBelief) -> None:
        picked = self.rng.choices(mb.particles, weights=[p.w for p in mb.particles], k=self.n)
        mb.particles = [Particle(dict(p.set), 1.0 / self.n, p.stats, p.mega_stats) for p in picked]

    # ---------- 증거 ----------
    def observe_move(self, sp: str, move: str) -> None:
        if move == "struggle":
            return
        self.mon(sp).moves.add(move)
        if move in ("trick", "switcheroo"):
            self.mon(sp).item_swapped = True
        self.reweight(sp, [1.0 if move in [to_id(m) for m in p.set.get("moves", [])] else 0.0 for p in self.mon(sp).particles],
                      f"기술 {move}")

    def observe_item(self, sp: str, item: str, note: str = "", consumed: bool = False, mega: bool = False) -> None:
        if mega or self.mon(sp).item_swapped or (not consumed and ("move:" in note or "ability:" in note)):
            if not consumed and ("move:" in note or "ability:" in note):
                self.mon(sp).item_swapped = True
            return
        self.mon(sp).item = item
        self.reweight(sp, [1.0 if to_id(p.set.get("item") or "") == item else 0.0 for p in self.mon(sp).particles],
                      f"도구 {item}")

    def observe_ability(self, sp: str, ability: str, note: str = "", mega: bool = False) -> None:
        if self.mon(sp).ability == "trace":       # 트레이스로 얻은 특성의 발동은 원래 특성의 증거가 아니다
            return
        if mega or any(x in to_id(note) for x in ABILITY_CHANGERS):
            if "trace" in to_id(note):
                ability = "trace"
            else:
                return
        self.mon(sp).ability = ability
        self.reweight(sp, [1.0 if to_id(p.set.get("ability") or "") == ability else 0.0 for p in self.mon(sp).particles],
                      f"특성 {ability}")

    def observe_mega(self, sp: str) -> None:
        self.mon(sp).mega = True
        self.reweight(sp, [1.0 if p.set.get("mega_species") else 0.0 for p in self.mon(sp).particles], "메가진화")

    def speed_of(self, p: Particle, mega: bool, boosts: int, status: str, weather: str) -> float | None:
        st = (p.mega_stats if mega else p.stats) or p.stats
        if not st:
            return None
        s = st["spe"] * stage_mul(boosts)
        if to_id(p.set.get("item") or "") == "choicescarf" and not mega:
            s *= 1.5
        if status == "par":
            s *= 0.5
        if SPEED_ABILITY.get(to_id(st.get("ability") or "")) == weather:
            s *= 2
        return s

    def observe_order(self, sp: str, opp_first: bool, my_speed: float, mega: bool, boosts: int, status: str,
                      weather: str) -> None:
        like = []
        for p in self.mon(sp).particles:
            s = self.speed_of(p, mega, boosts, status, weather)
            if s is None:
                like.append(1.0)
                continue
            ok = 0.5 if s == my_speed else (1.0 if (s > my_speed) == opp_first else 0.0)
            like.append(self.eps_speed + (1 - self.eps_speed) * ok)
        self.reweight(sp, like, "행동 순서")

    def observe_damage(self, sp: str, *, attacker_is_opp: bool, my_set: dict, move: str, my_mega: bool, opp_mega: bool,
                       my_boosts: dict, opp_boosts: dict, my_status: str, opp_status: str, field: dict, crit: bool,
                       before: float, after: float, my_maxhp: int | None = None) -> None:
        """attacker_is_opp: 상대가 나를 때렸다(before/after는 내 정확한 HP) / 아니면 내가 상대를 때렸다(before/after는 상대 HP %)."""
        if to_id(move) in SKIP_DAMAGE_MOVES:
            return
        mb = self.mon(sp)
        reqs = []
        for p in mb.particles:
            mine = {"set": my_set, "mega": my_mega, "boosts": my_boosts, "status": my_status or None}
            theirs = {"set": {k: p.set[k] for k in ("species", "item", "ability", "nature", "evs", "moves") if p.set.get(k)},
                      "mega": opp_mega, "boosts": opp_boosts, "status": opp_status or None}
            if attacker_is_opp:
                mine["hpPercent"] = 100.0 * before / my_maxhp if my_maxhp else None
                reqs.append({"attacker": theirs, "defender": mine, "move": move, "field": field, "crossCheck": False})
            else:
                theirs["hpPercent"] = before
                reqs.append({"attacker": mine, "defender": theirs, "move": move, "field": field, "crossCheck": False})
        for r in reqs:
            for side in ("attacker", "defender"):
                r[side] = {k: v for k, v in r[side].items() if v is not None}
        res = self.client.batch("/damage", reqs)
        like = []
        for p, r in zip(mb.particles, res):
            if not r.get("ok") or r["result"]["status"]:
                like.append(1.0)
                continue
            d = r["result"]
            rolls = d["critRolls"] if crit else d["rolls"]
            if attacker_is_opp:
                lost = before - after
                hits = sum(1 for x in rolls if abs(min(x, before) - lost) <= 1)
            else:
                mx = d["defender"]["maxhp"]
                hp0 = before * mx / 100
                hits = sum(1 for x in rolls if abs(100 * max(0.0, hp0 - x) / mx - after) <= 1.5)
            like.append(self.eps_damage + (1 - self.eps_damage) * hits / len(rolls))
        self.reweight(sp, like, f"데미지 ({move})")

    # ---------- 요약 ----------
    def summary(self, sp: str, top: int = 3) -> dict[str, Any]:
        mb = self.mon(sp)
        return {"species": sp, "ess": round(mb.ess(), 1),
                "items": [(k, round(v, 3)) for k, v in mb.marginal("item")[:top]],
                "abilities": [(k, round(v, 3)) for k, v in mb.marginal("ability")[:top]],
                "mega_stone": round(sum(p.w for p in mb.particles if p.set.get("mega_species")), 3)}

    def entropy(self, sp: str) -> float:
        return -sum(p.w * math.log(p.w) for p in self.mon(sp).particles if p.w > 0)
