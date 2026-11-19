"""세트 원형: 결합 분포 만들기 (PROJECT_PLAN 4.7).

사용률 통계는 기술·도구·특성·성격·배분을 따로 집계한 주변 분포다. 이를 독립으로 곱해서 세트를 뽑으면
"구애스카프 + 칼춤" 같은 없는 세트가 나온다. 이 모듈은:
1. 완성 세트(team_samples)가 있으면 (도구, 특성, 기술 4개)가 같은 것끼리 묶어 원형을 만든다 (n_support).
2. 완성 세트가 없거나 부족하면 주변 분포에서 후보를 뽑되 규칙 필터를 통과한 것만 원형으로 쓴다 (rule_generated).
3. 원형 가중치를 IPF(반복 비례 맞춤)로 조정해 원형들로 계산한 도구·특성·기술 채용률이 최신 통계와 맞게 한다.
4. 성격과 배분은 원형 안의 분포로 두고, 원형의 공격 성향(물리/특수)과 맞지 않는 성격·배분은 가중치를 낮춘다.

규칙(rule_violations): 학습 불가 기술 제외, 기술 중복 금지, 구애 계열·돌격조끼에는 변화기 금지(구애 계열의 트릭·바꿔치기·변신은 허용),
메가스톤은 그 포켓몬의 것만, 특성은 그 포켓몬의 것만, Stat Points 능력치당 32·합계 66.
"""

from __future__ import annotations

import json
import random
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pokechamp.config import sets_config
from pokechamp.data.db import active_version
from pokechamp.data.priors import Priors
from pokechamp.data.static.names import to_id

CHOICE_ITEMS = {"choicescarf", "choiceband", "choicespecs"}
NO_STATUS_ITEMS = CHOICE_ITEMS | {"assaultvest"}
CHOICE_STATUS_OK = {"trick", "switcheroo", "transform"}  # 구애 트릭, 구애스카프 메타몽
STATS = ("hp", "atk", "def", "spa", "spd", "spe")
NATURE_EFFECT = {  # 성격: (오르는 능력치, 내리는 능력치)
    "lonely": ("atk", "def"), "brave": ("atk", "spe"), "adamant": ("atk", "spa"), "naughty": ("atk", "spd"),
    "bold": ("def", "atk"), "relaxed": ("def", "spe"), "impish": ("def", "spa"), "lax": ("def", "spd"),
    "timid": ("spe", "atk"), "hasty": ("spe", "def"), "jolly": ("spe", "spa"), "naive": ("spe", "spd"),
    "modest": ("spa", "atk"), "mild": ("spa", "def"), "quiet": ("spa", "spe"), "rash": ("spa", "spd"),
    "calm": ("spd", "atk"), "gentle": ("spd", "def"), "sassy": ("spd", "spe"), "careful": ("spd", "spa"),
}


@dataclass
class SpeciesRules:
    species: str
    learnable: set[str]
    move_category: dict[str, str]
    abilities: set[str]
    stones: dict[str, str]          # 메가스톤 id → 메가 폼 id (이 포켓몬의 것)
    all_stones: set[str]            # 모든 메가스톤 id
    known_items: set[str]


@dataclass
class Archetype:
    species: str
    item: str | None
    ability: str | None
    moves: tuple[str, ...]
    weight: float
    n_support: int = 0
    rule_generated: bool = True
    mega_species: str | None = None
    nature_dist: dict[str, float] = field(default_factory=dict)
    spread_dist: list[tuple[dict[str, int], float]] = field(default_factory=list)

    @property
    def key(self) -> tuple:
        return (self.item, self.ability, self.moves)


def load_rules(conn: sqlite3.Connection, vid: int) -> dict[str, SpeciesRules]:
    cat = {r[0]: r[1] for r in conn.execute("SELECT id, category FROM moves WHERE version_id=?", (vid,))}
    learn: dict[str, set[str]] = defaultdict(set)
    for sid, mid in conn.execute("SELECT species_id, move_id FROM learnsets WHERE version_id=?", (vid,)):
        learn[sid].add(mid)
    stones: dict[str, dict[str, str]] = defaultdict(dict)
    all_stones = set()
    for base, mega, stone in conn.execute("SELECT base_species_id, mega_species_id, stone_item_id FROM mega_forms WHERE version_id=?", (vid,)):
        stones[base][stone] = mega
        all_stones.add(stone)
    items = {r[0] for r in conn.execute("SELECT id FROM items WHERE version_id=?", (vid,))}
    out = {}
    for r in conn.execute("SELECT id, abilities_json FROM species WHERE version_id=? AND is_mega=0", (vid,)):
        out[r["id"]] = SpeciesRules(r["id"], learn[r["id"]], cat, {to_id(a) for a in json.loads(r["abilities_json"]).values()},
                                    stones[r["id"]], all_stones, items)
    return out


