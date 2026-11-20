"""탐색 에이전트 대 기준선 봇 검증 (PROJECT_PLAN 11절 단계 5 인수 기준: 기준선 봇 대비 승률 향상).

한 판: 진짜 엔진 배틀에서 한쪽은 탐색 에이전트(자기 시점 로그 → 사건 → 세션(추적 + 믿음) → 탐색), 다른 쪽은 최대 데미지 봇.
에이전트는 상대 세트를 모른다(믿음 상태로 추정). 상대 팀은 세트 원형 분포에서 뽑는다.

짝지은 비교: 같은 팀 X·Y, 시드, 선출로
  (a) 에이전트(X) 대 봇(Y)    (a') 봇(X) 대 봇(Y)   — 차이 = 에이전트 효과 (X 쪽)
  (b) 봇(X) 대 에이전트(Y)    (b') 봇(X) 대 봇(Y)   — 차이 = 에이전트 효과 (Y 쪽)
짝 하나의 점수 = 에이전트 판 점수 평균 − 기준 판 점수 평균.
"""

from __future__ import annotations

import math
import random
from typing import Any

from pokechamp.battle.belief_check import archetype_team
from pokechamp.battle.events import LogConverter
from pokechamp.battle.search import Searcher, SearchConfig, sample_action, to_choice
from pokechamp.battle.session import BattleSession, engine_set
from pokechamp.data.priors import Priors
from pokechamp.engine_client import EngineClient


def play(conn, client: EngineClient, team_a: list[dict], team_b: list[dict], picks: dict[str, str], seed: str,
         agent_side: str = "p1", cfg: SearchConfig | None = None, max_steps: int = 300) -> dict[str, Any]:
    """agent_side 쪽이 에이전트, 반대쪽이 최대 데미지 봇. team_a = p1, team_b = p2."""
    cfg = cfg or SearchConfig(seed=seed)
    opp_side = "p2" if agent_side == "p1" else "p1"
    mine, theirs = (team_a, team_b) if agent_side == "p1" else (team_b, team_a)
    r = client.new_battle(team_a, team_b, seed=seed, picks=picks)
    sess = BattleSession(conn, mine, [s["species"] for s in theirs], client, seed=seed)
    brought = [mine[int(ch) - 1]["species"] for ch in picks[agent_side].split()[1]]
    srch = Searcher(sess, brought, cfg)
    conv = LogConverter(sess.res, me=agent_side)
    rng = random.Random(f"{seed}-agent")
    st, lines = r["state"], r["log"]
    decisions, errors = 0, []
    for _ in range(max_steps):
        sess.feed(conv.feed(lines))
        if r.get("ended"):
            break
        la = client.legal_actions(st)
        rq = r["requests"]
        choice = None
        my_rq = rq.get(agent_side)
        if la[agent_side] and my_rq and not my_rq.get("wait"):
            try:
                if my_rq.get("forceSwitch"):
                    cands = [sess.res(my_rq["side"]["pokemon"][a["slot"] - 1]["details"].split(",")[0])
                             for a in la[agent_side] if a["kind"] == "switch"]
                    if cands:
                        best = srch.choose_switch(cands)["choice"]
                        choice = to_choice(("switch", best), la[agent_side], my_rq, sess.res)
                else:
                    res = srch.choose()
                    action = sample_action(res, rng)
                    choice = to_choice(action, la[agent_side], my_rq, sess.res)
                decisions += 1
            except Exception as e:  # noqa: BLE001  탐색 실패는 기록하고 기준선 행동으로 대신한다
                errors.append(f"턴 {st.get('turn')}: {e}")
            if choice is None:
                choice = client.batch("/bot_choice", [{"state": st, "side": agent_side, "seed": f"{seed}-fb"}])[0]["result"]["choice"]
        opp_choice = None
        if la[opp_side]:
            opp_choice = client.batch("/bot_choice", [{"state": st, "side": opp_side, "seed": f"{seed}-{st.get('turn')}"}])[0]["result"]["choice"]
        r = client.step(st, **{agent_side: choice, opp_side: opp_choice})
        if r.get("errors"):
            errors.append(f"엔진 거부: {r['errors']}")
            r = client.step(st, **{agent_side: "default", opp_side: opp_choice or "default"})
        st, lines = r["state"], r["log"]
    winner = r.get("winner") if r.get("ended") else None
    score = 1.0 if winner == agent_side else 0.0 if winner else 0.5
    return {"score": score, "winner": winner, "turns": st.get("turn"), "decisions": decisions, "errors": errors,
            "simulations": srch.calls, "evidence": sess.evidence}


