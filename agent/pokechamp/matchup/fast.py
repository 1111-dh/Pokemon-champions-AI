"""1:1 상성 빠른 근사와 캐시 (PROJECT_PLAN 6절).

점수는 엔진 /matchup_fast가 계산한다 (서로 최고 기대 데미지 기술의 n타와 선공 여부, 등장 시 날씨·필드 반영, 대칭).
캐시 규칙:
- 캐시 키에 정적 데이터 버전을 넣는다. update-static은 캐시 전체를 지운다(data/static/update.py).
- update-stats로 세트 후보가 바뀌면 새 세트의 키만 새로 계산되고 기존 값은 그대로 쓴다.
- 사용자가 직접 넣은 세트도 같은 방식으로 필요할 때 계산해 캐시에 넣는다(사전 계산에 의존하지 않음).
- 메가스톤을 든 세트는 메가 전과 후를 별도 키로 둔다.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from pokechamp.data.db import active_version
from pokechamp.data.static.names import to_id
from pokechamp.engine_client import EngineClient

BATCH = 400


def canonical(set_: dict[str, Any], mega: bool = False) -> dict[str, Any]:
    evs = {k: int(v) for k, v in (set_.get("evs") or {}).items() if int(v)}
    return {"species": to_id(set_.get("species", "")), "item": to_id(set_.get("item") or ""),
            "ability": to_id(set_.get("ability") or ""), "nature": to_id(set_.get("nature") or ""),
            "evs": dict(sorted(evs.items())), "moves": sorted(to_id(m) for m in set_.get("moves", [])), "mega": bool(mega)}


def set_key(set_: dict[str, Any], mega: bool = False) -> str:
    return hashlib.sha1(json.dumps(canonical(set_, mega), sort_keys=True).encode()).hexdigest()[:20]


class MatchupCache:
    def __init__(self, conn: sqlite3.Connection, client: EngineClient | None = None):
        self.conn = conn
        self.client = client or EngineClient()
        v = active_version(conn)
        self.version = v["id"] if v else 0
        self.computed = 0      # 이번에 엔진으로 새로 계산한 쌍 수

    def scores(self, pairs: list[tuple[tuple[dict, bool], tuple[dict, bool]]]) -> list[float]:
        """[((세트A, 메가A), (세트B, 메가B)), ...] → A 관점 점수 목록. 없는 쌍은 엔진 배치로 계산해 캐시에 넣는다."""
        keys = [(set_key(a, ma), set_key(b, mb)) for (a, ma), (b, mb) in pairs]
        have: dict[tuple[str, str], float] = {}
        uniq = list({k for k in keys})
        for i in range(0, len(uniq), 500):
            chunk = uniq[i:i + 500]
            q = " OR ".join("(key_a=? AND key_b=?)" for _ in chunk)
            args = [x for k in chunk for x in k]
            for r in self.conn.execute(f"SELECT key_a, key_b, score FROM matchup_cache WHERE static_version=? AND ({q})",
                                       [self.version, *args]):
                have[(r[0], r[1])] = r[2]
        missing = []
        seen = set()
        for k, ((a, ma), (b, mb)) in zip(keys, pairs):
            if k not in have and k not in seen:
                seen.add(k)
                missing.append((k, a, ma, b, mb))
        for i in range(0, len(missing), BATCH):
            chunk = missing[i:i + BATCH]
            res = self.client.batch("/matchup_fast", [{"a": {"set": a, "mega": ma}, "b": {"set": b, "mega": mb}}
                                                      for _, a, ma, b, mb in chunk])
            rows = []
            for (k, *_), r in zip(chunk, res):
                score = r["result"]["score"] if r.get("ok") else 0.5
                detail = json.dumps(r.get("result") or {"error": r.get("error")}, ensure_ascii=False)
                have[k] = score
                rows.append((self.version, k[0], k[1], score, detail))
                rows.append((self.version, k[1], k[0], round(1 - score, 4), None))   # 대칭: 반대 방향도 저장
                have[(k[1], k[0])] = round(1 - score, 4)
            with self.conn:
                self.conn.executemany("INSERT OR REPLACE INTO matchup_cache VALUES (?,?,?,?,?)", rows)
            self.computed += len(chunk)
        return [have[k] for k in keys]

    def score(self, a: dict, b: dict, mega_a: bool = False, mega_b: bool = False) -> float:
        return self.scores([((a, mega_a), (b, mega_b))])[0]

    def detail(self, a: dict, b: dict, mega_a: bool = False, mega_b: bool = False) -> dict | None:
        self.score(a, b, mega_a, mega_b)
        r = self.conn.execute("SELECT detail_json FROM matchup_cache WHERE static_version=? AND key_a=? AND key_b=?",
                              (self.version, set_key(a, mega_a), set_key(b, mega_b))).fetchone()
        return json.loads(r[0]) if r and r[0] else None
