"""텍스트 배틀 세션 `pokechamp battle` (PROJECT_PLAN 3.1 세션 흐름, 9절).

1. 통계 신선도를 보여 준다(오래됐으면 알리기만 하고 막지 않는다. 갱신은 사용자가 update-stats로).
2. 선출 추천 → 사용자가 실제로 낸 선출(슬롯 번호, 선봉 먼저)과 상대 선봉을 입력.
3. 매 턴: 추천 한 줄 → 사용자가 그 턴에 본 것을 입력 → 해석 결과를 한 줄로 되돌려 보여 주고 확인 → 적용.
   명령: "왜"(상세 이유), "상태"(추적 상태), "끝"(종료, 승패를 묻는다), "?"(입력 형식 도움말).
   내 포켓몬이 기절하면 교체 추천을 보여 주고 교체할 포켓몬을 받는다.
4. 판이 끝나면 기록을 data/logs/battles/에 저장한다(단계 6 복기용).
입출력은 ask/say 함수로 받는다(테스트에서 대본 입력으로 끝까지 진행해 본다).
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from pokechamp.battle.events import Event
from pokechamp.battle.search import Searcher, SearchConfig, describe
from pokechamp.battle.session import BattleSession
from pokechamp.input.battle_text import BattleText
from pokechamp.paths import ROOT

HELP = """입력 형식 (문장은 '/'나 줄바꿈으로 구분, 포켓몬 이름을 빼면 지금 나와 있는 포켓몬):
  상대 지진 / 상대 HP 40% / 내 HP 120/181 / 상대 선공 / 상대 메가진화 / 상대 공격 +1 / 상대 화상
  상대 교체 한카리아스 / 내 누리레느 기절 / 상대 도구 먹다남은음식 / 상대 특성 위협 / 날씨 모래바람
