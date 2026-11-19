"""선출 추천 검증 (PROJECT_PLAN 11절 단계 4 인수 기준).

1. 무작위 선출 대비 승률: 메타에서 합성한 상대 팀(세트는 원형 분포에서 새로 뽑음)마다
   - 내 선출: 추천 분포에서 뽑은 선출 vs 무작위 합법 선출(메가 소지자 제한은 같게)
   - 상대 선출: 무작위 (두 조건에 같은 선출)
   - 같은 시드로 진영을 바꿔 두 판씩 (짝지은 대전). 짝 점수 차이로 신뢰구간을 낸다.
   추천은 상대의 실제 세트를 모르고 이름만 본다 (세트는 통계 샘플로 추정).
2. 작은 사례에서 전체 계산과 더블 오라클: 보수 행렬 전체를 정밀 계산해 균형 값을 구하고,
   같은 정밀값을 쓰는 더블 오라클의 균형 값과 비교한다.
"""

from __future__ import annotations

import random
import time
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np

from pokechamp.data.static.names import to_id
from pokechamp.engine_client import EngineClient
from pokechamp.select.picks import enumerate_picks
from pokechamp.select.recommend import BATCH, Selector, _Side, _States, engine_set
from pokechamp.select.solver import PreciseCells, double_oracle
from pokechamp.teambuild.game import solve_exact
from pokechamp.teambuild.pool import build_threats
from pokechamp.teambuild.validate import opponents, paired_diff, summarize


def meta_opponents(sel: Selector, n: int, seed: str) -> list[list[dict[str, Any]]]:
    """파티 추천 검증과 같은 방식으로 상대 팀 n개를 합성한다 (위협 목록 + 동료 통계, 세트는 원형 분포에서)."""
    from pokechamp.config import teambuild_config
    from pokechamp.matchup.fast import MatchupCache
    from pokechamp.teambuild.pool import Pool

    cfg = teambuild_config()
    pool = Pool(MatchupCache(sel.conn, sel.client))
    threats = build_threats(sel.conn, pool, sel.priors.species_weights(), int(cfg["threat_species"]),
                            int(cfg["threat_archetypes"]), sel.regulation)
    b = SimpleNamespace(threats=threats, priors=sel.priors, cfg=cfg, conn=sel.conn, regulation=sel.regulation)
    return opponents(b, n, seed)


def base_species(sel: Selector, st: dict[str, Any]) -> str:
    sp = to_id(st["species"])
    for (base, _stone), mega in sel.stones.items():
        if mega == sp:
            return base
    return sp


def validate_selection(sel: Selector, team: list[dict[str, Any]], n: int, seed: str, max_games: int,
                       log: Callable[[str], None] = lambda s: None) -> dict[str, Any]:
    t0 = time.time()
    rng = random.Random(f"{seed}-select-validate")
    opps = meta_opponents(sel, n, seed)
    holders = {i for i, s in enumerate(team) if sel.mega_of(s)}
    legal = enumerate_picks(len(team), holders, int(sel.cfg["max_mega_in_pick"]))
    opp_picks = enumerate_picks(6)
    p1 = [engine_set(s) for s in team]
    items, meta = [], []
    rec_converged = 0
    for i, ot in enumerate(opps):
        r = sel.run(team, [base_species(sel, s) for s in ot], seed=f"{seed}-{i}", max_games=max_games)
        rec_converged += int(r["oracle"]["converged"])
        probs = np.array([p["probability"] for p in r["picks"]])
        rec = r["picks"][rng.choices(range(len(probs)), probs / probs.sum())[0]]["choice"]
        rnd = rng.choice(legal).choice()
        op = rng.choice(opp_picks).choice()
        p2 = [engine_set(s) for s in ot]
        for cond, mine in (("rec", rec), ("rnd", rnd)):
            s = f"{seed}-g{i}"
            items.append({"p1": p1, "p2": p2, "seed": s, "policy": "maxdamage", "reps": 1, "picks": {"p1": mine, "p2": op}})
            meta.append((i, cond, "p1"))
            items.append({"p1": p2, "p2": p1, "seed": s, "policy": "maxdamage", "reps": 1, "picks": {"p1": op, "p2": mine}})
            meta.append((i, cond, "p2"))
        if (i + 1) % 25 == 0:
            log(f"  선출 추천 {i + 1}/{n} ({time.time() - t0:.0f}초)")
    results = []
    for b in range(0, len(items), BATCH):
        results += sel.client.batch("/simulate", items[b:b + BATCH])
    per: dict[tuple[int, str], list[float]] = {}
    errors = 0
    for (i, cond, side), res in zip(meta, results):
        if not res.get("ok") or res["result"]["wins"]["unfinished"]:
            errors += 1
            continue
        w = res["result"]["wins"]
        per.setdefault((i, cond), []).append(1.0 if w[side] else 0.0 if (w["p1"] or w["p2"]) else 0.5)
    ok = [i for i in range(n) if per.get((i, "rec")) and per.get((i, "rnd"))]
    rec = summarize([float(np.mean(per[(i, "rec")])) for i in ok], errors)
    rnd = summarize([float(np.mean(per[(i, "rnd")])) for i in ok])
    return {"recommended": rec, "random": rnd, "diff": paired_diff(rec, rnd), "opponents": n, "max_games": max_games,
            "converged_share": rec_converged / max(1, n), "seconds": round(time.time() - t0, 1), "seed": seed,
            "policy": "최대 데미지 봇, 상대 선출 무작위, 진영 교대 짝지은 대전"}


