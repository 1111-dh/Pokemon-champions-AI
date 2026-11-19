"""파티 추천 (PROJECT_PLAN 7절): 빔 서치로 채우고 국소 탐색으로 다듬은 뒤, 선형계획으로 최종 점수와 해설을 낸다.

재현성: 같은 DB(정적 데이터 버전, 통계 스냅샷, 세트 원형), 같은 설정, 같은 시드면 같은 팀이 나온다.
판단은 전부 상성 행렬과 균형 계산이 한다 (LLM 없음).
"""

from __future__ import annotations

import sqlite3
import time
from itertools import combinations
from typing import Any, Callable

import numpy as np

from pokechamp.config import teambuild_config
from pokechamp.data.db import active_version
from pokechamp.data.priors import Priors
from pokechamp.data.static.names import to_id
from pokechamp.engine_client import EngineClient
from pokechamp.matchup.fast import MatchupCache
from pokechamp.sets.archetypes import load_archetypes, load_rules, rule_violations
from pokechamp.teambuild.meta import build_meta
from pokechamp.teambuild.pool import Pool, Threats, Unit, build_threats, species_units, unit_from_archetype
from pokechamp.teambuild.score import Evaluator, TeamScore


class RecommendError(RuntimeError):
    pass


class Names:
    def __init__(self, conn: sqlite3.Connection, vid: int):
        self.t: dict[str, dict[str, tuple[str, str]]] = {}
        for table in ("species", "items", "moves", "abilities", "natures"):
            self.t[table] = {r[0]: (r[1], r[2] or r[1]) for r in
                             conn.execute(f"SELECT id, name_en, name_ko FROM {table} WHERE version_id=?", (vid,))}

    def en(self, table: str, i: str) -> str:
        return self.t[table].get(i, (i, i))[0] if i else ""

    def ko(self, table: str, i: str) -> str:
        return self.t[table].get(i, (i, i))[1] if i else ""

    def showdown_set(self, st: dict[str, Any]) -> dict[str, Any]:
        return {"species": self.en("species", st["species"]), "item": self.en("items", st.get("item", "")),
                "ability": self.en("abilities", st.get("ability", "")), "nature": self.en("natures", st.get("nature", "")),
                "evs": {k: v for k, v in (st.get("evs") or {}).items() if v},
                "moves": [self.en("moves", m) for m in st.get("moves", [])]}

    def line_ko(self, st: dict[str, Any]) -> str:
        evs = " ".join(f"{k}{v}" for k, v in (st.get("evs") or {}).items() if v)
        return (f"{self.ko('species', st['species'])} @ {self.ko('items', st.get('item', '')) or '-'} / "
                f"{self.ko('abilities', st.get('ability', ''))} / {self.ko('natures', st.get('nature', ''))} / SP {evs} / "
                + ", ".join(self.ko("moves", m) for m in st.get("moves", [])))


