"""웹 실전 세션 (단계 5 후속): 상대 6마리 → 선출 추천 → 실제 선출·상대 선봉 → 매 턴 추천과 관찰 입력.

CLI의 TextBattle과 같은 흐름을 단계별 함수로 나눈 것이다(웹 요청 하나가 단계 하나).
- 선출 추천: select.recommend.Selector (20초 예산)
- 매 턴: 추천 계산 → 사용자가 본 것을 입력 → 해석 미리보기 → 적용
- 끝: 기록을 my_battles(source='manual')에 저장
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np

from pokechamp.battle.events import Event
from pokechamp.battle.search import Searcher, SearchConfig
from pokechamp.battle.session import BattleSession
from pokechamp.data.db import active_version
from pokechamp.data.static.names import to_id
from pokechamp.input.battle_text import STAT_KO, STATUS_KO, TERRAIN_KO, WEATHER_KO, BattleText
from pokechamp.teambuild.search import Names
from pokechamp.web.battle import sprite_id


# 등장할 때 발동하는 특성 (자동 처리). 위협은 상대 공격 −1, 날씨·필드 특성은 날씨·필드를 깐다.
WEATHER_SETTERS = {"drought": "sun", "drizzle": "rain", "sandstream": "sand", "snowwarning": "snow", "orichalcumpulse": "sun"}
TERRAIN_SETTERS = {"electricsurge": "electric", "psychicsurge": "psychic", "grassysurge": "grassy", "mistysurge": "misty",
                   "hadronengine": "electric", "seedsower": "grassy"}
ENTRY_ABILITIES = {"intimidate"} | set(WEATHER_SETTERS) | set(TERRAIN_SETTERS)
# 효과는 없지만 등장할 때 알림이 떠서 특성이 드러나는 것 (믿음 상태 증거로 쓴다)
ANNOUNCE_ABILITIES = ENTRY_ABILITIES | {"pressure", "unnerve", "moldbreaker", "teravolt", "turboblaze", "frisk", "trace",
                                        "download", "anticipation", "forewarn", "airlock", "cloudnine", "comatose",
                                        "neutralizinggas", "screencleaner", "curiousmedicine", "asonespectrier", "asoneglastrier"}
ENTRY_ITEMS = {"airballoon"}
# 한 번 쓰고 사라지는 도구 (열매는 DB의 is_berry로 판단하고, 그 밖의 것은 여기 둔다)
CONSUMABLE_EXTRA = {"focussash", "whiteherb", "mentalherb", "powerherb", "weaknesspolicy", "redcard", "ejectbutton", "ejectpack",
                    "throatspray", "roomservice", "blunderpolicy", "airballoon", "adrenalineorb", "absorbbulb", "cellbattery",
                    "luminousmoss", "snowball", "electricseed", "grassyseed", "psychicseed", "mistyseed", "boosterenergy",
                    "mirrorherb", "normalgem"}
SAND_IMMUNE_TYPES = {"Rock", "Ground", "Steel"}
SAND_IMMUNE_ABILITIES = {"sandveil", "sandrush", "sandforce", "overcoat", "magicguard"}   # 등장할 때 알림이 뜨는 도구 (풍선: 맞으면 터진다)
INTIMIDATE_IMMUNE = {"clearbody", "hypercutter", "innerfocus", "oblivious", "owntempo", "scrappy", "whitesmoke", "fullmetalbody"}


class LiveBattle:
    def __init__(self, conn, client, my_team: list[dict], opp_species: list[str], team_name: str = "",
                 cfg: SearchConfig | None = None, seed: str | None = None):
        self.conn, self.client = conn, client
        self.my_team, self.opp_species = my_team, opp_species
        self.team_name = team_name
        self.seed = seed or datetime.now().strftime("%Y%m%d-%H%M%S")
        self.cfg = cfg or SearchConfig(seed=self.seed)
        self.bt = BattleText(conn)
        v = active_version(conn)
        self.names = Names(conn, v["id"])
        self.sess: BattleSession | None = None
        self.srch: Searcher | None = None
        self.brought: list[str] = []
        self.turn = 0
        self.rec: dict[str, Any] | None = None
        self.warnings: list[str] = []
        self.ended = False
        self.phase = "move"                  # move: 이번 턴 입력 / replace: 기절 교체 입력 대기
        self.need_replace = {"me": False, "opp": False}
        self._moves_info: dict[str, dict] = {}
        self.record: dict[str, Any] = {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                       "seed": self.seed, "my_team": my_team, "opp_species": opp_species, "turns": [],
                                       "via": "web"}

    # ---------- 이름 ----------
    def ko(self, table: str, i: str) -> str:
        return self.bt.name(table, i) if i else ""

    def en(self, sp: str) -> str:
        return self.names.en("species", sp) or sp

    # ---------- 선출 ----------
    def selection(self, budget_s: float | None = None) -> dict[str, Any]:
        from pokechamp.select.recommend import Selector
        r = Selector(self.conn, self.client).run(self.my_team, self.opp_species, seed=self.seed, budget_s=budget_s)
        self.record["selection"] = {"picks": r["picks"], "expected": r["expected_win_vs_equilibrium"]}
        for p in r["picks"]:
            p["sprites"] = [sprite_id(x) for x in p["member_species"]]
        for o in r["opponent"]:
            o["sprite"] = sprite_id(o["name_en"])
        return r

    # ---------- 등장 알림 (특성·도구) ----------
    def species_abilities(self, species: str) -> list[str]:
        cache = getattr(self, "_abil_cache", None)
        if cache is None:
            v = active_version(self.conn)
            cache = self._abil_cache = {r[0]: [to_id(x) for x in json.loads(r[1] or "{}").values()] for r in self.conn.execute(
                "SELECT id, abilities_json FROM species WHERE version_id=?", (v["id"],))}
        return cache.get(species, [])

    def entry_options(self, species: str) -> list[dict[str, Any]]:
        """상대 포켓몬이 나올 때 뜰 수 있는 알림 (체크박스).
        - 특성: 그 종이 가질 수 있는 특성 중 등장 알림이 뜨는 것만 (통계에 거의 없는 숨겨진 특성도 포함)
        - 풍선: 누구나 들 수 있어 항상 보인다. 단 그 포켓몬의 도구가 이미 다른 것으로 드러났거나,
          상대의 다른 포켓몬이 이미 풍선을 들었다고 드러났으면(도구 중복 금지) 숨긴다.
        확률(믿음 상태, 배틀 전에는 통계)이 50% 이상이면 미리 체크한다."""
        if self.sess:
            mb = self.sess.belief.mon(species)
            abilities, items = mb.marginal("ability"), mb.marginal("item")
        else:                                 # 배틀 시작 전(선봉 고를 때): 통계의 주변 분포로 대신한다
            from pokechamp.data.priors import Priors
            pri = getattr(self, "_priors", None) or Priors(self.conn)
            self._priors = pri
            abilities, items = pri.marginal(species, "abilities").values, pri.marginal(species, "items").values
        ap = {to_id(a or ""): p for a, p in abilities}
        ip = {to_id(i or ""): p for i, p in items}
        out = []
        for a in self.species_abilities(species):
            if a in ANNOUNCE_ABILITIES:
                pr = ap.get(a, 0.0)
                out.append({"key": f"ability:{a}", "name": self.ko("abilities", a), "p": round(pr, 2), "default": pr >= 0.5,
                            "effect": a in ENTRY_ABILITIES})
        show_balloon = True
        if self.sess:
            st = self.sess.state
            m = st.opp.mons.get(species)
            if m and m.item and m.item != "airballoon":
                show_balloon = False           # 이미 다른 도구가 드러났다
            if any(x != species and st.opp.mons[x].item == "airballoon" for x in st.opp.order):
                show_balloon = False           # 다른 포켓몬이 풍선을 들었다 (도구 중복 금지)
            if m and m.item == "airballoon" and m.item_consumed:
                show_balloon = False           # 이미 터졌다
        if show_balloon:
            pr = ip.get("airballoon", 0.0)
            out.append({"key": "item:airballoon", "name": self.ko("items", "airballoon"), "p": round(pr, 2), "default": pr >= 0.5,
                        "effect": True})
        return out

    def _immune_prob(self, side: str) -> float:
        """위협을 받는 쪽 출전 포켓몬이 면역 특성일 확률 (내 쪽은 0/1, 상대는 믿음 상태)."""
        st = self.sess.state
        sd = st.me if side == "me" else st.opp
        m = sd.mons.get(sd.active or "")
        if m is None:
            return 0.0
        if side == "me":
            return 1.0 if to_id((m.set or {}).get("ability") or "") in INTIMIDATE_IMMUNE else 0.0
        if m.ability:
            return 1.0 if m.ability in INTIMIDATE_IMMUNE else 0.0
        return sum(p for a, p in self.sess.belief.mon(m.species).marginal("ability") if to_id(a or "") in INTIMIDATE_IMMUNE)

    def _entry_events(self, side: str, species: str, keys: list[str]) -> list[Event]:
        """등장 알림의 사건: 특성·도구 공개 + 효과. 위협은 받는 쪽의 반응(오기·승기·미러아머·가드독·면역)까지."""
        ev: list[Event] = []
        for key in keys:
            kind, _, val = key.partition(":")
            if kind == "item" and val in ENTRY_ITEMS:
                ev.append(Event("item", side, species=species, item=val))
                continue
            if kind != "ability" or val not in ANNOUNCE_ABILITIES:
                continue
            ev.append(Event("ability", side, species=species, ability=val))
            if val in WEATHER_SETTERS:
                ev.append(Event("weather", value=WEATHER_SETTERS[val]))
            elif val in TERRAIN_SETTERS:
                ev.append(Event("terrain", value=TERRAIN_SETTERS[val]))
            elif val == "intimidate":
                other = "opp" if side == "me" else "me"
                if self._immune_prob(other) >= 0.5:
                    self.warnings.append(f"{'상대' if other == 'opp' else '내'} 포켓몬은 위협이 통하지 않는 특성일 가능성이 높아 공격 하락을 넣지 않았다")
                    continue
                st = self.sess.state
                sd = st.me if other == "me" else st.opp
                tgt = sd.mons.get(sd.active or "")
                tab = to_id(((tgt.set or {}).get("ability") if other == "me" else tgt.ability) or "") if tgt else ""
                if tab == "mirrorarmor":
                    ev.append(Event("boost", side, stat="atk", amount=-1))
                elif tab == "guarddog":
                    ev.append(Event("boost", other, stat="atk", amount=1))
                else:
                    ev.append(Event("boost", other, stat="atk", amount=-1))
                    if tab == "defiant":
                        ev.append(Event("boost", other, stat="atk", amount=2))
                    if tab == "competitive":
                        ev.append(Event("boost", other, stat="spa", amount=2))
        return ev

    def _my_entry_keys(self, species: str) -> list[str]:
        """내 포켓몬의 등장 알림은 세트로 안다."""
        m = self.sess.state.me.mons.get(species)
        st = (m.set or {}) if m else {}
        keys = []
        ab = to_id(st.get("ability") or "")
        if ab in ANNOUNCE_ABILITIES:
            keys.append(f"ability:{ab}")
        if to_id(st.get("item") or "") in ENTRY_ITEMS and not (m and m.item_consumed):
            keys.append(f"item:{to_id(st.get('item'))}")
        return keys

    def _switch_in(self, entries: list[tuple[str, str, list[str]]]) -> list[Event]:
        """교체 사건을 먼저 적용한 뒤 등장 알림을 적용한다 (위협을 받는 쪽을 정확히 알려면 교체가 먼저다). 적용한 사건을 돌려준다."""
        sw = [Event("switch", side, species=sp) for side, sp, _ in entries]
        self.warnings += self.sess.feed(sw)
        ent: list[Event] = []
        for side, sp, keys in entries:
            ent += self._entry_events(side, sp, keys)
        if ent:
            self.warnings += self.sess.feed(ent)
        return sw + ent

    def start(self, slots: list[int], opp_lead: str, opp_lead_entry: list[str] | None = None) -> dict[str, Any]:
        if len(slots) != 3 or len(set(slots)) != 3 or not all(1 <= s <= len(self.my_team) for s in slots):
            raise ValueError("선출은 서로 다른 슬롯 번호 3개다 (선봉 먼저)")
        lead = self.bt.lookup("species", opp_lead) or (to_id(opp_lead) if to_id(opp_lead) in self.opp_species else None)
        if not lead:
            raise ValueError(f"상대 선봉 '{opp_lead}'을(를) 모른다")
        self.brought = [self.my_team[s - 1]["species"] for s in slots]
        self.sess = BattleSession(self.conn, self.my_team, self.opp_species, self.client, seed=self.seed)
        self.srch = Searcher(self.sess, self.brought, self.cfg)
        me_lead, op_lead = self.sess.res(self.brought[0]), self.sess.res(lead)
        self.warnings = []
        # 선봉 등장 알림: 내 쪽은 세트로, 상대 쪽은 사용자가 체크한 것만
        self._switch_in([("me", me_lead, self._my_entry_keys(me_lead)), ("opp", op_lead, list(opp_lead_entry or []))])
        self.record.update(brought=self.brought, opp_lead=self.ko("species", self.sess.res(lead)))
        self._next_turn()
        return self.view()

    # ---------- 턴 ----------
    def _switch_phase(self) -> bool:
        st = self.sess.state
        me = st.me.mons.get(st.me.active or "")
        return me is None or me.fainted

    def _next_turn(self) -> None:
        if not self._switch_phase():
            self.turn += 1
            self.sess.feed([Event("turn", amount=self.turn)])
        self.rec = self._recommend()

    def _recommend(self) -> dict[str, Any] | None:
        st = self.sess.state
        t0 = time.time()
        if self._switch_phase():
            alive = [self.sess.res(x) for x in self.brought
                     if not st.me.mons[self.sess.res(x)].fainted and self.sess.res(x) != st.me.active]
            if not alive:
                return None
            r = self.srch.choose_switch(alive)
            opts = sorted(r["values"].items(), key=lambda kv: -kv[1])
            return {"phase": "switch", "best": f"교체 {self.ko('species', r['choice'])}", "win": opts[0][1],
                    "options": [{"label": f"교체 {self.ko('species', k)}", "value": v, "probability": 1.0 if k == r["choice"] else 0.0,
                                 "insert": f"내 교체 {self.ko('species', k)}", "action": {"kind": "switch", "species": k}}
                                for k, v in opts],
                    "seconds": round(time.time() - t0, 1)}
        r = self.srch.choose()
        opts = []
        for i in np.argsort(-r["vs_model"]):
            a = r["actions"][i]
            if a[0] == "move":
                label = self.ko("moves", a[1]) + (" + 메가" if a[2] else "")
                insert = ("내 메가진화 / " if a[2] else "") + f"내 {self.ko('moves', a[1])}"
                action = {"kind": "move", "move": a[1], "mega": bool(a[2])}
            else:
                label = f"교체 {self.ko('species', a[1])}"
                insert = f"내 교체 {self.ko('species', a[1])}"
                action = {"kind": "switch", "species": a[1]}
            opts.append({"label": label, "value": float(r["vs_model"][i]), "probability": float(r["probabilities"][i]),
                         "insert": insert, "action": action})
        best = max(opts, key=lambda o: o["probability"])
        return {"phase": "move", "best": best["label"], "win": float(r["value"]), "options": opts,
                "simulations": r["simulations"], "seconds": round(time.time() - t0, 1)}

    def preview(self, text: str) -> dict[str, Any]:
        res = self.bt.parse(text)
        return {"ok": res.ok, "errors": res.errors, "echo": self.bt.render(res.events) if res.events else ""}

    def apply(self, text: str) -> dict[str, Any]:
        """고급 입력: 문장으로 한 턴(또는 교체)을 넣는다."""
        if self.ended:
            raise ValueError("이미 끝난 배틀이다")
        res = self.bt.parse(text)
        if res.errors:
            raise ValueError("해석하지 못한 문장이 있다: " + " / ".join(res.errors))
        if not res.events:
            raise ValueError("입력이 비어 있다")
        self.warnings = []
        return self._apply_events(res.events, allow_replace_phase=False)

    def _apply_events(self, events: list[Event], allow_replace_phase: bool = True,
                      already: list[Event] | None = None) -> dict[str, Any]:
        """사건을 적용하고 기록한다. already: 이번 입력에서 먼저 적용한 사건(교체·등장 알림), 기록에만 합친다."""
        warns = self.sess.feed(events) if events else []
        shown = (already or []) + events
        self.record["turns"].append({"turn": self.turn, "input": self.bt.render(shown), "recommendation": self.rec,
                                     "warnings": self.warnings + warns})
        self.warnings = self.warnings + warns
        st = self.sess.state
        opp_seen = [x for x in st.opp.order if st.opp.mons[x].seen]
        opp_done = len(opp_seen) >= 3 and all(st.opp.mons[x].fainted for x in opp_seen)
        my_done = all(st.me.mons[self.sess.res(x)].fainted for x in self.brought)
        if opp_done or my_done:
            self.rec, self.phase = None, "over"
            self.warnings.append("상대 3마리가 모두 쓰러졌다. 승리로 끝내세요." if opp_done else "내 3마리가 모두 쓰러졌다. 패배로 끝내세요.")
            return self.view()
        me = st.me.mons.get(st.me.active or "")
        op = st.opp.mons.get(st.opp.active or "")
        self.need_replace = {"me": bool(me is None or me.fainted), "opp": bool(op is None or op.fainted)}
        if allow_replace_phase and (self.need_replace["me"] or self.need_replace["opp"]):
            # 기절 교체를 먼저 받는다 (상대가 누구를 냈는지 알아야 다음 추천이 맞다)
            self.phase = "replace"
            self.rec = self._recommend() if self.need_replace["me"] else None
            return self.view()
        self.phase = "move"
        self._next_turn()
        return self.view()

    # ---------- 단계별 폼 입력 ----------
    def moves_info(self, ids: list[str]) -> dict[str, dict]:
        want = [i for i in ids if i and i not in self._moves_info]
        if want:
            r = self.client.batch("/moves_info", [{"moves": want}])[0]
            if r.get("ok"):
                self._moves_info.update(r["result"])
        return {i: self._moves_info.get(i, {}) for i in ids}

    def form(self) -> dict[str, Any]:
        """이번 턴 입력 폼에 필요한 선택지."""
        st = self.sess.state
        me = st.me.mons.get(st.me.active or "")
        op = st.opp.mons.get(st.opp.active or "")
        out: dict[str, Any] = {"phase": self.phase, "need_replace": self.need_replace}
        if me and not me.fainted:
            out["my_moves"] = [{"id": to_id(m), "name": self.ko("moves", to_id(m))} for m in (me.set or {}).get("moves", [])]
            out["my_can_mega"] = bool(self.sess.mega_of(me.set or {})) and not st.me.mega_used
            out["my_hp_max"] = me.hp_max or (self.sess.my_stats.get((me.species, me.mega)) or {}).get("hp")
            out["my_hp"] = me.hp_abs if me.hp_abs is not None else out["my_hp_max"]
            cur = me.held if me.held is not None else ("" if me.item_consumed else to_id((me.set or {}).get("item") or ""))
            out["my_item"] = self.ko("items", cur) if cur else ""
            out["my_item_consumable"] = bool(cur) and self.is_consumable(cur)
        out["my_bench"] = [{"id": self.sess.res(x), "name": self.ko("species", self.sess.res(x)), "sprite": sprite_id(self.en(self.sess.res(x)))}
                           for x in self.brought if self.sess.res(x) != st.me.active and not st.me.mons[self.sess.res(x)].fainted]
        if op:
            mb = self.sess.belief.mon(op.species)
            weight: dict[str, float] = {}
            for p in mb.particles:
                for m in p.set.get("moves", []):
                    weight[to_id(m)] = weight.get(to_id(m), 0.0) + p.w
            ranked = [m for m in op.moves] + [m for m, _ in sorted(weight.items(), key=lambda kv: -kv[1]) if m not in op.moves]
            out["opp_moves"] = [{"id": m, "name": self.ko("moves", m), "seen": m in op.moves,
                                 "p": round(1.0 if m in op.moves else weight.get(m, 0.0), 2)} for m in ranked[:8]]
            out["opp_all_moves"] = sorted({self.ko("moves", m) for m in weight} | {self.ko("moves", m) for m in op.moves})
            out["opp_can_mega"] = not st.opp.mega_used and self.sess.belief.summary(op.species)["mega_stone"] > 0.01 and not op.mega
            out["opp_hp"] = op.hp
            if op.held is not None:
                out["opp_item_guess"] = self.ko("items", op.held) if op.held else ""
            elif op.item:
                out["opp_item_guess"] = self.ko("items", op.item)
            else:
                top = mb.marginal("item")
                out["opp_item_guess"] = self.ko("items", to_id(top[0][0] or "")) if top and top[0][0] else ""
            out["items_all"] = sorted(set(self.bt.ko["items"].values()))
        seen = [x for x in st.opp.order if st.opp.mons[x].seen]
        cands = [x for x in seen if x != st.opp.active and not st.opp.mons[x].fainted]
        if len(seen) < 3:
            cands += [x for x in st.opp.order if not st.opp.mons[x].seen]
        out["opp_bench"] = [{"id": x, "name": self.ko("species", x), "sprite": sprite_id(self.en(x)), "seen": st.opp.mons[x].seen,
                             "entry": self.entry_options(x)} for x in cands]
        return out

    def estimate(self, my: dict, opp: dict) -> dict[str, Any]:
        """고른 행동으로 예상 HP를 낸다 (상대 세트는 믿음에서 가중치 상위 파티클 몇 개의 평균)."""
        from pokechamp.battle.session import engine_set
        st = self.sess.state
        me, op = st.me.mons.get(st.me.active or ""), st.opp.mons.get(st.opp.active or "")
        out: dict[str, Any] = {"my_hp": me.hp_abs if me else None, "opp_hp": op.hp if op else None}
        if not me or not op or me.fainted or op.fainted:
            return out
        for a in (my, opp):
            if a.get("kind") == "move" and a.get("move"):
                try:
                    a["move"] = self._move_id(a["move"])
                except ValueError:
                    a["kind"] = "none"
        parts = sorted(self.sess.belief.mon(op.species).particles, key=lambda p: -p.w)[:6]
        field = self.sess._field()
        reqs, kinds = [], []
        mine_side = {"set": engine_set(me.set), "mega": me.mega or bool(my.get("mega")), "boosts": {k: v for k, v in me.boosts.items() if v}}
        for p in parts:
            theirs = {"set": engine_set(p.set), "mega": op.mega or bool(opp.get("mega") and p.set.get("mega_species")),
                      "boosts": {k: v for k, v in op.boosts.items() if v}, "hpPercent": op.hp}
            if my.get("kind") == "move" and my.get("move"):
                reqs.append({"attacker": mine_side, "defender": theirs, "move": my["move"], "field": field, "crossCheck": False})
                kinds.append(("opp", p.w))
            if opp.get("kind") == "move" and opp.get("move"):
                reqs.append({"attacker": {k: v for k, v in theirs.items() if k != "hpPercent"}, "defender": mine_side,
                             "move": opp["move"], "field": field, "crossCheck": False})
                kinds.append(("me", p.w))
        res = self.client.batch("/damage", reqs) if reqs else []
        acc = {"opp": [0.0, 0.0], "me": [0.0, 0.0]}
        for (who, w), r in zip(kinds, res):
            if not r.get("ok") or r["result"]["status"]:
                continue
            rolls = r["result"]["rolls"]
            mean = sum(rolls) / len(rolls)
            val = 100 * mean / r["result"]["defender"]["maxhp"] if who == "opp" else mean
            acc[who][0] += w * val
            acc[who][1] += w
        notes: list[str] = []
        cur = me.hp_abs if me.hp_abs is not None else (me.hp_max or (self.sess.my_stats.get((me.species, me.mega)) or {}).get("hp"))
        my_attacked = my.get("kind") == "move" and acc["opp"][1] > 0
        opp_attacked = opp.get("kind") == "move" and acc["me"][1] > 0
        if op.hp is not None:
            hp = op.hp - (acc["opp"][0] / acc["opp"][1] if acc["opp"][1] > 0 else 0)
            # 기합의띠: 가득 찬 HP에서 쓰러질 데미지면 1로 버틴다 (상대 도구가 띠로 드러났을 때만)
            if hp <= 0 and op.hp >= 100 and self.current_item("opp") == "focussash":
                hp, out["opp_item_used"] = 1, "기합의띠"
                notes.append("상대 기합의띠로 버팀")
            hp, used = self._end_of_turn("opp", max(0.0, hp), 100.0, opp_attacked, notes)
            if used:
                out["opp_item_used"] = self.ko("items", self.current_item("opp"))
            out["opp_hp"] = max(0, round(hp))
        mx = me.hp_max or (self.sess.my_stats.get((me.species, me.mega)) or {}).get("hp")
        if cur is not None and mx:
            hp = cur - (acc["me"][0] / acc["me"][1] if acc["me"][1] > 0 else 0)
            if hp <= 0 and cur >= mx and self.current_item("me") == "focussash":
                hp, out["my_item_used"] = 1, True
                notes.append("내 기합의띠로 버팀")
            hp, used = self._end_of_turn("me", max(0.0, hp), float(mx), my_attacked, notes)
            out["my_item_used"] = bool(out.get("my_item_used") or used)
            out["my_hp"] = max(0, round(hp))
        out["notes"] = notes
        return out

    # ---------- 도구 ----------
    def is_consumable(self, item: str) -> bool:
        berries = getattr(self, "_berries", None)
        if berries is None:
            v = active_version(self.conn)
            berries = self._berries = {r[0] for r in self.conn.execute(
                "SELECT id FROM items WHERE version_id=? AND is_berry=1", (v["id"],))}
        return item in berries or item in CONSUMABLE_EXTRA

    def current_item(self, side: str) -> str:
        """지금 드는 도구 (내 쪽은 세트로, 상대 쪽은 드러난 것만). 썼거나 잃었으면 ""."""
        st = self.sess.state
        sd = st.me if side == "me" else st.opp
        m = sd.mons.get(sd.active or "")
        if not m:
            return ""
        if m.held is not None:
            return m.held
        if m.item_consumed:
            return ""
        return to_id((m.set or {}).get("item") or "") if side == "me" else (m.item or "")

    def _types(self, species: str) -> list[str]:
        cache = getattr(self, "_type_cache", None)
        if cache is None:
            v = active_version(self.conn)
            cache = self._type_cache = {r[0]: json.loads(r[1] or "[]") for r in self.conn.execute(
                "SELECT id, types_json FROM species WHERE version_id=?", (v["id"],))}
        return cache.get(species, [])

    def _end_of_turn(self, side: str, hp: float, mx: float, attacked: bool, notes: list[str]) -> tuple[float, bool]:
        """턴 끝 효과를 예상 HP에 넣는다 (hp·mx는 같은 단위: 내 쪽은 실수치, 상대는 %·100).
        → (HP, 일회용 도구를 썼는지). 생명의구슬은 공격했을 때만."""
        st = self.sess.state
        sd = st.me if side == "me" else st.opp
        m = sd.mons.get(sd.active or "")
        if not m or hp <= 0:
            return hp, False
        item = self.current_item(side)
        ab = to_id(((m.set or {}).get("ability") if side == "me" else m.ability) or "")
        frac = lambda f: mx * f   # noqa: E731
        label = "내" if side == "me" else "상대"
        used = False
        if item == "lifeorb" and attacked and ab != "magicguard":
            hp -= frac(0.1)
            notes.append(f"{label} 생명의구슬 반동")
        if hp > 0 and st.weather.kind == "sand" and ab not in SAND_IMMUNE_ABILITIES and not (set(self._types(m.species)) & SAND_IMMUNE_TYPES):
            hp -= frac(1 / 16)
            notes.append(f"{label} 모래바람")
        if hp > 0 and st.terrain.kind == "grassy" and "Flying" not in self._types(m.species) and ab != "levitate" and item != "airballoon":
            hp = min(mx, hp + frac(1 / 16))
            notes.append(f"{label} 그래스필드 회복")
        if hp > 0 and (item == "leftovers" or (item == "blacksludge" and "Poison" in self._types(m.species))):
            hp = min(mx, hp + frac(1 / 16))
            notes.append(f"{label} {self.ko('items', item)} 회복")
        if hp > 0 and m.status == "brn" and ab != "magicguard":
            hp -= frac(1 / 16)
            notes.append(f"{label} 화상")
        if hp > 0 and m.status in ("psn", "tox") and ab not in ("magicguard", "poisonheal"):
            hp -= frac(1 / 8 if m.status == "psn" else 1 / 16)
            notes.append(f"{label} 독")
        if hp > 0 and item == "sitrusberry" and hp <= mx / 2:
            hp = min(mx, hp + frac(0.25))
            used = True
            notes.append(f"{label} 자뭉열매 사용")
        return hp, used

    def _move_id(self, x: str) -> str:
        """폼이 보낸 기술: id면 그대로, 한국어·영어 이름이면 찾아 바꾼다."""
        if x in self.sess.moves:
            return x
        mv = self.bt.lookup("moves", x)
        if not mv:
            raise ValueError(f"기술 '{x}'을(를) 모른다{self.bt.suggest('moves', x)}")
        return mv

    def _auto_effects(self, side: str, move: str, defender_hit: bool) -> list[Event]:
        info = self.moves_info([move]).get(move, {})
        ev = []
        for st_, n in (info.get("selfBoosts") or {}).items():
            ev.append(Event("boost", side, stat=st_, amount=int(n)))
        if defender_hit or info.get("category") == "Status":
            other = "opp" if side == "me" else "me"
            for st_, n in (info.get("targetBoosts") or {}).items():
                ev.append(Event("boost", other, stat=st_, amount=int(n)))
        return ev

    def submit_turn(self, f: dict) -> dict[str, Any]:
        """단계별 폼 한 턴: first(me|opp), me/opp: {kind: move|switch|none, move, mega, species}, my_hp, opp_hp, extras."""
        if self.ended:
            raise ValueError("이미 끝난 배틀이다")
        if self.phase != "move":
            raise ValueError("기절 교체를 먼저 입력하세요")
        st = self.sess.state
        me_act, op_act = f.get("me") or {}, f.get("opp") or {}
        first = f.get("first") or "me"
        ev: list[Event] = []
        # 1) 교체는 기술보다 먼저 일어난다 (등장 알림 포함)
        self.warnings = []
        entries = []
        for side, a in (("me", me_act), ("opp", op_act)):
            if a.get("kind") == "switch" and a.get("species"):
                sp = self.sess.res(a["species"])
                entries.append((side, sp, self._my_entry_keys(sp) if side == "me" else list(a.get("entry") or [])))
        already = self._switch_in(entries) if entries else []
        st = self.sess.state
        # 2) 메가진화 (기술 전에)
        for side, a in (("me", me_act), ("opp", op_act)):
            if a.get("kind") == "move" and a.get("mega"):
                ev.append(Event("mega", side))
        # 트릭·바꿔치기: 상대가 준 도구는 상대의 원래 도구라는 증거 (기술보다 먼저 둔다)
        swap_moves = {"trick", "switcheroo"}
        for a in (me_act, op_act):
            if a.get("kind") == "move" and a.get("move") and a["move"] not in self.sess.moves:
                a["move"] = self._move_id(a["move"])
        swap = None
        if any(a.get("kind") == "move" and a.get("move") in swap_moves for a in (me_act, op_act)):
            given = (f.get("swap") or {}).get("given") or ""
            given_id = self.bt.lookup("items", given) if given else None
            if given and not given_id:
                raise ValueError(f"도구 '{given}'을(를) 모른다{self.bt.suggest('items', given)}")
            mm = st.me.mons.get(st.me.active or "")
            om = st.opp.mons.get(st.opp.active or "")
            mine_now = (mm.held if mm.held is not None else ("" if mm.item_consumed else to_id((mm.set or {}).get("item") or ""))) if mm else ""
            if given_id and om and om.held is None and not om.item:
                ev.append(Event("item", "opp", item=given_id))
            swap = (given_id or "", mine_now)
        # 3) 기술: 먼저 움직인 쪽부터, 맞은 쪽 HP를 바로 뒤에 둔다 (데미지 증거)
        my_hp = f.get("my_hp")
        opp_hp = f.get("opp_hp")
        order = [("me", me_act), ("opp", op_act)] if first == "me" else [("opp", op_act), ("me", me_act)]
        hp_done = {"me": False, "opp": False}
        for side, a in order:
            if a.get("kind") != "move" or not a.get("move"):
                continue
            a["move"] = self._move_id(a["move"])
            ev.append(Event("move", side, move=a["move"]))
            other = "opp" if side == "me" else "me"
            val = opp_hp if other == "opp" else my_hp
            hit = False
            if val is not None and val != "":
                before = st.opp.mons[st.opp.active].hp if other == "opp" and st.opp.active else (
                    st.me.mons[st.me.active].hp_abs if st.me.active else None)
                hit = before is None or float(val) < float(before)
                ev.append(self._hp_event(other, val))
                hp_done[other] = True
                info = self.moves_info([a["move"]]).get(a["move"], {})
                if hit and info.get("category") in ("Physical", "Special") and self._has_balloon(other):
                    ev.append(Event("item", other, item="airballoon", value="consumed"))
            ev += self._auto_effects(side, a["move"], hit)
            if a["move"] in swap_moves and swap is not None:
                given_id, mine_now = swap
                ev.append(Event("item", "me", item=given_id, value="received", note="[from] move: Trick"))
                ev.append(Event("item", "opp", item=mine_now, value="received", note="[from] move: Trick"))
                swap = None
            if a["move"] == "knockoff" and hit:
                if side == "me":
                    kn = (f.get("knocked") or "").strip()
                    kid = self.bt.lookup("items", kn) if kn else ""
                    if kn and not kid:
                        raise ValueError(f"도구 '{kn}'을(를) 모른다{self.bt.suggest('items', kn)}")
                    ev.append(Event("item", "opp", item=kid or "", value="lost", note="[from] move: Knock Off"))
                else:
                    mm = st.me.mons.get(st.me.active or "")
                    cur = (mm.held if mm.held is not None else ("" if mm.item_consumed else to_id((mm.set or {}).get("item") or ""))) if mm else ""
                    if cur and not (mm.mega or (self.sess.mega_of(mm.set or {}) and cur == to_id((mm.set or {}).get("item") or ""))):
                        ev.append(Event("item", "me", item=cur, value="lost", note="[from] move: Knock Off"))
        # 4) 남은 HP (기술을 안 맞았어도 반동·회복 등으로 바뀐 값)
        for side, val in (("me", my_hp), ("opp", opp_hp)):
            if not hp_done[side] and val is not None and val != "":
                ev.append(self._hp_event(side, val))
        # 5) 기절
        for side, val in (("me", my_hp), ("opp", opp_hp)):
            if val is not None and val != "" and float(val) <= 0:
                ev.append(Event("faint", side))
        # 6) 추가 사항
        x = f.get("extras") or {}
        for side in ("me", "opp"):
            stt = x.get(f"{side}_status")
            if stt:
                ev.append(Event("status", side, status=stt))
        if x.get("opp_item"):
            it = self.bt.lookup("items", x["opp_item"])
            if not it:
                raise ValueError(f"도구 '{x['opp_item']}'을(를) 모른다{self.bt.suggest('items', x['opp_item'])}")
            # 먹다남은음식처럼 계속 드는 도구는 한 번 넣으면 기억하고, 열매·띠처럼 쓰고 사라지는 도구는 '사용'으로 처리한다
            ev.append(Event("item", "opp", item=it, value="consumed" if self.is_consumable(it) else ""))
        if x.get("my_item_used"):
            mine = self.current_item("me")
            if mine:
                ev.append(Event("item", "me", item=mine, value="consumed"))
        if x.get("opp_ability"):
            ab = self.bt.lookup("abilities", x["opp_ability"])
            if not ab:
                raise ValueError(f"특성 '{x['opp_ability']}'을(를) 모른다{self.bt.suggest('abilities', x['opp_ability'])}")
            ev.append(Event("ability", "opp", ability=ab))
        if x.get("weather"):
            ev.append(Event("weather", value=x["weather"]))
        if not ev and not already:
            raise ValueError("이번 턴에 일어난 일이 없다 (행동을 골라 주세요)")
        return self._apply_events(ev, already=already)

    def _has_balloon(self, side: str) -> bool:
        st = self.sess.state
        sd = st.me if side == "me" else st.opp
        m = sd.mons.get(sd.active or "")
        if not m or m.item_consumed:
            return False
        cur = m.held if m.held is not None else (m.item or (to_id((m.set or {}).get("item") or "") if side == "me" else ""))
        return cur == "airballoon"

    def _hp_event(self, side: str, val) -> Event:
        v = float(val)
        if side == "me":
            st = self.sess.state
            me = st.me.mons.get(st.me.active or "")
            mx = (me.hp_max if me else None) or (self.sess.my_stats.get((me.species, me.mega)) or {}).get("hp") if me else None
            if mx:
                return Event("hp", "me", hp=round(100 * v / mx, 1), hp_abs=int(v), hp_max=int(mx))
            return Event("hp", "me", hp=v)
        return Event("hp", "opp", hp=v)

    def submit_replace(self, my_species: str | None, opp_species: str | None, opp_entry: list[str] | None = None) -> dict[str, Any]:
        if self.phase != "replace":
            raise ValueError("지금은 기절 교체 단계가 아니다")
        entries = []
        if self.need_replace["me"]:
            if not my_species:
                raise ValueError("내가 다음에 낸 포켓몬을 골라 주세요")
            sp = self.sess.res(my_species)
            entries.append(("me", sp, self._my_entry_keys(sp)))
        if self.need_replace["opp"]:
            if not opp_species:
                raise ValueError("상대가 다음에 낸 포켓몬을 골라 주세요")
            entries.append(("opp", self.sess.res(opp_species), list(opp_entry or [])))
        self.phase = "move"
        self.warnings = []
        already = self._switch_in(entries)
        return self._apply_events([], already=already)

    def undo_hint(self) -> str:
        return "잘못 적용했으면 다음 입력에서 바로잡으세요 (예: 상대 HP 55%, 상대 교체 X). 되돌리기는 아직 없다."

    def explain(self) -> dict[str, Any]:
        out: dict[str, Any] = {"note": "값은 상대 세트 샘플 × 롤아웃(양쪽 최대 데미지 봇)으로 구한 승률이다. 상대가 가장 센 기술을 쓴다고 가정한 최선의 한 수를 추천한다."}
        opp = self.sess.state.opp.active if self.sess else None
        if opp:
            b = self.sess.belief.summary(opp)
            out["belief"] = {"species": self.ko("species", opp),
                             "items": [(self.ko("items", k) or "없음", round(v, 3)) for k, v in b["items"]],
                             "abilities": [(self.ko("abilities", k) or "-", round(v, 3)) for k, v in b["abilities"]],
                             "mega_stone": b["mega_stone"], "ess": b["ess"]}
        return out

    def end(self, result: str, save: bool = True) -> dict[str, Any]:
        """result: win / loss / tie / aborted(중단, 결과 없음). save=False면 기록을 남기지 않는다."""
        from pokechamp.battle.store import save_battle
        self.record["result"] = {"win": "win", "loss": "loss", "tie": "tie"}.get(result, "aborted" if result == "aborted" else "unknown")
        self.record["ended_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.ended = True
        if not save or self.sess is None:
            return {"battle_id": None}
        return {"battle_id": save_battle(self.conn, self.record, self.sess, "manual")}

    # ---------- 화면 ----------
    def _mon_view(self, m, mine: bool, active: str | None) -> dict[str, Any]:
        out = {"species": m.species, "name": self.ko("species", m.species), "sprite": sprite_id(self.en(m.species)),
               "hp": m.hp, "hp_abs": m.hp_abs, "hp_max": m.hp_max, "status": STATUS_KO.get(m.status, m.status),
               "fainted": m.fainted, "active": m.species == active, "mega": m.mega, "seen": m.seen,
               "boosts": {STAT_KO.get(k, k): v for k, v in m.boosts.items() if v},
               "item": self.ko("items", m.held if m.held is not None else m.item) + (" (교환으로 받음)" if m.held else ""),
               "item_consumed": m.item_consumed or m.held == "", "ability": self.ko("abilities", m.ability)}
        if mine and m.held is not None:
            out["moves_note"] = f"지금 도구: {self.ko('items', m.held) if m.held else '없음'}"
        if mine:
            out["moves"] = [self.ko("moves", to_id(x)) for x in (m.set or {}).get("moves", [])]
        else:
            out["moves"] = [self.ko("moves", x) for x in m.moves]
        return out

    def view(self) -> dict[str, Any]:
        st = self.sess.state
        mine = [self._mon_view(st.me.mons[self.sess.res(x)], True, st.me.active) for x in self.brought]
        opp = [self._mon_view(st.opp.mons[x], False, st.opp.active) for x in st.opp.order]

        def clock(c):
            if c.kind == "none":
                return None
            rem = c.remaining(max(st.turn, 1))
            return {"kind": c.kind, "remaining": list(rem) if rem else None}
        w, t = clock(st.weather), clock(st.terrain)
        return {"turn": self.turn, "phase": self.phase if self.phase != "move" else ("switch" if self._switch_phase() else "move"),
                "ended": self.ended, "form": self.form(),
                "mine": mine, "opp": opp, "mega_used": {"me": st.me.mega_used, "opp": st.opp.mega_used},
                "weather": {"name": WEATHER_KO.get(w["kind"], w["kind"]), "remaining": w["remaining"]} if w else None,
                "terrain": {"name": TERRAIN_KO.get(t["kind"], t["kind"]), "remaining": t["remaining"]} if t else None,
                "recommendation": self.rec, "warnings": self.warnings, "history": [x["input"] for x in self.record["turns"]][-12:],
                "team": self.team_name}


def dumps(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, default=float)
