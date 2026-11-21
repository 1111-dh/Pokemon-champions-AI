"""내 팀 저장, 빈 항목 채우기, 검증 (PROJECT_PLAN 4.6).

- 규칙 위반은 경고로만 기록하고 저장을 막지 않는다.
- 빈 항목은 사용자가 동의했을 때만 채운다(fill_defaults). 채운 항목은 세트의 filled에 남긴다.
  채울 때는 입력된 도구·기술과 가장 잘 맞는 세트 원형을 고르고, 그 원형과 최신 통계의 최빈값을 쓴다.
- 추천 팀(recommended)과 직접 넣은 팀(manual)은 같은 표, 같은 코드로 다룬다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from pokechamp.data.db import active_version
from pokechamp.data.priors import Priors
from pokechamp.data.static.names import to_id
from pokechamp.input.team_text import ParsedSet
from pokechamp.sets.archetypes import Archetype, load_archetypes, load_rules, move_allowed
from pokechamp.stat_order import ordered, sp_text


def _best_archetype(archs: list[Archetype], ps: ParsedSet) -> Archetype | None:
    if not archs:
        return None
    given_moves = {to_id(m) for m in ps.moves}
    item = to_id(ps.item or "")

    def fit(a: Archetype) -> float:
        s = len(given_moves & set(a.moves)) * 2.0 - len(given_moves - set(a.moves)) * 0.5
        if item:
            s += 3.0 if a.item == item else -3.0
        if ps.ability and a.ability == to_id(ps.ability):
            s += 1.0
        return s + a.weight

    return max(archs, key=fit)


def fill_defaults(conn: sqlite3.Connection, sets: list[ParsedSet], priors: Priors | None = None) -> list[str]:
    """빈 항목을 통계 기본값으로 채운다. 도구는 팀 안에서 겹치지 않게 고른다. 채운 내용을 문장 목록으로 돌려준다."""
    v = active_version(conn)
    priors = priors or Priors(conn)
    rules = load_rules(conn, v["id"])
    names = {t: {r[0]: r[1] for r in conn.execute(f"SELECT id, name_en FROM {t} WHERE version_id=?", (v["id"],))}
             for t in ("items", "abilities", "moves", "natures")}
    used_items = {to_id(s.item) for s in sets if s.item}
    notes = []
    for ps in sets:
        if not ps.missing:
            continue
        arch = _best_archetype(load_archetypes(conn, ps.species_id, v["regulation"]), ps)
        r = rules.get(ps.species_id)
        done = []
        if not ps.item:
            cands = ([arch.item] if arch and arch.item else []) + [k for k, _ in priors.marginal(ps.species_id, "items").values]
            pick = next((c for c in cands if c and c not in used_items), None)
            if pick:
                ps.item = names["items"].get(pick, pick)
                used_items.add(pick)
                done.append(f"도구 {ps.item}")
        if not ps.ability:
            pick = (arch.ability if arch else None) or next((k for k, _ in priors.marginal(ps.species_id, "abilities").values), None)
            if pick:
                ps.ability = names["abilities"].get(pick, pick)
                done.append(f"특성 {ps.ability}")
        if not ps.nature:
            dist = arch.nature_dist if arch and arch.nature_dist else dict(priors.marginal(ps.species_id, "natures").values)
            if dist:
                pick = max(dist, key=dist.get)
                ps.nature = names["natures"].get(pick, pick.capitalize())
                done.append(f"성격 {ps.nature}")
        if not ps.evs:
            spreads = arch.spread_dist if arch and arch.spread_dist else [
                (json.loads(k), p) for k, p in priors.marginal(ps.species_id, "spreads").values]
            if spreads:
                ps.evs = dict(max(spreads, key=lambda x: x[1])[0])
                done.append("배분 " + sp_text(ps.evs))
        if len(ps.moves) < 4:
            have = {to_id(m) for m in ps.moves}
            cands = list(arch.moves if arch else []) + [k for k, _ in priors.marginal(ps.species_id, "moves").values]
            for m in cands:
                if len(ps.moves) >= 4:
                    break
                if m not in have and (r is None or move_allowed(r, to_id(ps.item or ""), m)):
                    ps.moves.append(names["moves"].get(m, m))
                    have.add(m)
                    done.append(f"기술 {names['moves'].get(m, m)}")
        ps.filled = sorted(set(ps.filled) | set(ps.missing))
        ps.compute_missing()
        if done:
            notes.append(f"{ps.species}: " + ", ".join(done))
    return notes


def validate(sets: list[dict[str, Any]], client: Any | None = None) -> tuple[bool | None, list[str]]:
    """엔진 검증기로 검사한다. 엔진 서버가 꺼져 있으면 (None, [이유])."""
    if client is None:
        from pokechamp.engine_client import EngineClient
        client = EngineClient()
    try:
        r = client.validate_team([{k: v for k, v in s.items() if k != "filled"} for s in sets])
    except Exception as e:  # noqa: BLE001
        return None, [f"엔진 서버에 연결하지 못해 검증을 건너뛰었다 ({e})"]
    return bool(r["valid"]), list(r["problems"])


def save_team(conn: sqlite3.Connection, name: str, sets: list[dict[str, Any]], source: str = "manual",
              valid: bool | None = None, problems: list[str] | None = None, replace: bool = False) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    exists = conn.execute("SELECT id FROM my_teams WHERE name=?", (name,)).fetchone()
    if exists and not replace:
        raise ValueError(f"'{name}' 이름의 팀이 이미 있다 (바꾸려면 덮어쓰기 옵션)")
    with conn:
        if exists:
            conn.execute("DELETE FROM my_teams WHERE id=?", (exists[0],))
        return conn.execute(
            "INSERT INTO my_teams (name, created_at, source, team_json, validated_at, valid, problems_json) VALUES (?,?,?,?,?,?,?)",
            (name, now, source, json.dumps(sets, ensure_ascii=False), now if valid is not None else None,
             None if valid is None else int(valid), json.dumps(problems or [], ensure_ascii=False)),
        ).lastrowid


def list_teams(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [{**dict(r), "team": json.loads(r["team_json"]), "problems": json.loads(r["problems_json"] or "[]")}
            for r in conn.execute("SELECT * FROM my_teams ORDER BY created_at DESC")]


def load_team(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    r = conn.execute("SELECT * FROM my_teams WHERE name=?", (name,)).fetchone()
    if r is None:
        raise ValueError(f"'{name}' 팀이 없다")
    return {**dict(r), "team": json.loads(r["team_json"]), "problems": json.loads(r["problems_json"] or "[]")}


def delete_team(conn: sqlite3.Connection, name: str) -> bool:
    with conn:
        return conn.execute("DELETE FROM my_teams WHERE name=?", (name,)).rowcount > 0


def export_text(team: list[dict[str, Any]]) -> str:
    """Showdown 가져오기 형식 (EVs 줄은 Stat Points)."""
    label = {"hp": "HP", "atk": "Atk", "def": "Def", "spa": "SpA", "spd": "SpD", "spe": "Spe"}
    blocks = []
    for s in team:
        lines = [s["species"] + (f" @ {s['item']}" if s.get("item") else "")]
        if s.get("ability"):
            lines.append(f"Ability: {s['ability']}")
        if s.get("evs"):
            lines.append("EVs: " + " / ".join(f"{v} {label[k]}" for k, v in ordered(s["evs"]) if v))
        if s.get("nature"):
            lines.append(f"{s['nature']} Nature")
        lines += [f"- {m}" for m in s.get("moves", [])]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