def item_violations(rules: SpeciesRules, item: str | None) -> list[str]:
    v = []
    if item and item in rules.all_stones and item not in rules.stones:
        v.append(f"다른 포켓몬의 메가스톤 {item}")
    if item and item not in rules.known_items:
        v.append(f"모르는 도구 {item}")
    return v


def move_allowed(rules: SpeciesRules, item: str | None, move: str) -> bool:
    if move not in rules.learnable:
        return False
    if item in NO_STATUS_ITEMS and rules.move_category.get(move) == "Status":
        return item in CHOICE_ITEMS and move in CHOICE_STATUS_OK
    return True


def rule_violations(rules: SpeciesRules, item: str | None, ability: str | None, moves: tuple[str, ...] | list[str],
                    spread: dict[str, int] | None = None) -> list[str]:
    v = item_violations(rules, item)
    if len(set(moves)) != len(moves):
        v.append("기술 중복")
    if len(moves) > 4 or len(moves) < min(4, len(rules.learnable)):
        v.append("기술 수")
    for m in moves:
        if m not in rules.learnable:
            v.append(f"학습 불가 {m}")
    if item in NO_STATUS_ITEMS:
        for m in moves:
            if rules.move_category.get(m) == "Status" and not (item in CHOICE_ITEMS and m in CHOICE_STATUS_OK):
                v.append(f"{item} + 변화기 {m}")
    if ability and ability not in rules.abilities:
        v.append(f"가질 수 없는 특성 {ability}")
    if spread is not None:
        if any(spread.get(s, 0) > 32 or spread.get(s, 0) < 0 for s in STATS) or sum(spread.get(s, 0) for s in STATS) > 66:
            v.append("Stat Points 상한 초과")
    return v


def _sample_moves(rng: random.Random, pool: list[tuple[str, float]], k: int) -> tuple[str, ...]:
    """포함 확률 q에 비례하는 오즈 q/(1-q)로 비복원 추출 (포함 확률이 높은 기술이 더 자주 뽑힌다)."""
    items = [(m, max(1e-4, min(0.98, q)) / (1 - max(1e-4, min(0.98, q)))) for m, q in pool]
    chosen = []
    for _ in range(min(k, len(items))):
        tot = sum(w for _, w in items)
        x = rng.random() * tot
        for i, (m, w) in enumerate(items):
            x -= w
            if x <= 0:
                chosen.append(m)
                items.pop(i)
                break
        else:
            chosen.append(items.pop()[0])
    return tuple(sorted(chosen))


def _orientation(rules: SpeciesRules, moves: tuple[str, ...]) -> str:
    phys = sum(rules.move_category.get(m) == "Physical" for m in moves)
    spec = sum(rules.move_category.get(m) == "Special" for m in moves)
    return "physical" if phys > spec else "special" if spec > phys else "mixed"


def _conditioned(nat: list[tuple[str, float]], spreads: list[tuple[str, float]], orient: str):
    """원형의 공격 성향과 맞지 않는 성격(주 공격 능력치를 내림)과 배분(안 쓰는 공격 능력치에 투자)의 가중치를 낮춘다."""
    main, unused = {"physical": ("atk", "spa"), "special": ("spa", "atk")}.get(orient, (None, None))
    nd = {}
    for n, p in nat:
        up, down = NATURE_EFFECT.get(n, (None, None))
        nd[n] = p * (0.1 if main and down == main else 1.0)
    sd = []
    for sj, p in spreads:
        sp = json.loads(sj) if isinstance(sj, str) else sj
        factor = 0.2 if unused and sp.get(unused, 0) >= 16 and sp.get(main, 0) < 16 else 1.0
        sd.append((sp, p * factor))
    zn, zs = sum(nd.values()) or 1, sum(p for _, p in sd) or 1
    return {k: v / zn for k, v in nd.items()}, [(s, p / zs) for s, p in sd]