def bot_game(client: EngineClient, team_a, team_b, picks, seed) -> dict[str, float]:
    r = client.batch("/simulate", [{"p1": team_a, "p2": team_b, "picks": picks, "seed": seed, "policy": "maxdamage", "reps": 1}])[0]
    w = r["result"]["wins"]
    return {"p1": 1.0 if w["p1"] else 0.0 if w["p2"] else 0.5}


def evaluate(conn, client: EngineClient, n_pairs: int, seed: str, cfg: SearchConfig | None = None,
             log=lambda s: None, start: int = 0) -> dict[str, Any]:
    rng = random.Random(f"{seed}-agenteval-{start}")
    w = Priors(conn).species_weights()
    pool = list(w)[:80]
    weights = [w[s] for s in pool]
    pairs = []
    for i in range(start, start + n_pairs):
        x = [engine_set(s) for s in archetype_team(conn, rng, pool, weights)]
        y = [engine_set(s) for s in archetype_team(conn, rng, pool, weights)]
        picks = {s: "team " + "".join(str(k + 1) for k in rng.sample(range(6), 3)) for s in ("p1", "p2")}
        sd = f"{seed}-{i}"
        base = bot_game(client, x, y, picks, sd)["p1"]                      # 봇(X) 대 봇(Y), X 관점
        a = play(conn, client, x, y, picks, sd, "p1", cfg or SearchConfig(seed=sd))
        b = play(conn, client, x, y, picks, sd, "p2", cfg or SearchConfig(seed=sd))
        diff = ((a["score"] - base) + (b["score"] - (1 - base))) / 2
        pairs.append({"pair": i, "agent_x": a["score"], "agent_y": b["score"], "bot_x": base, "diff": diff,
                      "errors": a["errors"] + b["errors"], "decisions": a["decisions"] + b["decisions"]})
        log(f"  짝 {i}: 에이전트 X {a['score']} / Y {b['score']}, 봇끼리 X {base} → 차이 {diff:+.2f}")
    return summarize(pairs)


def summarize(pairs: list[dict]) -> dict[str, Any]:
    n = len(pairs)
    d = [p["diff"] for p in pairs]
    mean = sum(d) / n if n else 0.0
    sd = math.sqrt(sum((x - mean) ** 2 for x in d) / max(1, n - 1)) if n else 0.0
    agent = [(p["agent_x"] + p["agent_y"]) / 2 for p in pairs]
    am = sum(agent) / n if n else 0.0
    asd = math.sqrt(sum((x - am) ** 2 for x in agent) / max(1, n - 1)) if n else 0.0
    return {"pairs": n, "agent_win_rate": am, "agent_ci": (am - 1.96 * asd / math.sqrt(max(1, n)), am + 1.96 * asd / math.sqrt(max(1, n))),
            "diff": mean, "diff_ci": (mean - 1.96 * sd / math.sqrt(max(1, n)), mean + 1.96 * sd / math.sqrt(max(1, n))),
            "errors": sum(len(p["errors"]) for p in pairs), "error_examples": [e for p in pairs for e in p["errors"]][:5],
            "decisions": sum(p["decisions"] for p in pairs), "detail": pairs}
