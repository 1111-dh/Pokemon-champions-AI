"""대본 배틀: 엔진 배틀(양쪽 기준선 봇)의 관찰을 사용자 입력처럼 텍스트 세션에 넣어 끝까지 진행한다 (테스트·복기 점검용, source='sim')."""

from __future__ import annotations

import random

from pokechamp.battle.belief_check import archetype_team
from pokechamp.battle.cli_session import TextBattle
from pokechamp.battle.events import LogConverter, SpeciesResolver
from pokechamp.battle.search import SearchConfig
from pokechamp.battle.selfcheck import sparse
from pokechamp.battle.session import engine_set
from pokechamp.data.priors import Priors
from pokechamp.input.battle_text import BattleText


def scripted_battle(conn, client, seed: str, cfg: SearchConfig | None = None, log_dir=None, say=lambda s: None) -> dict:
    rng = random.Random(f"{seed}-scripted")
    w = Priors(conn).species_weights()
    pool = list(w)[:60]
    mine = [engine_set(s) for s in archetype_team(conn, rng, pool, [w[s] for s in pool])]
    opp = [engine_set(s) for s in archetype_team(conn, rng, pool, [w[s] for s in pool])]
    r = client.new_battle(mine, opp, seed=seed, picks={"p1": "team 213", "p2": "team 123"})
    bt, res = BattleText(conn), SpeciesResolver(conn)
    conv = LogConverter(res)
    conv.feed(r["log"])
    chunks, st = [], r["state"]
    while not r.get("ended") and len(chunks) < 150:
        la = client.legal_actions(st)
        ch = {s: client.batch("/bot_choice", [{"state": st, "side": s, "seed": f"{seed}-{st['turn']}"}])[0]["result"]["choice"]
              if la[s] else None for s in ("p1", "p2")}
        r = client.step(st, **ch)
        st = r["state"]
        text = bt.render(sparse([e for e in conv.feed(r["log"]) if e.kind != "turn"]))
        if text:
            chunks.append(text)
    queue = iter(chunks)
    winner = r.get("winner")

    def ask(prompt: str) -> str:
        if "내 선출" in prompt:
            return "213"
        if "상대 선봉" in prompt:
            return bt.name("species", res(opp[0]["species"]))
        if "이번 턴" in prompt:
            return next(queue, "끝")
        if "결과" in prompt:
            return "승" if winner == "p1" else "패"
        return ""
    tb = TextBattle(conn, mine, [s["species"] for s in opp], client, ask=ask, say=say,
                    cfg=cfg or SearchConfig(seed=seed, samples=2, model_reps=2), seed=seed, log_dir=log_dir, source="sim")
    rec = tb.run(recommend_selection=False)
    rec["engine_winner"] = winner
    return rec
