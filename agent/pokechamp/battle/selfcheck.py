"""입력 파서와 상태 추적의 자동 검증 (단계 5).

엔진에서 무작위 배틀을 진행하면서 매 결정 시점마다:
1. 새 로그 → 사건(LogConverter) → 사용자가 칠 법한 한국어 문장(render) → 파서(parse) 왕복이 같은 사건을 돌려주는가.
2. 파싱한 사건을 적용한 추적 상태가 엔진의 실제 상태(직렬화 상태)와 맞는가:
   출전 포켓몬, HP(내 쪽 정확한 값, 상대 % ±1), 상태이상, 기절, 출전 포켓몬의 랭크, 날씨·필드 종류와 남은 턴.
mode="sparse"는 실제 사용자처럼 HP를 턴의 마지막 값만 넣고 도구·특성 발동, 급소 등은 빼고 입력한다
(기술·교체·메가·기절·상태이상·랭크·날씨·필드와 HP 마지막 값만).
"""

from __future__ import annotations

import random
import re
from collections import Counter
from typing import Any

from pokechamp.battle.events import Event, LogConverter, SpeciesResolver
from pokechamp.battle.state import BattleState
from pokechamp.data.db import active_version
from pokechamp.data.static.names import to_id
from pokechamp.engine_client import EngineClient
from pokechamp.input.battle_text import BattleText

ENGINE_WEATHER = {"sunnyday": "sun", "raindance": "rain", "sandstorm": "sand", "snowscape": "snow", "snow": "snow",
                  "desolateland": "sun", "primordialsea": "rain", "": "none"}
ENGINE_TERRAIN = {"electricterrain": "electric", "grassyterrain": "grassy", "psychicterrain": "psychic",
                  "mistyterrain": "misty", "": "none"}
SPARSE_KINDS = {"move", "switch", "mega", "faint", "status", "cure", "boost", "clearboost", "setboost", "transform",
                "weather", "terrain",
                "hp"}


def base_mapper(conn) -> tuple[dict[str, str], Any]:
    res = SpeciesResolver(conn)
    return res.mega, res


def sparse(events: list[Event]) -> list[Event]:
    """사용자가 실제로 넣을 법한 최소 입력: HP는 포켓몬마다 마지막 값만, 발동·급소 등은 뺀다."""
    last_hp: dict[tuple, int] = {}
    keep = [e for e in events if e.kind in SPARSE_KINDS]
    for i, e in enumerate(keep):
        if e.kind == "hp":
            last_hp[(e.side, e.species)] = i
    return [e for i, e in enumerate(keep) if e.kind != "hp" or last_hp.get((e.side, e.species)) == i]


def _engine_species(p: dict) -> str:
    key = "baseSpecies" if p.get("transformed") else "species"     # 변신(메타몽)은 원래 종으로
    m = re.search(r"\[Species:(\w+)\]", str(p.get(key, "")))
    return m.group(1) if m else to_id(str(p.get(key, "")))


def compare(state: BattleState, engine: dict, base, me: str = "p1", check_clock: bool = True) -> list[str]:
    """추적 상태와 엔진 직렬화 상태의 차이."""
    diffs: list[str] = []
    sides = {s["id"]: s for s in engine["sides"]}
    for sid, tracked in ((me, state.me), ("p2" if me == "p1" else "p1", state.opp)):
        side = sides[sid]
        label = "me" if tracked is state.me else "opp"
        for p in side["pokemon"]:
            sp = base(_engine_species(p))
            m = tracked.mons.get(sp)
            if p.get("isActive") and not p.get("fainted"):
                if tracked.active != sp:
                    diffs.append(f"{label}.active: 추적 {tracked.active} / 엔진 {sp}")
            if m is None or not m.seen:
                continue
            hp, mx = p["hp"], p["maxhp"]
            if label == "me":
                if m.hp_abs is not None and m.hp_abs != hp:
                    diffs.append(f"{label}.{sp}.hp: 추적 {m.hp_abs} / 엔진 {hp}")
            elif abs(m.hp - 100 * hp / mx) > 1.01:
                diffs.append(f"{label}.{sp}.hp%: 추적 {m.hp} / 엔진 {100 * hp / mx:.1f}")
            if bool(m.fainted) != bool(p.get("fainted") or hp == 0):
                diffs.append(f"{label}.{sp}.fainted: 추적 {m.fainted} / 엔진 {p.get('fainted')}")
            st = p.get("status") or ""
            if not m.fainted and (m.status or "") != st:
                diffs.append(f"{label}.{sp}.status: 추적 '{m.status}' / 엔진 '{st}'")
            if p.get("isActive") and not p.get("fainted"):
                eb = {k: v for k, v in p["boosts"].items() if v}
                tb = {k: v for k, v in m.boosts.items() if v}
                if eb != tb:
                    diffs.append(f"{label}.{sp}.boosts: 추적 {tb} / 엔진 {eb}")
    f = engine["field"]
    for key, clock, table in (("weather", state.weather, ENGINE_WEATHER), ("terrain", state.terrain, ENGINE_TERRAIN)):
        kind = table.get(to_id(f.get(key) or ""), to_id(f.get(key) or ""))
        if kind != clock.kind:
            diffs.append(f"{key}: 추적 {clock.kind} / 엔진 {kind}")
        elif kind != "none" and check_clock:
            # 턴 시작 시점에만 비교한다 (턴 끝 기절 교체를 기다릴 때는 그 턴의 차감이 이미 끝나 1 차이가 난다)
            dur = (f.get(f"{key}State") or {}).get("duration")
            rem = clock.remaining(engine["turn"])
            if dur is not None and rem is not None and not (rem[0] <= dur <= rem[1]):
                diffs.append(f"{key}.remaining: 추적 {rem} / 엔진 {dur} (턴 {engine['turn']})")
    return diffs