def ipf(archs: list[Archetype], item_t: dict[str, float], ability_t: dict[str, float], move_t: dict[str, float],
        iterations: int) -> None:
    """원형 가중치를 도구·특성 분포와 기술 포함 확률에 맞춘다 (제자리 수정, 합 1)."""
    def norm():
        z = sum(a.weight for a in archs) or 1.0
        for a in archs:
            a.weight /= z
    norm()
    for _ in range(iterations):
        for target, attr in ((item_t, "item"), (ability_t, "ability")):
            if not target:
                continue
            cur: dict[Any, float] = defaultdict(float)
            for a in archs:
                cur[getattr(a, attr)] += a.weight
            covered = sum(target.get(k, 0) for k in cur)
            for a in archs:
                k = getattr(a, attr)
                if k in target and cur[k] > 0 and covered > 0:
                    a.weight *= (target[k] / covered) / cur[k]
            norm()
        for m, q in move_t.items():
            c = sum(a.weight for a in archs if m in a.moves)
            if c <= 0 or c >= 1:
                continue
            for a in archs:
                a.weight *= (q / c) if m in a.moves else ((1 - q) / (1 - c))
            norm()


def _fallback_archetype(rules: SpeciesRules, conn: sqlite3.Connection, vid: int) -> Archetype:
    """통계가 없는 포켓몬: 종족값과 학습 기술로 기본 세트를 만든다 (주 공격 능력치 쪽 위력 높은 기술 4개)."""
    r = conn.execute("SELECT base_stats_json, types_json FROM species WHERE version_id=? AND id=?", (vid, rules.species)).fetchone()
    stats, types = json.loads(r["base_stats_json"]), json.loads(r["types_json"])
    cat = "Physical" if stats["atk"] >= stats["spa"] else "Special"
    rows = conn.execute("SELECT id, type, power, category FROM moves WHERE version_id=?", (vid,)).fetchall()
    cands = sorted((m for m in rows if m["id"] in rules.learnable and m["category"] == cat and m["power"] > 0),
                   key=lambda m: -(m["power"] * (1.5 if m["type"] in types else 1.0)))
    moves, seen_types = [], set()
    for m in cands:
        if m["type"] not in seen_types:
            moves.append(m["id"])
            seen_types.add(m["type"])
        if len(moves) == 4:
            break
    for m in cands:
        if len(moves) == 4:
            break
        if m["id"] not in moves:
            moves.append(m["id"])
    nature = "adamant" if cat == "Physical" else "modest"
    main = "atk" if cat == "Physical" else "spa"
    return Archetype(rules.species, "lifeorb" if "lifeorb" in rules.known_items else None, sorted(rules.abilities)[0],
                     tuple(sorted(moves)), 1.0, nature_dist={nature: 1.0},
                     spread_dist=[({"hp": 2, main: 32, "spe": 32}, 1.0)])


