"""믿음 상태 검증 (단계 5): 상대 세트를 원형 분포에서 뽑아 엔진 배틀을 진행하고, 관찰 사건을 세션에 넣은 뒤
실제 세트의 사후확률이 사전확률보다 얼마나 올라가는지 잰다.

지표(배틀에 나온 상대 포켓몬마다, 배틀이 끝난 시점):
- P(실제 도구), P(실제 특성), P(실제 기술 4개 조합), P(실제 스피드 실수치) — 사전(증거 전 파티클 가중치) 대 사후
- 모순: 증거 때문에 모든 가중치가 0이 된 횟수 (사전 분포에서 뽑은 상대라면 0이어야 한다)
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any

from pokechamp.battle.events import LogConverter
from pokechamp.battle.session import BattleSession, engine_set
from pokechamp.data.db import active_version
from pokechamp.data.priors import Priors
from pokechamp.data.static.names import to_id
from pokechamp.engine_client import EngineClient
from pokechamp.sets.archetypes import load_archetypes, sample_sets


def archetype_team(conn, rng: random.Random, species_pool: list[str], weights: list[float]) -> list[dict]:
    regulation = active_version(conn)["regulation"]
    team, used_items, used_sp = [], set(), set()
    while len(team) < 6:
        sp = rng.choices(species_pool, weights=weights)[0]
        if sp in used_sp:
            continue
        archs = load_archetypes(conn, sp, regulation)
        if not archs:
            used_sp.add(sp)
            continue
        for _ in range(20):
            s = sample_sets(archs, 1, rng)[0]
            if not s["item"] or s["item"] not in used_items:
                break
        used_sp.add(sp)
        if s["item"]:
            used_items.add(s["item"])
        team.append(s)
    return team


def _p(mb, pred) -> float:
    return sum(p.w for p in mb.particles if pred(p))


def run(conn, client: EngineClient, n_battles: int, seed: str, use_damage: bool = True, n_particles: int = 48) -> dict[str, Any]:
    rng = random.Random(f"{seed}-beliefcheck")
    w = Priors(conn).species_weights()
    pool = list(w)[:80]
    weights = [w[s] for s in pool]
    agg: dict[str, list[tuple[float, float]]] = defaultdict(list)
    calib: list[tuple[float, int]] = []          # (예측 확률, 맞음 여부) — 도구 후보마다 (PROJECT_PLAN 10절 보정 곡선)
    logloss = {"prior": [], "posterior": []}
    contradictions, evidence = 0, defaultdict(int)
    for b in range(n_battles):
        mine = client.batch("/random_team", [{"seed": f"{seed}-{b}-me"}])[0]["result"]["team"]
        opp = archetype_team(conn, rng, pool, weights)
        picks = {s: "team " + "".join(str(i + 1) for i in rng.sample(range(6), 3)) for s in ("p1", "p2")}
        r = client.new_battle(mine, [engine_set(s) for s in opp], seed=f"{seed}-{b}", picks=picks)
        sess = BattleSession(conn, mine, [s["species"] for s in opp], client, n_particles=n_particles, derive_field=False,
                             seed=f"{seed}-{b}", use_damage=use_damage)
        prior = {sp: [p.w for p in mb.particles] for sp, mb in sess.belief.mons.items()}
        prior_parts = {sp: list(mb.particles) for sp, mb in sess.belief.mons.items()}
        conv = LogConverter(sess.res)
        st, lines = r["state"], r["log"]
        for _ in range(200):
            warns = sess.feed(conv.feed(lines))
            contradictions += sum(1 for x in warns if "맞지 않는다" in x)
            if r.get("ended"):
                break
            la = client.legal_actions(st)
            c1 = rng.choice(la["p1"])["choice"] if la["p1"] else None
            c2 = rng.choice(la["p2"])["choice"] if la["p2"] else None
            r = client.step(st, p1=c1, p2=c2)
            st, lines = r["state"], r["log"]
        for k, v in sess.evidence.items():
            evidence[k] += v
        truth = {sess.res(s["species"]): s for s in opp}
        true_spe = {}
        tres = client.batch("/stats", [{"set": engine_set(s)} for s in opp])
        for s, tr in zip(opp, tres):
            true_spe[sess.res(s["species"])] = tr["result"]["spe"] if tr.get("ok") else None
        for sp, mon in sess.state.opp.mons.items():
            if not mon.seen or sp not in truth:
                continue
            t = truth[sp]
            mb = sess.belief.mons[sp]

            class _Prior:
                particles = [type("P", (), {"w": wt, "set": p.set, "stats": p.stats})() for wt, p in zip(prior[sp], prior_parts[sp])]
            checks = {
                "item": lambda p: to_id(p.set.get("item") or "") == to_id(t.get("item") or ""),
                "ability": lambda p: to_id(p.set.get("ability") or "") == to_id(t.get("ability") or ""),
                "moves": lambda p: sorted(to_id(m) for m in p.set.get("moves", [])) == sorted(to_id(m) for m in t.get("moves", [])),
                "speed": lambda p: bool(p.stats) and p.stats.get("spe") == true_spe.get(sp),
            }
            for k, f in checks.items():
                agg[k].append((_p(_Prior, f), _p(mb, f)))
            true_item = to_id(t.get("item") or "")
            for item, pr in mb.marginal("item"):
                calib.append((float(pr), int(to_id(item or "") == true_item)))
            logloss["prior"].append(-math.log(max(1e-3, _p(_Prior, checks["item"]))))
            logloss["posterior"].append(-math.log(max(1e-3, _p(mb, checks["item"]))))
    out = {"battles": n_battles, "use_damage": use_damage, "particles": n_particles, "contradictions": contradictions,
           "evidence": dict(evidence)}
    for k, xs in agg.items():
        out[k] = {"mons": len(xs), "prior": round(sum(a for a, _ in xs) / len(xs), 3),
                  "posterior": round(sum(b for _, b in xs) / len(xs), 3),
                  "improved_share": round(sum(1 for a, b in xs if b > a + 1e-9) / len(xs), 3)}
    bins = []
    for lo in [i / 10 for i in range(10)]:
        xs = [(p, y) for p, y in calib if lo <= p < lo + 0.1 or (lo == 0.9 and p == 1.0)]
        if xs:
            bins.append({"range": f"{lo:.1f}~{lo + 0.1:.1f}", "n": len(xs), "predicted": round(sum(p for p, _ in xs) / len(xs), 3),
                         "actual": round(sum(y for _, y in xs) / len(xs), 3)})
    out["item_calibration"] = bins
    out["item_logloss"] = {k: round(sum(v) / len(v), 3) for k, v in logloss.items() if v}
    return out