def run(conn, client: EngineClient, n_battles: int, seed: str, mode: str = "full", max_steps: int = 200) -> dict[str, Any]:
    bt = BattleText(conn)
    base, base_of = base_mapper(conn)
    rng = random.Random(f"{seed}-selfcheck-{mode}")
    stats: Counter = Counter()
    examples: dict[str, list[str]] = {}

    def note(kind: str, msg: str) -> None:
        stats[kind] += 1
        if len(examples.setdefault(kind, [])) < 5:
            examples[kind].append(msg)

    for b in range(n_battles):
        teams = client.batch("/random_team", [{"seed": f"{seed}-{b}-a"}, {"seed": f"{seed}-{b}-b"}])
        mine, opp = teams[0]["result"]["team"], teams[1]["result"]["team"]
        picks = {s: "team " + "".join(str(i + 1) for i in rng.sample(range(6), 3)) for s in ("p1", "p2")}
        r = client.new_battle(mine, opp, seed=f"{seed}-{b}", picks=picks)
        state = BattleState(mine, [base_of(s["species"]) for s in opp], base_of)
        # 일루전(조로아크)이 있는 판은 겉보기와 실제가 달라 따로 센다 (KNOWN_GAPS)
        illusion = any(to_id(s.get("ability") or "") == "illusion" for s in mine + opp)
        stats["illusion_battles"] += int(illusion)
        pre = "illusion_" if illusion else ""
        conv = LogConverter(base_of)
        st = r["state"]
        lines = r["log"]
        for step in range(max_steps):
            events = conv.feed(lines)
            body = [e for e in events if e.kind != "turn"]
            given = sparse(body) if mode == "sparse" else body
            text = bt.render(given)
            parsed = bt.parse(text) if text else None
            stats["steps"] += 1
            stats["events"] += len(given)
            if parsed and parsed.errors:
                note("parse_error", f"{parsed.errors[0]}  ← {text[:200]}")
            elif parsed:
                fam = lambda e: e.key() if not e.species else tuple(  # noqa: E731
                    (k, base_of.family(v) if k == "species" else v) for k, v in e.key())
                if [fam(e) for e in parsed.events] != [fam(e) for e in given]:
                    bad = next(i for i, (x, y) in enumerate(zip(parsed.events, given)) if fam(x) != fam(y)) \
                        if len(parsed.events) == len(given) else -1
                    detail = (f"원래 {given[bad].to_dict()} / 파싱 {parsed.events[bad].to_dict()}" if bad >= 0
                              else f"개수 {len(given)} → {len(parsed.events)}")
                    note("roundtrip", f"{text[:120]} | {detail}")
            # 턴 경계를 같은 순서로 끼워 적용한다 (턴 표시는 입력이 아니라 세션이 넣는다)
            seq: list[Event] = []
            pe = iter(parsed.events if parsed and not parsed.errors else given)
            given_ids = {id(e) for e in given}
            for e in events:
                if e.kind == "turn":
                    seq.append(e)
                elif id(e) in given_ids:
                    seq.append(next(pe))
            warns = state.apply(seq)
            for w in warns:
                note(pre + "warning", f"배틀 {b} 턴 {st['turn']}: {w}")
            at_turn_start = bool(events) and events[-1].kind == "turn"
            for d in compare(state, st, base_of, check_clock=at_turn_start):
                note(pre + "state_" + d.split(":")[0].split(".")[-1], f"배틀 {b} 턴 {st['turn']}: {d}")
            if r.get("ended"):
                stats["ended"] += 1
                break
            la = client.legal_actions(st)
            c1 = rng.choice(la["p1"])["choice"] if la["p1"] else None
            c2 = rng.choice(la["p2"])["choice"] if la["p2"] else None
            r = client.step(st, p1=c1, p2=c2)
            st, lines = r["state"], r["log"]
        stats["battles"] += 1
    return {"mode": mode, "stats": dict(stats), "examples": examples}