class Builder:
    def __init__(self, conn: sqlite3.Connection, cfg: dict[str, Any] | None = None, seed: str | int | None = None,
                 client: EngineClient | None = None, log: Callable[[str], None] | None = None):
        self.conn = conn
        self.cfg = {**teambuild_config(), **(cfg or {})}
        self.seed = str(seed if seed is not None else self.cfg["seed"])
        self.log = log or (lambda s: None)
        v = active_version(conn)
        if not v:
            raise RecommendError("정적 데이터가 없다. `pokechamp update-static`을 먼저 실행하세요.")
        self.version = v
        self.regulation = v["regulation"]
        self.priors = Priors(conn)
        self.weights = self.priors.species_weights()
        if not self.weights:
            raise RecommendError("현재 레귤레이션 통계가 없다. `pokechamp update-stats`를 먼저 실행하세요.")
        self.rules = load_rules(conn, v["id"])
        self.names = Names(conn, v["id"])
        self.num = {r[0]: r[1] for r in conn.execute("SELECT id, num FROM species WHERE version_id=?", (v["id"],))}
        self.cache = MatchupCache(conn, client)
        self.pool = Pool(self.cache)
        self.max_mega = int(self.cfg["max_mega"])

    # ---------- 준비 ----------
    def prepare(self, fixed_species: list[str], locked_sets: list[dict[str, Any]], exclude: list[str]) -> None:
        t0 = time.time()
        self.threats: Threats = build_threats(self.conn, self.pool, self.weights, int(self.cfg["threat_species"]),
                                              int(self.cfg["threat_archetypes"]), self.regulation)
        self.meta = build_meta(self.threats, self.priors, int(self.cfg["opponent_teams"]), f"{self.seed}-meta", self.cfg)
        have = [r[0] for r in self.conn.execute("SELECT DISTINCT species_id FROM set_archetypes WHERE regulation=? ORDER BY species_id",
                                                (self.regulation,))]
        if not have:
            raise RecommendError("세트 원형이 없다. `pokechamp build-sets`를 먼저 실행하세요.")
        excluded = set(exclude) - set(fixed_species)
        self.candidates: list[Unit] = []
        for sp in have:
            if sp in excluded:
                continue
            for u in species_units(self.conn, sp, int(self.cfg["candidate_archetypes"]), self.regulation):
                self.candidates.append(self.pool.add_mine(u))
        self.start: list[Unit] = []
        for st in locked_sets:
            u = self.locked_unit(st)
            self.start.append(self.pool.add_mine(u))
        self.fixed_species = [s for s in fixed_species if all(u.species != s for u in self.start)]
        for sp in self.fixed_species:
            if not any(u.species == sp for u in self.candidates):
                raise RecommendError(f"'{self.names.ko('species', sp)}'의 세트 원형이 없다")
        n_new = self.cache.computed
        self.log(f"상성 행렬 준비: 내 후보 상태 {len(self.pool.mine.states)} × 위협 상태 {len(self.pool.opp.states)}")
        self.M = self.pool.matrix()
        self.log(f"  새로 계산 {self.cache.computed - n_new}쌍, {time.time() - t0:.1f}초")
        self.wcol = self.threats.column_weights(self.M.shape[1])
        self.ev = Evaluator(self.pool, self.meta, self.cfg)

    def locked_unit(self, st: dict[str, Any]) -> Unit:
        s = {"species": to_id(st["species"]), "item": to_id(st.get("item") or ""), "ability": to_id(st.get("ability") or ""),
             "nature": to_id(st.get("nature") or ""), "evs": {k: int(v) for k, v in (st.get("evs") or {}).items() if v},
             "moves": [to_id(m) for m in st.get("moves", [])]}
        base = s["species"]
        r = self.rules.get(base)
        mega = r.stones.get(s["item"]) if r else None
        return Unit(base, s, mega, 1.0, False, locked=True)

    # ---------- 제약 ----------
    def can_add(self, team: list[Unit], u: Unit) -> bool:
        if any(self.num.get(t.species) == self.num.get(u.species) for t in team):
            return False
        if u.item and any(t.item == u.item for t in team):
            return False
        if u.mega_species and sum(1 for t in team if t.mega_species) >= self.max_mega:
            return False
        return True

    @staticmethod
    def eff(u: Unit) -> int:
        return u.mega_row if u.mega_row is not None else u.row

    def team_key(self, team: list[Unit]) -> str:
        return "|".join(sorted(u.key for u in team))

    def cheap_gain(self, team: list[Unit], cands: list[Unit]) -> np.ndarray:
        E = self.M[[self.eff(u) for u in cands]]
        cov = self.M[[self.eff(u) for u in team]].max(0) if team else np.zeros(self.M.shape[1])
        return (np.maximum(E, cov) - cov) @ self.wcol + 0.1 * (E @ self.wcol)

    # ---------- 빔 서치 ----------
    def beam(self) -> list[tuple[TeamScore, list[Unit]]]:
        beams = [list(self.start)]
        # 종만 고정한 포켓몬: 원형 후보마다 빔을 나눠 시작한다
        for sp in self.fixed_species:
            nxt = []
            for team in beams:
                for u in [c for c in self.candidates if c.species == sp]:
                    if self.can_add(team, u):
                        nxt.append(team + [u])
            beams = nxt or beams
        width, pre = int(self.cfg["beam_width"]), int(self.cfg["prefilter"])
        scored = [(self.ev.score(t), t) for t in beams] if beams[0] else []
        scored.sort(key=lambda x: (-x[0].score, self.team_key(x[1])))
        beams = [t for _, t in scored[:width]] or beams
        while len(beams[0]) < 6:
            seen, nxt = set(), []
            for team in beams:
                valid = [u for u in self.candidates if self.can_add(team, u)]
                if not valid:
                    continue
                g = self.cheap_gain(team, valid)
                order = sorted(range(len(valid)), key=lambda i: (-g[i], valid[i].key))[:pre]
                for i in order:
                    new = team + [valid[i]]
                    k = self.team_key(new)
                    if k not in seen:
                        seen.add(k)
                        nxt.append(new)
            if not nxt:
                break
            scored = [(self.ev.score(t), t) for t in nxt]
            scored.sort(key=lambda x: (-x[0].score, self.team_key(x[1])))
            beams = [t for _, t in scored[:width]]
            self.log(f"  {len(beams[0])}마리: 최고 점수 {scored[0][0].score:.3f} (평가 {len(nxt)}팀)")
        return scored[:width]

    # ---------- 국소 탐색 ----------
    def variants(self, u: Unit) -> list[Unit]:
        out: dict[str, Unit] = {}
        r = self.rules.get(u.species)
        for a in load_archetypes(self.conn, u.species, self.regulation)[:6]:
            nu = unit_from_archetype(a)
            out.setdefault(nu.key, nu)
        if r:
            for it, _ in self.priors.marginal(u.species, "items").values[:6]:
                if it == u.item:
                    continue
                st = {**u.set, "item": it}
                if not rule_violations(r, it, st["ability"] or None, tuple(st["moves"]), st["evs"]):
                    nu = Unit(u.species, st, r.stones.get(it), 1.0)
                    out.setdefault(nu.key, nu)
            moves = list(u.set["moves"])
            top = [m for m, _ in self.priors.marginal(u.species, "moves").values[:10] if m not in moves][:6]
            for slot in range(len(moves)):
                for m in top:
                    mv = moves[:slot] + [m] + moves[slot + 1:]
                    if not rule_violations(r, u.item or None, u.set["ability"] or None, tuple(mv), u.set["evs"]):
                        nu = Unit(u.species, {**u.set, "moves": mv}, u.mega_species, 1.0)
                        out.setdefault(nu.key, nu)
        out.pop(u.key, None)
        return list(out.values())

    def local_search(self, team: list[Unit], best: TeamScore) -> tuple[list[Unit], TeamScore, list[str]]:
        changes = []
        for rnd in range(int(self.cfg["local_search_rounds"])):
            trials: list[tuple[int, Unit]] = []
            for i, u in enumerate(team):
                if u.locked:
                    continue
                for nu in self.variants(u):
                    rest = team[:i] + team[i + 1:]
                    if self.can_add(rest, nu):
                        trials.append((i, self.pool.add_mine(nu)))
            if not trials:
                break
            n0 = self.cache.computed
            self.M = self.pool.matrix()
            results = []
            for i, nu in trials:
                t = team[:i] + [nu] + team[i + 1:]
                results.append((self.ev.score(t), i, nu, t))
            results.sort(key=lambda x: (-x[0].score, x[2].key))
            s, i, nu, t = results[0]
            self.log(f"  국소 탐색 {rnd + 1}회: 변형 {len(trials)}개 (새 상성 {self.cache.computed - n0}쌍), "
                     f"최고 {s.score:.3f} (현재 {best.score:.3f})")
            if s.score <= best.score + 1e-6:
                break
            changes.append(f"{self.names.ko('species', nu.species)}: {self.describe_change(team[i], nu)}")
            team, best = t, s
        return team, best, changes

    def describe_change(self, a: Unit, b: Unit) -> str:
        parts = []
        if a.item != b.item:
            parts.append(f"도구 {self.names.ko('items', a.item) or '-'} → {self.names.ko('items', b.item) or '-'}")
        if a.set.get("ability") != b.set.get("ability"):
            parts.append(f"특성 → {self.names.ko('abilities', b.set['ability'])}")
        rm = [m for m in a.set["moves"] if m not in b.set["moves"]]
        ad = [m for m in b.set["moves"] if m not in a.set["moves"]]
        if rm or ad:
            parts.append("기술 " + ", ".join(self.names.ko("moves", m) for m in rm) + " → " + ", ".join(self.names.ko("moves", m) for m in ad))
        return "; ".join(parts) or "성격·배분 변경"

    # ---------- 해설 ----------
    def species_score(self, row: int, sp: str) -> float:
        us = self.threats.units[sp]
        tot = 0.0
        for u in us:
            s = self.M[row, u.row] if u.mega_row is None else (self.M[row, u.row] + self.M[row, u.mega_row]) / 2
            tot += u.weight * s
        return float(tot)

    def analyze(self, team: list[Unit]) -> dict[str, Any]:
        ts, A, X, Y = self.ev.exact(team)
        w0 = self.meta.scenario_weights[0]
        threat_sp = list(self.threats.units)
        S = np.array([[self.species_score(self.eff(u), sp) for sp in threat_sp] for u in team])   # (6, 위협 종)
        tw = np.array([self.threats.weights[sp] for sp in threat_sp])
        # 선출 빈도: 균형 전략에서 각 포켓몬이 선출되는 확률 (상대 팀 가중 평균)
        member_rows = [{u.row, u.mega_row} - {None} for u in team]
        inside = np.array([[bool(set(A[a]) & member_rows[i]) for a in range(len(A))] for i in range(len(team))], dtype=float)
        pick_rate = (X @ inside.T).T @ w0
        best_col = S.max(0)
        members = []
        for i, u in enumerate(team):
            # 팀 안에서 이 포켓몬이 가장 잘 맡는 위협 (출현 가중치 순)
            strong = [threat_sp[j] for j in np.argsort(-tw) if S[i, j] >= 0.6 and S[i, j] >= best_col[j] - 1e-9][:3]
            members.append({
                "set": self.names.showdown_set(u.set), "line_ko": self.names.line_ko(u.set),
                "species_ko": self.names.ko("species", u.species), "mega": self.names.ko("species", u.mega_species) if u.mega_species else None,
                "locked": u.locked, "rule_generated": u.rule_generated, "pick_rate": round(float(pick_rate[i]), 3),
                "role": ("상대 " + ", ".join(self.names.ko("species", s) for s in strong) + "에 강함") if strong else "뚜렷하게 강한 상대 없음 (보조 역할)",
            })
        best = S.max(0)
        weak = [{"species": self.names.ko("species", threat_sp[j]), "weight": round(float(tw[j]), 3),
                 "best_answer": self.names.ko("species", team[int(S[:, j].argmax())].species), "best_score": round(float(best[j]), 2)}
                for j in np.argsort(-tw) if best[j] < 0.5][:6]
        megas = self.mega_check(team, S, tw, threat_sp, pick_rate)
        return {"score": ts, "members": members, "weaknesses": weak, "mega": megas,
                "opponent_values": ts.values, "my_subsets": A, "strategies": X}

    def mega_check(self, team, S, tw, threat_sp, pick_rate) -> dict[str, Any]:
        holders = [i for i, u in enumerate(team) if u.mega_species]
        out: dict[str, Any] = {"holders": [], "warnings": []}
        for h in holders:
            bad = [j for j in range(len(threat_sp)) if S[h, j] < 0.45]
            bad_w = tw[bad].sum() if bad else 0.0
            partners = [i for i in range(len(team)) if i != h and not team[i].mega_species]
            combos = []
            for a, b in combinations(partners, 2):
                cover = sum(tw[j] for j in bad if max(S[a, j], S[b, j]) >= 0.55)
                combos.append(((cover / bad_w) if bad_w else 1.0, a, b))
            combos.sort(key=lambda x: -x[0])
            name = self.names.ko("species", team[h].mega_species)
            entry = {"mega": name, "pick_rate": round(float(pick_rate[h]), 3),
                     "weak_to": [self.names.ko("species", threat_sp[j]) for j in sorted(bad, key=lambda j: -tw[j])[:5]],
                     "partners": [{"pair": [self.names.ko("species", team[a].species), self.names.ko("species", team[b].species)],
                                   "covers": round(float(c), 2)} for c, a, b in combos[:3]]}
            out["holders"].append(entry)
            if not combos:
                out["warnings"].append(f"{name}: 함께 낼 파트너가 2마리 미만이다")
            elif combos[0][0] < 0.5:
                out["warnings"].append(f"{name}: 약한 상대를 받아 줄 파트너 조합이 없다 (최선 조합이 위협 가중치의 {combos[0][0]:.0%}만 커버)")
        if len(holders) == 2:
            r = [pick_rate[h] for h in holders]
            lo = holders[int(np.argmin(r))]
            if min(r) < 0.25 * max(r):
                out["warnings"].append(f"메가 후보 2마리 중 {self.names.ko('species', team[lo].species)}는 균형 선출에서 거의 쓰이지 않는다 "
                                       f"(선출 확률 {min(r):.0%}). 다른 포켓몬으로 바꾸는 것을 고려")
        return out

    def replacements(self, team: list[Unit], ts: TeamScore, k: int = 2) -> list[dict[str, Any]]:
        """기여가 가장 작은 (고정 안 된) 포켓몬을 다른 후보로 바꿨을 때 점수 상위 k개."""
        free = [i for i, u in enumerate(team) if not u.locked and u.species not in self.fixed_species]
        if not free:
            return []
        contrib = {i: ts.score - self.ev.score(team[:i] + team[i + 1:]).score for i in free}
        i = min(contrib, key=contrib.get)
        rest = team[:i] + team[i + 1:]
        valid = [u for u in self.candidates if self.can_add(rest, u) and u.species != team[i].species]
        g = self.cheap_gain(rest, valid)
        order = sorted(range(len(valid)), key=lambda j: (-g[j], valid[j].key))[:12]
        res = sorted(((self.ev.score(rest + [valid[j]]).score, valid[j]) for j in order), key=lambda x: (-x[0], x[1].key))
        base = self.ev.score(team).score
        return [{"out": self.names.ko("species", team[i].species), "in": self.names.ko("species", u.species),
                 "line_ko": self.names.line_ko(u.set), "score_delta": round(float(s - base), 3)} for s, u in res[:k]]

    # ---------- 전체 ----------
    def run(self, fixed_species: list[str] | None = None, locked_sets: list[dict[str, Any]] | None = None,
            exclude: list[str] | None = None) -> dict[str, Any]:
        t0 = time.time()
        self.prepare(fixed_species or [], locked_sets or [], exclude or [])
        self.log(f"빔 서치 ({time.time() - t0:.0f}초)")
        top = self.beam()
        team, best = top[0][1], top[0][0]
        self.log(f"국소 탐색 ({time.time() - t0:.0f}초)")
        team, best, changes = self.local_search(team, best)
        self.log(f"최종 평가: 선형계획 ({time.time() - t0:.0f}초)")
        an = self.analyze(team)
        ts: TeamScore = an["score"]
        warnings = [w for w in [self.priors.warning()] if w]
        if any(u.rule_generated for u in team):
            warnings.append("세트는 완성 세트가 아니라 통계를 조합한 추정이다 (rule_generated)")
        warnings += an["mega"]["warnings"]
        return {
            "seed": self.seed, "regulation": self.regulation, "static_version": self.version["id"],
            "stats": self.priors.freshness().label(), "elapsed_s": round(time.time() - t0, 1),
            "team": [m["set"] for m in an["members"]], "members": an["members"],
            "score": {"score": round(float(ts.score), 4), "mean": round(float(ts.mean), 4), "cvar": round(float(ts.cvar), 4), "sd": round(float(ts.sd), 4),
                      "alpha": self.cfg["alpha"], "beta": self.cfg["beta"], "cvar_fraction": self.cfg["cvar_fraction"]},
            "scenarios": [{"name": n, "expected": round(v, 4)} for n, v in zip(self.meta.scenario_names, ts.scenario_means)],
            "weaknesses": an["weaknesses"], "mega": an["mega"], "local_search_changes": changes,
            "replacements": self.replacements(team, ts), "notes": self.meta.notes, "warnings": warnings,
            "evaluations": self.ev.evals, "new_matchups": self.cache.computed,
            "_units": team,
        }