def build_species(conn: sqlite3.Connection, vid: int, rules: SpeciesRules, priors: Priors, rng: random.Random,
                  cfg: dict[str, Any], samples: list[dict] | None = None) -> tuple[list[Archetype], bool]:
    """(원형 목록, fallback 여부)"""
    sp = rules.species
    items_d, ab_d, mv_d = priors.marginal(sp, "items"), priors.marginal(sp, "abilities"), priors.marginal(sp, "moves")
    nat_d, spr_d = priors.marginal(sp, "natures"), priors.marginal(sp, "spreads")
    if items_d.fallback and mv_d.fallback and not samples:
        return [_fallback_archetype(rules, conn, vid)], True

    item_t = {k: p for k, p in items_d.values if not item_violations(rules, k)}
    ability_t = {k: p for k, p in ab_d.values if k in rules.abilities}
    move_t = {k: p for k, p in mv_d.values if k in rules.learnable}
    archs: dict[tuple, Archetype] = {}

    # 1) 완성 세트 원형
    for s in samples or []:
        moves = tuple(sorted(to_id(m) for m in s.get("moves", [])))
        item, ability = to_id(s.get("item") or "") or None, to_id(s.get("ability") or "") or None
        if rule_violations(rules, item, ability, moves, s.get("evs")):
            continue
        a = archs.setdefault((item, ability, moves), Archetype(sp, item, ability, moves, 0.0, rule_generated=False))
        a.weight += 1.0
        a.n_support += 1

    # 2) 주변 분포 + 규칙 필터 원형
    # 출처는 상위 10개 기술만 준다. 4칸 중 상위 10개로 채워지지 않는 몫(4 - 합)을 나머지 학습 기술에 나눠 준다.
    # 각 기술의 몫은 10위 기술의 채용률보다 작게 둔다 (보정 목표는 상위 10개에만 건다).
    pool = list(move_t.items())
    residual = max(0.0, 4.0 - sum(move_t.values()))
    if move_t and residual > 0:
        cap = 0.9 * min(move_t.values())
        mv_rows = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
            "SELECT id, type, power, category FROM moves WHERE version_id=?", (vid,))}
        types = set(json.loads(conn.execute("SELECT types_json FROM species WHERE version_id=? AND id=?", (vid, sp)).fetchone()[0]))
        others = sorted(rules.learnable - set(move_t))
        w = {}
        for m in others:
            mtype, power, catg = mv_rows.get(m, ("", 0, "Status"))
            w[m] = 1.0 if catg == "Status" else max(0.2, power / 100) * (1.5 if mtype in types else 1.0)
        z = sum(w.values()) or 1.0
        pool += [(m, min(cap, residual * x / z)) for m, x in w.items()]
    if pool and item_t:
        n = int(cfg["samples_per_species"])
        item_keys, item_w = list(item_t), list(item_t.values())
        ab_keys, ab_w = (list(ability_t), list(ability_t.values())) if ability_t else (sorted(rules.abilities), None)
        rule_w = float(cfg["rule_generated_weight"]) if samples else 1.0
        for _ in range(n):
            item = rng.choices(item_keys, item_w)[0]
            ability = rng.choices(ab_keys, ab_w)[0] if ab_w else ab_keys[0]
            usable = [(m, q) for m, q in pool if move_allowed(rules, item, m)]
            if len(usable) < min(4, len(rules.learnable)):
                continue
            moves = _sample_moves(rng, usable, 4)
            if len(moves) < 4 and len(rules.learnable) >= 4:
                extra = sorted(m for m in rules.learnable - set(moves) if move_allowed(rules, item, m))
                rng.shuffle(extra)
                moves = tuple(sorted(moves + tuple(extra[: 4 - len(moves)])))
            if rule_violations(rules, item, ability, moves):
                continue
            key = (item, ability, moves)
            a = archs.get(key)
            if a is None:
                a = archs[key] = Archetype(sp, item, ability, moves, 0.0)
            if a.rule_generated:
                a.weight += rule_w

    out = [a for a in archs.values() if a.weight > 0]
    if not out:
        return [_fallback_archetype(rules, conn, vid)], True
    ipf(out, item_t, ability_t, move_t, int(cfg["ipf_iterations"]))
    out.sort(key=lambda a: -a.weight)
    # 완성 세트 근거가 있는 원형은 항상 남기고, 남은 자리를 규칙 원형 중 가중치 상위로 채운다
    supported = [a for a in out if not a.rule_generated]
    rest = [a for a in out if a.rule_generated][: max(0, int(cfg["max_archetypes"]) - len(supported))]
    out = sorted(supported + rest, key=lambda a: -a.weight)
    ipf(out, item_t, ability_t, move_t, int(cfg["ipf_iterations"]))   # 잘라낸 뒤 다시 맞춘다
    z = sum(a.weight for a in out) or 1.0
    nat = [(k, p) for k, p in nat_d.values] or [("serious", 1.0)]
    spr = [(k, p) for k, p in spr_d.values] or [(json.dumps({"hp": 2, "atk": 32, "spe": 32}), 1.0)]
    for a in out:
        a.weight /= z
        a.mega_species = rules.stones.get(a.item) if a.item else None
        a.nature_dist, a.spread_dist = _conditioned(nat, spr, _orientation(rules, a.moves))
    return out, False


