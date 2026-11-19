"""정적 데이터와 통계 스냅샷 상태 (CLI status와 웹 화면이 함께 쓴다)."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from pokechamp.data.db import active_version


def status_data(conn: sqlite3.Connection) -> dict[str, Any]:
    v = active_version(conn)
    static = None
    if v is not None:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE version_id=?", (v["id"],)).fetchone()[0]
                  for t in ("species", "moves", "items", "abilities", "mega_forms")}
        static = {
            "version": v["id"], "regulation": v["regulation"], "official_notice_id": v["official_notice_id"],
            "showdown_commit": v["showdown_commit"], "applied_at": v["applied_at"],
            "regulation_starts_at": v["regulation_starts_at"], "regulation_ends_at": v["regulation_ends_at"],
            "regulation_ended": bool(v["regulation_ends_at"] and time.time() > v["regulation_ends_at"]),
            "counts": counts,
        }
    log = [dict(r) for r in conn.execute("SELECT * FROM update_log ORDER BY id DESC LIMIT 10")]
    return {"static": static, "stats": None, "update_log": log}
