"""기술 선택의 판단 품질 벤치마크 (전체 대전보다 빠르고 잡음이 적다).

국면: 봇끼리 둔 배틀(세트 원형 분포의 팀)의 결정 시점 (양쪽 모두 기술 선택, 1~8턴 중 무작위).
기준 값 V_ref(행동): 진짜 상태(상대 세트를 앎)에서 그 행동 + 상대는 최대 데미지 봇의 선택으로 한 수를 두고, 이후 봇끼리 R판 굴린 승률.
에이전트는 그 시점까지의 자기 시점 관찰만 받는다(세션 + 믿음 상태 + 탐색).
지표: 고른 분포의 기대 기준 값 E = Σ p_i V_ref(i), 후회 = max V_ref − E, 봇이 고른 행동의 V_ref와의 차이.
"""

from __future__ import annotations

import random
from typing import Any

import numpy as np

from pokechamp.battle.belief_check import archetype_team
from pokechamp.battle.events import LogConverter
from pokechamp.battle.search import Searcher, SearchConfig, abstract_actions
from pokechamp.battle.session import BattleSession, engine_set
from pokechamp.data.priors import Priors


def make_positions(conn, client, n: int, seed: str) -> list[dict[str, Any]]:
    rng = random.Random(f"{seed}-positions")
    w = Priors(conn).species_weights()
    pool = list(w)[:80]
    weights = [w[s] for s in pool]
    out = []
    g = 0
    while len(out) < n and g < n * 4:
        g += 1
        mine = [engine_set(s) for s in archetype_team(conn, rng, pool, weights)]
        opp = [engine_set(s) for s in archetype_team(conn, rng, pool, weights)]
        picks = {s: "team " + "".join(str(k + 1) for k in rng.sample(range(6), 3)) for s in ("p1", "p2")}
        r = client.new_battle(mine, opp, seed=f"{seed}-g{g}", picks=picks)
        target = rng.randint(1, 8)
        logs = [r["log"]]
        st = r["state"]
        while not r.get("ended"):
            rq = r["requests"]
            both_move = all(rq.get(s) and not rq[s].get("wait") and not rq[s].get("forceSwitch") for s in ("p1", "p2"))
            if both_move and st["turn"] >= target:
                out.append({"my_team": mine, "opp_team": opp, "picks": picks, "logs": logs, "state": st,
                            "request": rq["p1"], "turn": st["turn"]})
                break
            la = client.legal_actions(st)
            ch = {s: client.batch("/bot_choice", [{"state": st, "side": s, "seed": f"{seed}-{g}-{st['turn']}"}])[0]["result"]["choice"]
                  if la[s] else None for s in ("p1", "p2")}
            r = client.step(st, **ch)
            st = r["state"]
            logs.append(r["log"])
    return out


def reference(client, pos: dict, reps: int, seed: str, resolve) -> dict[str, Any]:
    st = pos["state"]
    la = client.legal_actions(st)
    acts = abstract_actions(la["p1"], pos["request"], resolve)
    bot_opp = client.batch("/bot_choice", [{"state": st, "side": "p2", "seed": f"{seed}-ref"}])[0]["result"]["choice"]
    bot_me = client.batch("/bot_choice", [{"state": st, "side": "p1", "seed": f"{seed}-ref"}])[0]["result"]["choice"]
    items = [{"state": st, "choices": [[a["choice"], bot_opp]], "policy": "maxdamage", "reps": reps, "seed": f"{seed}-ref"}
             for a in la["p1"]]
    res = client.batch("/simulate", items)
    vals = {ab: r["result"]["p1WinRate"] for ab, r in zip(acts, res)}
    bot_ab = next((ab for a, ab in zip(la["p1"], acts) if a["choice"] == bot_me), None)
    return {"values": vals, "bot": bot_ab}


def run(conn, client, positions: list[dict], configs: dict[str, SearchConfig], ref_reps: int = 200,
        log=lambda s: None) -> dict[str, Any]:
    from pokechamp.battle.events import SpeciesResolver
    res_ = SpeciesResolver(conn)
    rows = {name: [] for name in configs}
    bot_rows, best_rows, unif_rows = [], [], []
    for pi, pos in enumerate(positions):
        ref = reference(client, pos, ref_reps, f"bench{pi}", res_)
        vals = ref["values"]
        best = max(vals.values())
        best_rows.append(best)
        bot_rows.append(vals.get(ref["bot"], np.nan))
        unif_rows.append(float(np.mean(list(vals.values()))))
        for name, cfg in configs.items():
            sess = BattleSession(conn, pos["my_team"], [s["species"] for s in pos["opp_team"]], client, seed=f"b{pi}", derive_field=False)
            conv = LogConverter(sess.res)
            for chunk in pos["logs"]:
                sess.feed(conv.feed(chunk))
            brought = [pos["my_team"][int(c) - 1]["species"] for c in pos["picks"]["p1"].split()[1]]
            out = Searcher(sess, brought, cfg).choose()
            e = sum(p * vals.get(a, np.nan) for a, p in zip(out["actions"], out["probabilities"]) if p > 0)
            rows[name].append(e)
        log(f"  국면 {pi}: 최선 {best:.2f} 봇 {bot_rows[-1]:.2f} 무작위 {unif_rows[-1]:.2f} | "
            + " ".join(f"{n} {rows[n][-1]:.2f}" for n in configs))
    def stat(xs):
        a = np.array(xs, dtype=float)
        a = a[~np.isnan(a)]
        return {"mean": round(float(a.mean()), 4), "se": round(float(a.std(ddof=1) / np.sqrt(len(a))), 4), "n": int(len(a))}
    out = {"positions": len(positions), "ref_reps": ref_reps, "best": stat(best_rows), "bot": stat(bot_rows),
           "uniform": stat(unif_rows)}
    for n in configs:
        out[n] = stat(rows[n])
        d = np.array(rows[n]) - np.array(bot_rows)
        d = d[~np.isnan(d)]
        out[n]["vs_bot"] = {"mean": round(float(d.mean()), 4), "se": round(float(d.std(ddof=1) / np.sqrt(len(d))), 4)}
    return out