명령: 왜 (추천 이유) · 상태 (추적 상태) · 끝 (배틀 종료) · ? (이 도움말)"""


class TextBattle:
    def __init__(self, conn, my_team: list[dict], opp_species: list[str], client=None,
                 ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
                 cfg: SearchConfig | None = None, seed: str | None = None, log_dir: Path | None = None):
        self.conn, self.ask, self.say = conn, ask, say
        self.my_team, self.opp_species = my_team, opp_species
        self.seed = seed or datetime.now().strftime("%Y%m%d-%H%M%S")
        self.cfg = cfg or SearchConfig(seed=self.seed)
        self.bt = BattleText(conn)
        self.client = client
        self.log_dir = log_dir or ROOT / "data" / "logs" / "battles"
        self.record: dict[str, Any] = {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                       "seed": self.seed, "my_team": my_team, "opp_species": opp_species, "turns": []}

    def ko(self, table: str, i: str) -> str:
        return self.bt.name(table, i)

    # ---------- 시작 ----------
    def start(self, recommend_selection: bool = True) -> None:
        from pokechamp.data.priors import Priors
        pri = Priors(self.conn)
        self.say(f"통계: {pri.freshness().label()}")
        if w := pri.warning():
            self.say(f"⚠️ {w} 갱신하려면 배틀 뒤에 `pokechamp update-stats`를 실행하세요 (지금 진행해도 됩니다).")
        if recommend_selection:
            from pokechamp.select.recommend import Selector
            from pokechamp.select.report import text_report
            r = Selector(self.conn, self.client).run(self.my_team, self.opp_species, seed=self.seed)
            self.record["selection"] = {k: r[k] for k in ("picks", "expected_win_vs_equilibrium")}
            self.say(text_report(r).split("\n\n전체 기대 승률")[0])
        names = ", ".join(f"{i + 1}.{self.ko('species', self._id(s))}" for i, s in enumerate(self.my_team))
        while True:
            raw = self.ask(f"내 선출 (슬롯 번호, 선봉 먼저, 예: 513) [{names}]: ").strip()
            digits = [int(c) for c in raw if c.isdigit()]
            if len(digits) == 3 and len(set(digits)) == 3 and all(1 <= d <= len(self.my_team) for d in digits):
                break
            self.say("슬롯 번호 3개를 다시 입력하세요 (예: 513).")
        self.brought = [self.my_team[d - 1]["species"] for d in digits]
        while True:
            raw = self.ask("상대 선봉: ").strip()
            sp = self.bt.lookup("species", raw)
            if sp:
                break
            self.say(f"포켓몬 '{raw}'을(를) 모른다{self.bt.suggest('species', raw)}")
        self.sess = BattleSession(self.conn, self.my_team, self.opp_species, self.client, seed=self.seed)
        self.client = self.sess.client
        self.srch = Searcher(self.sess, self.brought, self.cfg)
        lead = self.sess.res(self.brought[0])
        events = [Event("switch", "me", species=lead), Event("switch", "opp", species=self.sess.res(sp))]
        for w in self.sess.feed(events):
            self.say(f"⚠️ {w}")
        self.record["brought"] = self.brought
        self.record["opp_lead"] = sp

    def _id(self, s: dict) -> str:
        from pokechamp.data.static.names import to_id
        return to_id(s["species"])

    # ---------- 턴 ----------
    def recommend(self) -> dict | None:
        st = self.sess.state
        me = st.me.mons.get(st.me.active or "")
        if me is None or me.fainted:
            alive = [self.sess.res(x) for x in self.brought
                     if not st.me.mons[self.sess.res(x)].fainted and self.sess.res(x) != st.me.active]
            if not alive:
                return None
            r = self.srch.choose_switch(alive)
            vals = " / ".join(f"{self.ko('species', k)} {v:.0%}" for k, v in sorted(r["values"].items(), key=lambda kv: -kv[1]))
            self.say(f"교체 추천: {self.ko('species', r['choice'])}  (후보별 승률 {vals})")
            return {"switch": r}
        t0 = time.time()
        r = self.srch.choose()
        names = {**self.bt.ko["moves"], **self.bt.ko["species"]}
        self.say(f"추천: {describe(r, names)}   [{time.time() - t0:.1f}초, 시뮬레이션 {r['simulations']}판]")
        return {"move": r}

    def explain(self, rec: dict | None) -> None:
        if not rec or "move" not in rec:
            self.say("교체 단계라 상세 이유가 없다.")
            return
        r = rec["move"]
        names = {**self.bt.ko["moves"], **self.bt.ko["species"]}
        for i in np.argsort(-r["probabilities"]):
            a = r["actions"][i]
            label = (names.get(a[1], a[1]) + (" + 메가" if len(a) > 2 and a[2] else "")) if a[0] == "move" else f"교체 {names.get(a[1], a[1])}"
            self.say(f"  {label}: 확률 {r['probabilities'][i]:.0%} (균형 {r['equilibrium'][i]:.0%}) · "
                     f"상대가 최선으로 받을 때 {r['worst_case'][i]:.0%} · 상대 모델 대비 {r['vs_model'][i]:.0%}")
        opp = self.sess.state.opp.active
        if opp:
            b = self.sess.belief.summary(opp)
            items = ", ".join(f"{self.ko('items', k) or '-'} {v:.0%}" for k, v in b["items"])
            self.say(f"  상대 {self.ko('species', opp)} 도구 추정: {items} · 메가스톤 {b['mega_stone']:.0%}")
        self.say("  (값은 상대 세트 샘플 × 롤아웃(최대 데미지 봇)으로 구한 승률이다. 첫 수 뒤의 운영은 기준선 봇 수준으로 가정한다)")

    def read_turn(self, rec: dict | None) -> list[Event] | None:
        while True:
            raw = self.ask("이번 턴에 본 것: ").strip()
            if not raw:
                continue
            if raw in ("?", "도움말"):
                self.say(HELP)
                continue
            if raw in ("왜", "왜?"):
                self.explain(rec)
                continue
            if raw == "상태":
                self.say(json.dumps(self.sess.summary()["state"], ensure_ascii=False)[:2000])
                continue
            if raw in ("끝", "종료"):
                return None
            res = self.bt.parse(raw)
            if res.errors:
                for e in res.errors:
                    self.say(f"  해석 실패: {e}")
                self.say("  다시 입력하세요 ('?'로 형식 보기).")
                continue
            self.say(f"  해석: {self.bt.render(res.events)}")
            ok = self.ask("  맞으면 엔터, 고치려면 다시 입력: ").strip()
            if ok:
                raw2 = self.bt.parse(ok)
                if raw2.errors:
                    for e in raw2.errors:
                        self.say(f"  해석 실패: {e}")
                    continue
                res = raw2
                self.say(f"  해석: {self.bt.render(res.events)}")
            return res.events

    def run(self, recommend_selection: bool = True, max_turns: int = 200) -> dict[str, Any]:
        self.start(recommend_selection)
        self.say(HELP.split("\n")[0])
        turn = 0
        while turn < max_turns:
            st = self.sess.state
            me = st.me.mons.get(st.me.active or "")
            if me is not None and not me.fainted:          # 기절 교체만 하는 입력은 턴을 올리지 않는다
                turn += 1
                self.sess.feed([Event("turn", amount=turn)])
            rec = self.recommend()
            if rec is None:
                self.say("남은 포켓몬이 없다.")
                break
            events = self.read_turn(rec)
            if events is None:
                break
            warns = self.sess.feed(events)
            for w in warns:
                self.say(f"⚠️ {w}")
            self.record["turns"].append({"turn": turn, "input": self.bt.render(events),
                                         "recommendation": self._rec_json(rec), "warnings": warns})
            st = self.sess.state
            if all(st.opp.mons[x].fainted for x in st.opp.order if st.opp.mons[x].seen) and sum(
                    1 for x in st.opp.order if st.opp.mons[x].seen) >= 3:
                self.say("상대 3마리가 모두 쓰러졌다. 승리!")
                self.record["result"] = "win"
                break
            if all(st.me.mons[self.sess.res(x)].fainted for x in self.brought):
                self.say("내 3마리가 모두 쓰러졌다. 패배…")
                self.record["result"] = "loss"
                break
        if "result" not in self.record:
            r = self.ask("결과 (승/패/무, 엔터=기록 안 함): ").strip()
            self.record["result"] = {"승": "win", "패": "loss", "무": "tie"}.get(r, "unknown")
        return self.save()

    def _rec_json(self, rec: dict | None) -> Any:
        if not rec:
            return None
        if "switch" in rec:
            return rec["switch"]
        r = rec["move"]
        return {"actions": [list(a) for a in r["actions"]], "probabilities": [round(float(x), 4) for x in r["probabilities"]],
                "value": round(float(r["value"]), 4)}

    def save(self) -> dict[str, Any]:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"battle-{self.seed}.json"
        self.record["ended_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        path.write_text(json.dumps(self.record, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        self.say(f"기록을 저장했다: {path}")
        self.record["path"] = str(path)
        return self.record