def build_all(conn: sqlite3.Connection, cfg: dict[str, Any] | None = None, priors: Priors | None = None) -> dict[str, Any]:
    cfg = {**sets_config(), **(cfg or {})}
    v = active_version(conn)
    if v is None:
        raise RuntimeError("정적 데이터가 없다. 먼저 `pokechamp update-static`을 실행하세요.")
    priors = priors or Priors(conn)
    rules = load_rules(conn, v["id"])
    samples: dict[str, list[dict]] = defaultdict(list)
    for (tj,) in conn.execute("SELECT team_json FROM team_samples WHERE regulation=?", (v["regulation"],)):
        for s in json.loads(tj):
            samples[to_id(s.get("species", ""))].append(s)
    rng = random.Random(int(cfg["seed"]))
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    latest = priors.freshness().latest_date
    fallback, total = [], 0
    rows = []
    for sid in sorted(rules):
        archs, fb = build_species(conn, v["id"], rules[sid], priors, rng, cfg, samples.get(sid))
        if fb:
            fallback.append(sid)
        for a in archs:
            total += 1
            rows.append((sid, v["regulation"], built_at, json.dumps(list(a.moves)), a.item, a.ability, a.mega_species,
                         json.dumps(a.nature_dist), json.dumps([[s, p] for s, p in a.spread_dist]), a.weight,
                         a.n_support, int(a.rule_generated), latest))
    with conn:
        conn.execute("DELETE FROM set_archetypes WHERE regulation=?", (v["regulation"],))
        conn.executemany(
            "INSERT INTO set_archetypes (species_id, regulation, built_at, moves_json, item_id, ability_id, mega_species_id,"
            " nature_dist_json, spread_dist_json, weight, n_support, rule_generated, source_snapshot_date)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return {"species": len(rules), "archetypes": total, "fallback": fallback, "with_samples": sorted(samples),
            "stats_date": latest}


def load_archetypes(conn: sqlite3.Connection, species: str, regulation: str | None = None) -> list[Archetype]:
    regulation = regulation or active_version(conn)["regulation"]
    out = []
    for r in conn.execute("SELECT * FROM set_archetypes WHERE regulation=? AND species_id=? ORDER BY weight DESC",
                          (regulation, species)):
        out.append(Archetype(r["species_id"], r["item_id"], r["ability_id"], tuple(json.loads(r["moves_json"])), r["weight"],
                             r["n_support"], bool(r["rule_generated"]), r["mega_species_id"],
                             json.loads(r["nature_dist_json"]), [(s, p) for s, p in json.loads(r["spread_dist_json"])]))
    return out


def sample_sets(archs: list[Archetype], n: int, rng: random.Random) -> list[dict[str, Any]]:
    """원형 분포에서 구체적인 세트(PokemonSet 형식)를 n개 뽑는다. 주변 분포를 독립으로 곱하지 않는다."""
    if not archs:
        return []
    weights = [a.weight for a in archs]
    out = []
    for a in rng.choices(archs, weights, k=n):
        nature = rng.choices(list(a.nature_dist), list(a.nature_dist.values()))[0] if a.nature_dist else "serious"
        spread = rng.choices([s for s, _ in a.spread_dist], [p for _, p in a.spread_dist])[0] if a.spread_dist else {}
        out.append({"species": a.species, "item": a.item or "", "ability": a.ability or "", "nature": nature,
                    "evs": dict(spread), "moves": list(a.moves), "mega_species": a.mega_species,
                    "rule_generated": a.rule_generated})
    return out


def check_samples(conn: sqlite3.Connection, n: int = 10_000, seed: int = 7) -> dict[str, Any]:
    """인수 기준(단계 2): 결합 분포에서 세트 n개를 뽑아 규칙 위반이 없는지 검사한다.
    포켓몬은 출현 가중치(순위 기반)로 고르고, 세트는 그 포켓몬의 원형 분포에서 뽑는다."""
    v = active_version(conn)
    rules = load_rules(conn, v["id"])
    weights = Priors(conn).species_weights() or {s: 1.0 for s in rules}
    species = [s for s in weights if s in rules]
    rng = random.Random(seed)
    cache: dict[str, list[Archetype]] = {}
    violations: list[str] = []
    picked = rng.choices(species, [weights[s] for s in species], k=n)
    for sp in picked:
        archs = cache.setdefault(sp, load_archetypes(conn, sp, v["regulation"]))
        st = sample_sets(archs, 1, rng)[0]
        bad = rule_violations(rules[sp], st["item"] or None, st["ability"] or None, tuple(st["moves"]), st["evs"])
        if bad:
            violations.append(f"{sp}: {', '.join(bad)}")
    return {"samples": n, "species": len(set(picked)), "violations": len(violations), "examples": violations[:10]}
