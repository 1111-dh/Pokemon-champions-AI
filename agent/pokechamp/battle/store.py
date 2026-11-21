"""배틀 기록 저장 (my_battles, PROJECT_PLAN 4.4·10절).

opp_team_json: 상대 6마리마다 {species, brought(배틀에 나옴), moves, item, ability, mega} — 관찰된 것만 (로컬 메타용).
log_json: 세션 기록(선출, 선봉, 턴별 입력 문장과 추천, 경고).
source: manual(사람이 입력한 실제 배틀) | sim(검증·테스트용 자동 배틀). 로컬 메타와 상대 모델에는 manual만 쓴다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from pokechamp.data.db import active_version


def observed_opponents(session) -> list[dict[str, Any]]:
    st = session.state
    out = []
    for sp in st.opp.order:
        m = st.opp.mons[sp]
        out.append({"species": sp, "brought": m.seen, "moves": list(m.moves), "item": m.item or None,
                    "ability": m.ability or None, "mega": m.mega})
    return out


def save_battle(conn: sqlite3.Connection, record: dict[str, Any], session, source: str = "manual") -> int:
    v = active_version(conn)
    with conn:
        return conn.execute(
            "INSERT INTO my_battles (played_at, regulation, source, my_team_json, opp_team_json, result, log_json) VALUES (?,?,?,?,?,?,?)",
            (record.get("started_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"), v["regulation"] if v else None,
             source, json.dumps(record["my_team"], ensure_ascii=False), json.dumps(observed_opponents(session), ensure_ascii=False),
             record.get("result"), json.dumps(record, ensure_ascii=False, default=str))).lastrowid


def list_battles(conn: sqlite3.Connection, source: str | None = None) -> list[dict[str, Any]]:
    q = "SELECT id, played_at, regulation, source, result, opp_team_json FROM my_battles"
    args: list[Any] = []
    if source:
        q += " WHERE source=?"
        args.append(source)
    rows = conn.execute(q + " ORDER BY id DESC", args).fetchall()
    return [{**dict(r), "opp": [o["species"] for o in json.loads(r["opp_team_json"])]} for r in rows]


def load_battle(conn: sqlite3.Connection, battle_id: int | str) -> dict[str, Any]:
    if battle_id == "latest":
        r = conn.execute("SELECT * FROM my_battles ORDER BY id DESC LIMIT 1").fetchone()
    else:
        r = conn.execute("SELECT * FROM my_battles WHERE id=?", (int(battle_id),)).fetchone()
    if r is None:
        raise ValueError(f"배틀 기록이 없다: {battle_id}")
    return {**dict(r), "my_team": json.loads(r["my_team_json"]), "opp": json.loads(r["opp_team_json"]),
            "log": json.loads(r["log_json"])}


def delete_battle(conn: sqlite3.Connection, battle_id: int) -> bool:
    with conn:
        return conn.execute("DELETE FROM my_battles WHERE id=?", (int(battle_id),)).rowcount > 0