def _precise_matrix(sel: Selector, team, my_picks, opp_teams, opp_picks, seed: str, samples: int, log, label: str) -> np.ndarray:
    full = PreciseCells()
    stats: dict[str, Any] = {"games": 0, "errors": 0, "games_per_second": None}
    ev = sel.evaluator(team, my_picks, opp_teams, opp_picks, full, seed, float("inf"), stats, max_games=10**12)
    cells = [((i, j), samples) for i in range(len(my_picks)) for j in range(len(opp_picks))]
    t0 = time.time()
    for k in range(0, len(cells), 200):
        ev(cells[k:k + 200])
        if (k // 200) % 5 == 0:
            log(f"  {label} {min(k + 200, len(cells))}/{len(cells)}칸 ({time.time() - t0:.0f}초)")
    return np.array([[full.value((i, j)) for j in range(len(opp_picks))] for i in range(len(my_picks))])


def full_vs_oracle(sel: Selector, team: list[dict[str, Any]], opp_species: list[str], seed: str, samples: int,
                   log: Callable[[str], None] = lambda s: None, budget_games: int = 3000,
                   save_path: str | None = None) -> dict[str, Any]:
    """작은 사례에서 전체 계산과 더블 오라클 비교.
    - T: 보수 행렬 전체를 정밀 계산(칸마다 samples판). 전체 균형은 T로 푼다. 더블 오라클은 T의 값을 읽는다
      (예산 무제한, 그리고 budget_games판 예산).
    - T2: 다른 시드(다른 상대 세트 샘플과 배틀 시드)로 따로 계산한 행렬. 전략의 질은 T2에서 잰다
      (T로 푼 전체 균형이 T의 잡음에 맞춰진 이점을 빼기 위해). 지표: 상대가 T2 기준 최선으로 응수할 때의 값(최악값)과
      상대가 무작위로 고를 때의 값."""
    t0 = time.time()
    cfg = sel.cfg
    opp_teams = sel.opponent_samples(opp_species, samples, seed)
    mine, theirs = _States(), _States()
    me = _Side(team, [mine.add(s, False) for s in team], [mine.add(s, True) if sel.mega_of(s) else None for s in team])
    opp = [_Side(t, [theirs.add(s, False) for s in t], [theirs.add(s, True) if s.get("mega_species") else None for s in t])
           for t in opp_teams]
    my_picks = enumerate_picks(len(team), me.holders, int(cfg["max_mega_in_pick"]))
    opp_picks = enumerate_picks(6)
    flat = sel.cache.scores([((a, ma), (b, mb)) for a, ma in mine.states for b, mb in theirs.states])
    M = np.array(flat).reshape(len(mine.states), len(theirs.states))
    A = sel.approx_matrix(me, my_picks, opp, opp_picks, M)
    T = _precise_matrix(sel, team, my_picks, opp_teams, opp_picks, seed, samples, log, "전체 정밀 계산")
    holdout_teams = sel.opponent_samples(opp_species, samples, f"{seed}-holdout")
    T2 = _precise_matrix(sel, team, my_picks, holdout_teams, opp_picks, f"{seed}-holdout", samples, log, "홀드아웃 계산")

    def run_oracle(budget: int | None):
        used = PreciseCells()

        def from_full(reqs):
            done = 0
            for c, _n in reqs:
                if c in used:
                    done += 1
                    continue
                if budget is not None and used.n_games() + samples > budget:
                    break
                used.add(c, T[c] * samples, samples)
                done += 1
            return done

        return double_oracle(A, from_full, used, initial=int(cfg["initial_candidates"]),
                             add_per_iter=int(cfg["add_per_iteration"]), eps=float(cfg["oracle_epsilon"]),
                             samples=samples, max_samples=samples, probe=int(cfg["probe_candidates"]),
                             probe_rounds=int(cfg["probe_rounds"]), final=str(cfg["final_solve"]))

    if save_path:   # 전략 비교를 다시 계산 없이 해 보도록 행렬을 저장한다
        np.savez_compressed(save_path, A=A, T=T, T2=T2, my_picks=[p.choice() for p in my_picks],
                            opp_picks=[p.choice() for p in opp_picks])
    v_full, x_full, _ = solve_exact(T)
    _, x_approx, _ = solve_exact(A)
    r_inf, r_bud = run_oracle(None), run_oracle(budget_games)
    uniform = np.full(len(my_picks), 1 / len(my_picks))

    def quality(x: np.ndarray) -> dict[str, float]:
        return {"worst_case": round(float((x @ T2).min()), 4), "vs_random_opponent": round(float((x @ T2).mean()), 4)}

    return {
        "cells": int(T.size), "samples_per_cell": samples, "seconds": round(time.time() - t0, 1),
        "value_full": round(float(v_full), 4), "value_oracle": round(r_inf.value, 4),
        "value_oracle_budget": round(r_bud.value, 4), "difference": round(r_inf.value - float(v_full), 4),
        "oracle_cells": len(r_inf.precise), "oracle_iterations": r_inf.iterations, "oracle_converged": r_inf.converged,
        "oracle_budget_cells": len(r_bud.precise), "budget_games": budget_games,
        "holdout": {"full_equilibrium": quality(x_full), "oracle": quality(r_inf.x), "oracle_budget": quality(r_bud.x),
                    "approx_only": quality(x_approx), "uniform_random": quality(uniform),
                    "full_value_on_holdout": round(float(solve_exact(T2)[0]), 4)},
    }
