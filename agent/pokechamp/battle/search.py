"""기술 선택 탐색 (PROJECT_PLAN 9.3, 단계 5 첫 버전).

1. 결정화: 믿음 상태에서 상대 세트를 K개 뽑고(아직 안 나온 상대 선출 자리는 남은 종으로 채움), 추적 상태와 함께 엔진 상태를 만든다(/build_state).
2. 행렬: 내 행동(기술 × 메가, 교체) × 그 샘플의 상대 행동마다 한 수를 둔 뒤 최대 데미지 봇으로 끝까지 진행해 승패를 본다(/simulate).
   변화기·교체의 가치는 첫 수에서 이렇게 평가된다. 이후 수는 기준선 봇 정책이다(깊이 1 + 롤아웃).
3. 풀이: 상대가 자기 세트를 아는 베이즈 게임으로 보고 선형계획으로 푼다: max_x Σ_k (1/K) min_j Σ_i x_i M_k[i, j].
   최종 = (1 − λ) × 균형 + λ × 상대 모델(최대 데미지 봇의 선택)에 대한 최선 응수.
4. 기절 교체: 후보마다 그 포켓몬이 나온 상태에서 끝까지 진행한 승률이 가장 높은 쪽.
행동은 추상화해서 다룬다: ("move", 기술 id, 메가 여부) / ("switch", 종 id). 진짜 배틀과 재구성 상태의 슬롯 번호가 달라서다.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import linprog

from pokechamp.battle.session import BattleSession, engine_set
from pokechamp.data.static.names import to_id

CHOICE_ITEMS = {"choiceband", "choicespecs", "choicescarf"}


@dataclass
class SearchConfig:
    samples: int = 8          # 결정화 샘플 수 K
    reps: int = 3             # 칸·샘플마다 롤아웃 판 수 (판단 품질 벤치마크: 6×1 후회 0.079 → 8×3 공통 난수 0.058)
    mix: float = 0.3          # 상대 모델 최선 응수 비율 λ
    seed: str = "search"
    crn: bool = True          # 공통 난수: 같은 샘플 안에서는 칸이 달라도 같은 롤아웃 시드 (행동 간 비교의 잡음을 줄인다)
    # model(기본): 상대 모델 대비 값만 (λ=1과 같음). 짝지은 대전 120짝에서 봇 대비 +21.7%p (docs/VERIFIED.md).
    # bayes: 상대가 자기 세트를 아는 베이즈 게임 + λ 혼합. 지금 롤아웃 수에서는 칸값 잡음 때문에 봇보다 낫지 않았다(−2.5%p).
    solve: str = "model"
    model_reps: int = 12      # model: 샘플마다 상대 모델의 행동 한 열만 계산하므로 판 수를 늘린다


def abstract_actions(la: list[dict], request: dict, resolve) -> list[tuple]:
    """엔진 합법 행동 → 추상 행동 (요청의 side.pokemon으로 교체 대상 종을 찾는다)."""
    mons = (request or {}).get("side", {}).get("pokemon", [])
    out = []
    for a in la:
        if a["kind"] == "move":
            out.append(("move", a["move"], bool(a.get("mega"))))
        elif a["kind"] == "switch":
            sp = mons[a["slot"] - 1]["details"].split(",")[0] if a["slot"] - 1 < len(mons) else "?"
            out.append(("switch", resolve(sp)))
        else:
            out.append((a["kind"],))
    return out


def to_choice(action: tuple, la: list[dict], request: dict, resolve) -> str | None:
    for a, ab in zip(la, abstract_actions(la, request, resolve)):
        if ab == action:
            return a["choice"]
    return None


def solve_bayes(Ms: list[np.ndarray]) -> tuple[float, np.ndarray]:
    """max_x Σ_k w_k z_k, z_k ≤ (x M_k)_j. 변수 [x(nA), z(K)]."""
    nA, K = Ms[0].shape[0], len(Ms)
    c = np.concatenate([np.zeros(nA), -np.full(K, 1.0 / K)])
    A_ub, b_ub = [], []
    for k, M in enumerate(Ms):
        for j in range(M.shape[1]):
            row = np.zeros(nA + K)
            row[:nA] = -M[:, j]
            row[nA + k] = 1.0
            A_ub.append(row)
            b_ub.append(0.0)
    A_eq = np.concatenate([np.ones(nA), np.zeros(K)])[None, :]
    res = linprog(c, A_ub=np.array(A_ub), b_ub=b_ub, A_eq=A_eq, b_eq=[1.0],
                  bounds=[(0, None)] * nA + [(None, None)] * K, method="highs")
    x = np.clip(res.x[:nA], 0, None)
    return float(-res.fun), x / x.sum()


class Searcher:
    def __init__(self, session: BattleSession, my_brought: list[str], cfg: SearchConfig | None = None):
        self.s = session
        self.client = session.client
        self.my_brought = [session.res(x) for x in my_brought]
        self.cfg = cfg or SearchConfig()
        self.rng = random.Random(f"{self.cfg.seed}-searcher")
        self.calls = 0

    # ---------- 결정화 ----------
    def _patch(self, m, mine: bool, active: bool) -> dict:
        p: dict[str, Any] = {}
        if m.fainted:
            return {"fainted": True, "mega": m.mega}
        if mine and m.hp_abs is not None:
            p["hp"] = m.hp_abs
        else:
            p["hpPct"] = m.hp
        p["status"] = m.status
        cur_item = m.held if m.held is not None else m.item
        if active:
            p["boosts"] = {k: v for k, v in m.boosts.items() if v}
            if cur_item in CHOICE_ITEMS and m.last_move and not m.mega and not m.item_consumed:
                p["choiceLock"] = m.last_move
        if m.mega:
            p["mega"] = True
        if m.held is not None:                      # 트릭 등으로 도구가 바뀌었다
            if m.held:
                p["item"] = m.held
            else:
                p["itemConsumed"] = True
        elif m.item_consumed:
            p["itemConsumed"] = True
        return p

    def determinize(self, k: int, active_me: str | None = None) -> dict:
        st = self.s.state
        me_active = active_me or st.me.active
        my_order = [me_active] + [x for x in self.my_brought if x != me_active]
        my_sets = {self.s.res(t["species"]): t for t in self.s.my_team}
        p1 = [engine_set(my_sets[x]) for x in my_order] + [engine_set(t) for t in self.s.my_team
                                                            if self.s.res(t["species"]) not in my_order]
        opp_seen = [x for x in st.opp.order if st.opp.mons[x].seen]
        unseen = [x for x in st.opp.order if not st.opp.mons[x].seen]
        self.rng.shuffle(unseen)
        brought = opp_seen + unseen[: max(0, 3 - len(opp_seen))]
        active = st.opp.active
        if not active or st.opp.mons[active].fainted:
            # 상대가 기절 교체를 아직 고르지 않았다: 샘플마다 남은 포켓몬 중 하나를 가정한다
            alive = [x for x in brought if not st.opp.mons[x].fainted]
            active = self.rng.choice(alive) if alive else active
        opp_order = [active] + [x for x in brought if x != active]
        rest = [x for x in st.opp.order if x not in opp_order]
        sets = {}
        for x in opp_order + rest:
            sets[x] = self.s.belief.mon(x).sample(self.rng).set
        # 도구 중복 금지: 겹치면 다시 뽑아 본다
        used = set()
        for x in opp_order:
            for _ in range(10):
                it = sets[x].get("item")
                if not it or it not in used:
                    break
                sets[x] = self.s.belief.mon(x).sample(self.rng).set
            if sets[x].get("item"):
                used.add(sets[x]["item"])
        p2 = [engine_set(sets[x]) for x in opp_order + rest]
        mons = {"p1": [self._patch(st.me.mons[x], True, i == 0) for i, x in enumerate(my_order)],
                "p2": [self._patch(st.opp.mons[x], False, i == 0 and x == st.opp.active) if st.opp.mons[x].seen else None
                       for i, x in enumerate(opp_order)]}
        w, t = st.weather, st.terrain
        field = {}
        if w.kind != "none":
            rem = w.remaining(max(st.turn, 1))
            field.update(weather=w.kind, weatherTurns=max(1, rem[0] if rem else 5))
        if t.kind != "none":
            rem = t.remaining(max(st.turn, 1))
            field.update(terrain=t.kind, terrainTurns=max(1, rem[0] if rem else 5))
        def conds(sd) -> dict[str, int]:      # 벽·순풍은 남은 턴 하한, 설치기는 겹 수
            # 이미 끝났을 수도 있는 것(남은 턴 하한 0)은 넣지 않는다
            out = {c: (clk.remaining(max(st.turn, 1)) or (0, 0))[0] for c, clk in sd.timed.items()}
            out = {c: n for c, n in out.items() if n >= 1}
            out.update(sd.hazards)
            return out
        extra: dict[str, Any] = {}
        side_conds = {k: v for k, v in (("p1", conds(st.me)), ("p2", conds(st.opp))) if v}
        if side_conds:
            extra["sideConditions"] = side_conds
        if st.trickroom.kind == "trickroom":
            extra["trickRoom"] = max(1, (st.trickroom.remaining(max(st.turn, 1)) or (1, 1))[0])
        return {**extra, "p1": p1, "p2": p2, "seed": f"{self.cfg.seed}-det{k}-{st.turn}",
                "picks": {"p1": "team " + "".join(str(i + 1) for i in range(len(my_order))),
                          "p2": "team " + "".join(str(i + 1) for i in range(len(opp_order)))},
                "mons": mons, "field": field, "megaUsed": {"p1": st.me.mega_used, "p2": st.opp.mega_used},
                "turn": max(st.turn, 1)}

    def _states(self, active_me: str | None = None) -> list[dict]:
        dets = [self.determinize(k, active_me) for k in range(self.cfg.samples)]
        res = self.client.batch("/build_state", dets)
        return [r["result"] for r in res if r.get("ok")]

    # ---------- 기술 선택 ----------
    def choose(self) -> dict[str, Any]:
        views = self._states()
        if not views:
            raise RuntimeError("결정화 상태를 만들지 못했다")
        las = self.client.batch("/legal_actions", [{"state": v["state"]} for v in views])
        las = [r["result"] for r in las]
        my_acts = abstract_actions(las[0]["p1"], views[0]["requests"]["p1"], self.s.res)
        if self.cfg.solve == "model":
            return self._choose_model(views, las, my_acts)
        items, index = [], []
        Ms = []
        for k, (v, la) in enumerate(zip(views, las)):
            mine = {ab: a["choice"] for a, ab in zip(la["p1"], abstract_actions(la["p1"], v["requests"]["p1"], self.s.res))}
            opp = la["p2"] or [{"choice": None}]
            Ms.append(np.full((len(my_acts), len(opp)), 0.5))
            for i, ab in enumerate(my_acts):
                if ab not in mine:
                    continue
                for j, oa in enumerate(opp):
                    sd = (f"{self.cfg.seed}-{self.s.state.turn}-{k}" if self.cfg.crn
                          else f"{self.cfg.seed}-{self.s.state.turn}-{k}-{i}-{j}")
                    items.append({"state": v["state"], "choices": [[mine[ab], oa["choice"]]], "policy": "maxdamage",
                                  "reps": self.cfg.reps, "seed": sd})
                    index.append((k, i, j))
        res = []
        for b in range(0, len(items), 400):
            res += self.client.batch("/simulate", items[b:b + 400])
        self.calls += len(items)
        for (k, i, j), r in zip(index, res):
            if r.get("ok"):
                Ms[k][i, j] = r["result"]["p1WinRate"]
        value, x = solve_bayes(Ms)
        bots = self.client.batch("/bot_choice", [{"state": v["state"], "side": "p2", "seed": f"{self.cfg.seed}-bot{k}"}
                                                 for k, v in enumerate(views)])
        model_vals = np.zeros(len(my_acts))
        for k, (r, la) in enumerate(zip(bots, las)):
            ch = (r.get("result") or {}).get("choice")
            opp = [a["choice"] for a in la["p2"]] or [None]
            j = opp.index(ch) if ch in opp else None
            model_vals += Ms[k][:, j] if j is not None else Ms[k].mean(axis=1)
        model_vals /= len(views)
        br = int(np.argmax(model_vals))
        if self.cfg.solve == "model":
            x = np.zeros(len(my_acts))
            x[br] = 1.0
        final = (1 - self.cfg.mix) * x
        final[br] += self.cfg.mix
        eq_vals = np.array([np.mean([M[i].min() if M.shape[1] else 0.5 for M in Ms]) for i in range(len(my_acts))])
        return {"actions": my_acts, "probabilities": final / final.sum(), "equilibrium": x, "value": value,
                "vs_model": model_vals, "worst_case": eq_vals, "best_response": br, "samples": len(views),
                "simulations": len(items)}

    def _choose_model(self, views: list[dict], las: list[dict], my_acts: list[tuple]) -> dict[str, Any]:
        """상대 모델(최대 데미지 봇)의 행동만 계산한다: 샘플마다 그 행동 한 열 × 내 행동 × model_reps판."""
        bots = self.client.batch("/bot_choice", [{"state": v["state"], "side": "p2", "seed": f"{self.cfg.seed}-bot{k}"}
                                                 for k, v in enumerate(views)])
        items, index = [], []
        for k, (v, la, b) in enumerate(zip(views, las, bots)):
            mine = {ab: a["choice"] for a, ab in zip(la["p1"], abstract_actions(la["p1"], v["requests"]["p1"], self.s.res))}
            oc = (b.get("result") or {}).get("choice")
            for i, ab in enumerate(my_acts):
                if ab in mine:
                    items.append({"state": v["state"], "choices": [[mine[ab], oc]], "policy": "maxdamage",
                                  "reps": self.cfg.model_reps, "seed": f"{self.cfg.seed}-{self.s.state.turn}-{k}"})
                    index.append((k, i))
        res = []
        for b in range(0, len(items), 400):
            res += self.client.batch("/simulate", items[b:b + 400])
        self.calls += len(items) * self.cfg.model_reps
        tot, cnt = np.zeros(len(my_acts)), np.zeros(len(my_acts))
        for (k, i), r in zip(index, res):
            if r.get("ok"):
                tot[i] += r["result"]["p1WinRate"]
                cnt[i] += 1
        vals = np.where(cnt > 0, tot / np.maximum(cnt, 1), 0.0)
        br = int(np.argmax(vals))
        p = np.zeros(len(my_acts))
        p[br] = 1.0
        return {"actions": my_acts, "probabilities": p, "equilibrium": p, "value": float(vals[br]), "vs_model": vals,
                "worst_case": vals, "best_response": br, "samples": len(views), "simulations": len(items) * self.cfg.model_reps}

    def choose_switch(self, candidates: list[str]) -> dict[str, Any]:
        """기절 교체: 후보마다 그 포켓몬이 나온 상태에서 끝까지 진행한 승률."""
        vals = {}
        for c in candidates:
            views = self._states(active_me=c)
            items = [{"state": v["state"], "policy": "maxdamage", "reps": max(2, self.cfg.reps * 2),
                      "seed": f"{self.cfg.seed}-sw-{c}-{k}"} for k, v in enumerate(views)]
            res = self.client.batch("/simulate", items)
            self.calls += len(items)
            wr = [r["result"]["p1WinRate"] for r in res if r.get("ok")]
            vals[c] = float(np.mean(wr)) if wr else 0.0
        best = max(vals, key=vals.get)
        return {"choice": best, "values": vals}


def sample_action(result: dict[str, Any], rng: random.Random) -> tuple:
    acts, p = result["actions"], result["probabilities"]
    return acts[rng.choices(range(len(acts)), weights=list(np.maximum(p, 0)))[0]]


def describe(result: dict[str, Any], names: dict[str, str] | None = None, top: int = 4) -> str:
    """'기술A 58% / 교체B 31% / 기술C 11%  (기대 승률 63%)' (9.4 출력 예)."""
    names = names or {}
    order = np.argsort(-result["probabilities"])
    parts = []
    for i in order[:top]:
        pr = result["probabilities"][i]
        if pr < 0.01:
            break
        a = result["actions"][i]
        if a[0] == "move":
            label = names.get(a[1], a[1]) + (" + 메가" if a[2] else "")
        elif a[0] == "switch":
            label = f"교체 {names.get(a[1], a[1])}"
        else:
            label = a[0]
        parts.append(f"{label} {pr:.0%}")
    return " / ".join(parts) + f"  (기대 승률 {result['value']:.0%})"


def mega_label(sp: str) -> str:
    return to_id(sp)
