"""선출 추천 본체 (PROJECT_PLAN 8절): 내 팀 6마리(세트 포함) + 상대 6마리 이름 → 내 선출(3마리 + 선봉)의 혼합 전략.

1. 상대 세트: 포켓몬마다 세트 원형 분포(결합 분포)에서 opponent_samples개 팀을 뽑는다. 메가스톤 소지 여부도 이 샘플에서 나온다.
2. 근사 행렬(60 × 60): 빠른 1:1 상성(캐시)으로 3:3 값을 근사해 샘플 평균을 낸다.
   3:3 근사 = (1−λ) × 선출 게임 값(teambuild.game.payoff_tensor) + λ × 선봉끼리 1:1 값.
3. 정밀 칸: 엔진 3:3 시뮬레이션(최대 데미지 봇). 샘플 k의 상대 팀으로 한 판씩 두고, 칸이 달라도 샘플 k에는 같은 시드를 쓴다
   (공통 난수로 칸 사이 비교의 잡음을 줄인다).
4. 더블 오라클 + 선형계획 (solver.py), 시간 예산 안에서 멈춘다.
5. 상대 모델(사용률 × 상성 가정)에 대한 최선 응수와 균형 해를 섞는다.

메가: 내 선출은 소지자 최대 max_mega_in_pick마리. 선출 안에서 출전 순서상 첫 소지자를 메가 폼으로 센다(상대도 같음).
"""

from __future__ import annotations

import random
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from pokechamp.config import select_config, teambuild_config
from pokechamp.data.db import active_version
from pokechamp.data.priors import Priors
from pokechamp.data.static.names import to_id
from pokechamp.engine_client import EngineClient
from pokechamp.matchup.fast import MatchupCache, set_key
from pokechamp.select.picks import Pick, enumerate_picks, mega_slot
from pokechamp.select.solver import PreciseCells, double_oracle, mix_with_model, opponent_model
from pokechamp.sets.archetypes import load_archetypes, sample_sets
from pokechamp.teambuild.game import payoff_tensor
from pokechamp.teambuild.search import Names

ENGINE_KEYS = ("species", "item", "ability", "nature", "evs", "moves")
BATCH = 400


class SelectError(RuntimeError):
    pass


def parse_opponents(conn: sqlite3.Connection, text: str) -> tuple[list[str], list[str]]:
    """상대 6마리 이름(한국어/영어, 쉼표로 구분. 쉼표가 없으면 공백으로 구분) → (기본 폼 id 목록, 안내 문장).
    메가 폼 이름을 넣으면 기본 폼으로 바꾼다 (팀 프리뷰에서는 메가 전 모습만 보이고, 메가스톤은 세트 통계로 추정한다)."""
    from pokechamp.input.team_text import NameIndex, TeamParseError

    idx = NameIndex(conn)       # 팀 입력용 표라 메가 폼은 없다
    v = active_version(conn)
    vid = v["id"] if v else -1
    base_of = {r[0]: r[1] for r in conn.execute(
        "SELECT mega_species_id, base_species_id FROM mega_forms WHERE version_id=?", (vid,))}
    mega_names: dict[str, str] = {}
    for r in conn.execute("SELECT id, name_en, name_ko FROM species WHERE version_id=? AND is_mega=1", (vid,)):
        for key in (r["id"], to_id(r["name_en"]), idx.norm(r["name_ko"] or "")):
            if key:
                mega_names[key] = r["id"]
    parts = [x.strip() for x in (text.split(",") if "," in text else text.split())]
    ids, notes, errors = [], [], []
    for name in [x for x in parts if x]:
        mega = mega_names.get(idx.norm(name)) or mega_names.get(to_id(name))
        if mega and mega in base_of:
            notes.append(f"'{name}'은(는) 메가 폼이라 기본 폼으로 바꿨다 (메가스톤 소지는 세트 통계로 추정)")
            ids.append(base_of[mega])
            continue
        try:
            ids.append(idx.find("species", name)[0])
        except TeamParseError as e:
            errors.append(str(e))
    if errors:
        raise SelectError("\n".join(errors))
    return ids, notes


def engine_set(st: dict[str, Any]) -> dict[str, Any]:
    return {k: st[k] for k in ENGINE_KEYS if st.get(k) not in (None, "")}


class _States:
    """(세트, 메가 여부) 상태에 번호를 붙인다 (같은 세트는 같은 번호)."""

    def __init__(self) -> None:
        self.states: list[tuple[dict[str, Any], bool]] = []
        self.index: dict[tuple[str, bool], int] = {}

    def add(self, st: dict[str, Any], mega: bool) -> int:
        k = (set_key(st), mega)
        if k not in self.index:
            self.index[k] = len(self.states)
            self.states.append((st, mega))
        return self.index[k]


@dataclass
class _Side:
    sets: list[dict[str, Any]]
    rows: list[int]
    mega_rows: list[int | None]

    @property
    def holders(self) -> set[int]:
        return {i for i, r in enumerate(self.mega_rows) if r is not None}

    def pick_rows(self, p: Pick) -> list[int]:
        m = mega_slot(p, self.holders)
        return [self.mega_rows[i] if i == m else self.rows[i] for i in p.order]   # type: ignore[misc]


class Selector:
    def __init__(self, conn: sqlite3.Connection, client: EngineClient | None = None, cfg: dict[str, Any] | None = None,
                 log: Callable[[str], None] | None = None):
        self.conn = conn
        self.client = client or EngineClient()
        self.cfg = {**select_config(), **(cfg or {})}
        self.w_assign = float(teambuild_config()["assignment_weight"])
        v = active_version(conn)
        if not v:
            raise SelectError("정적 데이터가 없다. `pokechamp update-static`을 먼저 실행하세요.")
        self.vid, self.regulation = v["id"], v["regulation"]
        self.names = Names(conn, self.vid)
        self.stones = {(r[0], r[2]): r[1] for r in conn.execute(
            "SELECT base_species_id, mega_species_id, stone_item_id FROM mega_forms WHERE version_id=?", (self.vid,))}
        self.priors = Priors(conn)
        self.cache = MatchupCache(conn, self.client)
        self.log = log or (lambda s: None)

    # ---------- 입력 ----------
    def mega_of(self, st: dict[str, Any]) -> str | None:
        return self.stones.get((to_id(st["species"]), to_id(st.get("item") or "")))

    def opponent_samples(self, opp: list[str], k: int, seed: str) -> list[list[dict[str, Any]]]:
        """상대 6마리의 세트를 원형 분포에서 k팀 뽑는다. 한 팀 안에서 도구는 겹치지 않게 한다(도구 중복 금지 규칙)."""
        rng = random.Random(f"{seed}-opp-{'-'.join(opp)}")
        archs = {sp: load_archetypes(self.conn, sp, self.regulation) for sp in opp}
        missing = [self.names.ko("species", sp) for sp, a in archs.items() if not a]
        if missing:
            raise SelectError(f"세트 원형이 없는 포켓몬: {', '.join(missing)}. `pokechamp build-sets`를 실행하세요.")
        teams = []
        for _ in range(k):
            used: set[str] = set()
            team = []
            for sp in opp:
                st = None
                for _ in range(20):
                    st = sample_sets(archs[sp], 1, rng)[0]
                    if not st["item"] or st["item"] not in used:
                        break
                assert st is not None
                if st["item"]:
                    used.add(st["item"])
                team.append(st)
            teams.append(team)
        return teams

    # ---------- 근사 행렬 ----------
    def approx_matrix(self, me: _Side, my_picks: list[Pick], opp: list[_Side], opp_picks: list[Pick],
                      M: np.ndarray) -> np.ndarray:
        lam = float(self.cfg["lead_weight"])
        myR = np.array([me.pick_rows(p) for p in my_picks], dtype=np.int64)                        # (nA, 3)
        oppR = np.array([[o.pick_rows(p) for p in opp_picks] for o in opp], dtype=np.int64)        # (K, nB, 3)
        base = payoff_tensor(M, myR, oppR, self.w_assign)                                          # (K, nA, nB)
        lead = M[myR[None, :, 0, None], oppR[:, None, :, 0]]                                       # (K, nA, nB)
        return ((1 - lam) * base + lam * lead).mean(axis=0)

    # ---------- 정밀 칸 ----------
    def evaluator(self, my_sets: list[dict], my_picks: list[Pick], opp_teams: list[list[dict]], opp_picks: list[Pick],
                  precise: PreciseCells, seed: str, deadline: float, stats: dict[str, Any], max_games: int | None = None):
        rate = {"gps": float(self.cfg["games_per_second_estimate"])}
        p1 = [engine_set(s) for s in my_sets]
        p2 = [[engine_set(s) for s in t] for t in opp_teams]

        def evaluate(reqs: list[tuple[tuple[int, int], int]]) -> int:
            if max_games is not None:      # 판 수 예산: 처리 속도와 무관하게 재현된다
                allowed = max(0, max_games - stats["games"])
            else:                          # 시간 예산: 추정 처리량의 90%만 계획한다 (예산을 넘기지 않게)
                allowed = int(max(0.0, deadline - time.time()) * rate["gps"] * 0.9)
            plan: list[tuple[tuple[int, int], list[int]]] = []
            games = 0
            for cell, n in reqs:
                start = precise.games.get(cell, 0)
                ks = list(range(start, min(start + n, len(opp_teams))))
                if games + len(ks) > allowed:
                    break
                plan.append((cell, ks))
                games += len(ks)
            items, owners = [], []
            for cell, ks in plan:
                i, j = cell
                for k in ks:
                    items.append({"p1": p1, "p2": p2[k], "seed": f"{seed}-s{k}", "policy": "maxdamage", "reps": 1,
                                  "picks": {"p1": my_picks[i].choice(), "p2": opp_picks[j].choice()}})
                    owners.append(cell)
            acc: dict[tuple[int, int], list[float]] = {}
            sent = 0
            while sent < len(items):
                if max_games is None and time.time() >= deadline:
                    break                  # 처리량이 갑자기 떨어져도 예산에서 멈춘다
                # 시간 예산이면 한 번에 약 1초 분량만 보낸다 (마감 직전에 큰 묶음이 걸리지 않게)
                chunk = BATCH if max_games is not None else max(32, min(BATCH, int(rate["gps"])))
                t = time.time()
                res = self.client.batch("/simulate", items[sent:sent + chunk])
                dt = max(1e-3, time.time() - t)
                rate["gps"] = 0.5 * rate["gps"] + 0.5 * (len(res) / dt)
                for cell, r in zip(owners[sent:sent + chunk], res):
                    if not r.get("ok") or r["result"]["wins"]["unfinished"]:
                        stats["errors"] += 1
                        acc.setdefault(cell, [])
                        continue
                    w = r["result"]["wins"]
                    acc.setdefault(cell, []).append(1.0 if w["p1"] else 0.0 if w["p2"] else 0.5)
                sent += len(res)
            stats["games"] += sent
            stats["games_per_second"] = round(rate["gps"], 1)
            done = 0
            for cell, ks in plan:
                if cell not in acc:        # 마감으로 못 보낸 칸: 여기서부터는 계산하지 못한 것으로 돌려준다
                    break
                sc = acc[cell]
                if sc:
                    precise.add(cell, sum(sc), len(sc))
                elif cell not in precise:
                    precise.add(cell, 0.5, 1)       # 모든 판이 오류면 0.5 한 판으로 둔다 (errors에 셈)
                done += 1
            return done

        return evaluate

    # ---------- 실행 ----------
    def run(self, my_team: list[dict[str, Any]], opp_species: list[str], seed: Any = None, budget_s: float | None = None,
            team_problems: list[str] | None = None, max_games: int | None = None) -> dict[str, Any]:
        """budget_s: 시간 예산(초). max_games를 주면 시간 대신 3:3 시뮬레이션 판 수로 예산을 정한다(같은 시드면 같은 결과)."""
        t0 = time.time()
        cfg = self.cfg
        seed = str(seed if seed is not None else cfg["seed"])
        budget = float(budget_s if budget_s is not None else cfg["time_budget_s"])
        deadline = t0 + budget
        if len(opp_species) != 6:
            raise SelectError(f"상대 포켓몬은 6마리여야 한다 (지금 {len(opp_species)}마리)")
        if len(my_team) < 3:
            raise SelectError(f"내 팀이 {len(my_team)}마리다. 3마리 이상이 필요하다")
        warnings: list[str] = []
        if w := self.priors.warning():
            warnings.append(w)
        if team_problems:
            warnings.append(f"내 팀에 규칙 위반 경고가 {len(team_problems)}개 있다. 이 결과는 경고가 있는 팀으로 계산했다")
        if len(my_team) != 6:
            warnings.append(f"내 팀이 {len(my_team)}마리다 (게임 규칙은 6마리)")

        # 상태와 1:1 상성
        K = int(cfg["opponent_samples"])
        opp_teams = self.opponent_samples(opp_species, K, seed)
        mine, theirs = _States(), _States()
        me = _Side(my_team, [mine.add(s, False) for s in my_team],
                   [mine.add(s, True) if self.mega_of(s) else None for s in my_team])
        opp = [_Side(t, [theirs.add(s, False) for s in t], [theirs.add(s, True) if s.get("mega_species") else None for s in t])
               for t in opp_teams]
        max_mega = int(cfg["max_mega_in_pick"])
        my_picks = enumerate_picks(len(my_team), me.holders, max_mega)
        if any(len(p.members & me.holders) > max_mega for p in my_picks):
            warnings.append(f"메가스톤 소지자가 너무 많아 소지자 {max_mega}마리 이하 선출을 만들 수 없다. 제한 없이 계산했다")
        opp_picks = enumerate_picks(6)
        pairs = [((sa, ma), (sb, mb)) for sa, ma in mine.states for sb, mb in theirs.states]
        flat = self.cache.scores(pairs)
        M = np.array(flat).reshape(len(mine.states), len(theirs.states))
        A = self.approx_matrix(me, my_picks, opp, opp_picks, M)
        t_approx = time.time() - t0
        self.log(f"근사 행렬 {A.shape[0]}×{A.shape[1]} (상대 세트 샘플 {K}팀, 1:1 상성 새로 계산 {self.cache.computed}쌍), "
                 f"{t_approx:.1f}초")

        # 더블 오라클
        precise = PreciseCells()
        stats: dict[str, Any] = {"games": 0, "errors": 0, "games_per_second": None}
        evaluate = self.evaluator(my_team, my_picks, opp_teams, opp_picks, precise, seed, deadline, stats, max_games)
        r = double_oracle(A, evaluate, precise, initial=int(cfg["initial_candidates"]),
                          add_per_iter=int(cfg["add_per_iteration"]), eps=float(cfg["oracle_epsilon"]),
                          samples=int(cfg["precise_samples"]), max_samples=K,
                          probe=int(cfg["probe_candidates"]), probe_rounds=int(cfg["probe_rounds"]),
                          final=str(cfg["final_solve"]))
        self.log(f"더블 오라클: 반복 {r.iterations}회, 후보 {len(r.rows)}×{len(r.cols)}, 정밀 칸 {len(precise)}개, "
                 f"{stats['games']:,}판, {'수렴' if r.converged else '예산 종료'}")

        # 상대 모델과 혼합
        sw = self.priors.species_weights()
        floor = min(sw.values()) if sw else 1.0
        wsp = np.array([sw.get(sp, floor) for sp in opp_species]) ** float(cfg["opponent_usage_exponent"])
        # 로컬 선출률(상대 모델 학습, 단계 6): 내 실제 배틀에서 그 종이 보였을 때 실제로 나온 비율. 기록이 없으면 모두 0.5라 영향 없음
        from pokechamp.data.local_meta import bring_rates, bring_stats, local_records
        rates = bring_rates(bring_stats(local_records(self.conn, self.regulation)), 0.5,
                            float(self.priors.cfg["local_bring_prior_n"]))
        wsp = wsp * np.array([rates.get(sp, 0.5) / 0.5 for sp in opp_species])
        prior = np.array([np.prod(wsp[list(p.order)]) for p in opp_picks])
        q = opponent_model(r.H, list(range(len(my_picks))), prior / prior.sum(), float(cfg["opponent_model_temperature"]))
        mix = float(cfg["opponent_model_mix"])
        s, br = mix_with_model(r, q, mix)
        return self._result(locals())

    # ---------- 출력 ----------
    def _result(self, L: dict[str, Any]) -> dict[str, Any]:
        r, s, q, M = L["r"], L["s"], L["q"], L["M"]
        my_team, opp_species, opp_teams = L["my_team"], L["opp_species"], L["opp_teams"]
        me, opp, my_picks, opp_picks = L["me"], L["opp"], L["my_picks"], L["opp_picks"]
        H, y = r.H, r.y
        ko = lambda st: self.names.ko("species", to_id(st["species"])) or st["species"]   # noqa: E731
        opp_ko = [self.names.ko("species", sp) for sp in opp_species]
        # 상대 포켓몬별 출전 확률(균형 기준)과 메가스톤 소지 확률(세트 샘플 기준)
        bring = np.zeros(6)
        for j, p in enumerate(opp_picks):
            for t in p.order:
                bring[t] += y[j]
        mega_p = [sum(1 for t in opp_teams if t[i].get("mega_species")) / len(opp_teams) for i in range(6)]
        # 1:1 상성: 내 슬롯 상태 × 상대 슬롯 (샘플 평균, 상대는 기본 폼)
        def one_on_one(row: int) -> np.ndarray:
            return np.array([np.mean([M[row, o.rows[t]] for o in opp]) for t in range(6)])

        show = float(self.cfg["show_min_probability"])
        picks_out = []
        for i in np.argsort(-s):
            if s[i] < show:
                break
            p = my_picks[int(i)]
            rows = me.pick_rows(p)
            mm = np.max([one_on_one(rw) for rw in rows], axis=0)
            order = np.argsort(-bring)
            strong = [opp_ko[t] for t in order if mm[t] >= 0.65][:3]
            weak = [opp_ko[t] for t in order if mm[t] < 0.4][:2]
            m = mega_slot(p, me.holders)
            cells = [(int(i), int(j)) for j in np.nonzero(y > 1e-9)[0]]   # 상대 균형 지지 선출과의 칸
            picks_out.append({
                "index": int(i), "probability": float(s[i]), "equilibrium_probability": float(r.x[i]),
                "lead": ko(my_team[p.lead]), "members": [ko(my_team[k]) for k in p.order], "slots": [k + 1 for k in p.order],
                "member_species": [my_team[k]["species"] for k in p.order],
                "choice": p.choice(),
                "mega": (self.names.ko("species", self.mega_of(my_team[m])) if m is not None else None),
                "win_vs_equilibrium": float(H[i] @ y), "win_vs_model": float(H[i] @ q),
                "precise_share": sum(1 for c in cells if c in r.precise) / max(1, len(cells)),
                "strong": strong, "weak": weak,
                "opponent_model_best_response": int(i) == L["br"],
            })
        opp_out = []
        for j in np.argsort(-y)[:3]:
            if y[j] < show:
                break
            p = opp_picks[int(j)]
            opp_out.append({"probability": float(y[j]), "lead": opp_ko[p.lead], "members": [opp_ko[t] for t in p.order]})
        nA, nB = H.shape
        return {
            "seed": L["seed"], "budget_s": None if L["max_games"] is not None else L["budget"], "max_games": L["max_games"], "elapsed_s": round(time.time() - L["t0"], 1),
            "approx_seconds": round(L["t_approx"], 1),
            "opponent": [{"species": opp_ko[i], "id": opp_species[i], "name_en": self.names.en("species", opp_species[i]),
                          "bring_probability": float(bring[i]), "mega_stone_probability": mega_p[i]} for i in range(6)],
            "picks": picks_out,
            "opponent_likely_picks": opp_out,
            "value_equilibrium": float(r.value),
            "expected_win_vs_equilibrium": float(s @ H @ y),
            "expected_win_vs_model": float(s @ H @ q),
            "opponent_model_mix": L["mix"],
            "matrix": {
                "size": [nA, nB], "candidates": [len(r.rows), len(r.cols)],
                "precise_cells": len(r.precise), "approx_only_share": 1 - len(r.precise) / (nA * nB),
                "candidate_precise_share": r.candidate_precise_ratio(),
                "weighted_precise_share": r.weighted_precise_ratio(s),
                "rows": [my_picks[i].choice() for i in r.rows], "cols": [opp_picks[j].choice() for j in r.cols],
                "values": [[float(H[i, j]) for j in r.cols] for i in r.rows],
                "precise": [[(i, j) in r.precise for j in r.cols] for i in r.rows],
                "calibration": {"intercept": r.calibration[0], "slope": r.calibration[1]},
            },
            "oracle": {"iterations": r.iterations, "converged": r.converged, "refined": r.refined, "history": r.history},
            "games": L["stats"]["games"], "errors": L["stats"]["errors"], "games_per_second": L["stats"]["games_per_second"],
            "freshness": self.priors.freshness().label(), "regulation": self.regulation,
            "warnings": L["warnings"],
            "policy": "정밀 칸: 엔진 3:3 시뮬레이션, 최대 데미지 봇(스스로 교체하지 않음, 변화기 안 씀, 쓰러진 뒤 나올 포켓몬은 무작위)",
        }
