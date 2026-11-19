"""웹 화면 백엔드 단위 테스트 (엔진 없이)."""

import json
import random
import sqlite3

from pokechamp.data.db import connect
from pokechamp.web.battle import LogReader, Names, Session, sprite_id
from pokechamp.web.server import parse_verified


def _names(tmp_path):
    conn = connect(tmp_path / "t.sqlite")
    conn.execute("INSERT INTO static_versions (applied_at, regulation, showdown_commit, format, active) VALUES ('x','M-C','c','f',1)")
    conn.execute("INSERT INTO species VALUES (1,'garchomp','Garchomp','한카리아스',445,0,'Garchomp','',0,'0445-000','[\"Dragon\",\"Ground\"]','{}','{}',95)")
    conn.execute("INSERT INTO species VALUES (1,'florges','Florges','플라제스',671,0,'Florges','',0,'0671-000','[\"Fairy\"]','{}','{}',10)")
    conn.execute("INSERT INTO species VALUES (1,'mausholdfour','Maushold-Four','파밀리쥐',925,1,'Maushold','Four',0,'0925-001','[\"Normal\"]','{}','{}',2)")
    conn.execute("INSERT INTO moves VALUES (1,'earthquake','Earthquake','지진','Ground','Physical',100,100,10,12,0,'allAdjacent','[]','')")
    conn.execute("INSERT INTO items VALUES (1,'leftovers','Leftovers','먹다남은음식',0,0,'')")
    conn.commit()
    return Names(conn)


def test_names_fallback_for_cosmetic_forms(tmp_path):
    n = _names(tmp_path)
    assert n.get("species", "Garchomp") == "한카리아스"
    assert n.get("species", "Florges-Blue") == "플라제스"   # 외형 폼은 기본 종 이름
    assert n.get("species", "Maushold") == "파밀리쥐"       # 허용 목록에 다른 폼만 있는 종
    assert n.get("moves", "Earthquake") == "지진"
    assert n.get("moves", "Unknown Move") == "Unknown Move"
    assert n.types("Florges-Blue") == ["Fairy"]


def test_log_reader_player_view(tmp_path):
    n = _names(tmp_path)
    s = Session(id="x", seed="s", state=None, my_team=[], rng=random.Random(0))
    LogReader(s, n).feed([
        "|poke|p2|Garchomp, L50, M|",
        "|switch|p2a: Garchomp|Garchomp, L50, M|100/100",
        "|turn|1",
        "|move|p2a: Garchomp|Earthquake|p1a: Florges",
        "|split|p1",
        "|-damage|p1a: Florges|93/141",     # 내 쪽: 정확한 값
        "|-damage|p1a: Florges|65/100",
        "|split|p2",
        "|-heal|p2a: Garchomp|150/183|[from] item: Leftovers",  # 상대 비공개 값은 쓰지 않는다
        "|-heal|p2a: Garchomp|81/100|[from] item: Leftovers",
        "|win|p1",
    ])
    texts = [e["text"] for e in s.events]
    assert "상대 한카리아스의 지진!" in texts
    assert "플라제스 HP → 93/141" in texts
    assert "상대 한카리아스 회복 → 81% (먹다남은음식)" in texts
    o = s.opp["Garchomp"]
    assert o.revealed and o.hp_pct == 81 and o.moves == ["Earthquake"]
    assert s.ended and s.winner == "p1"


def test_sprite_id_and_verified_parse():
    assert sprite_id("Garchomp-Mega-Z") == "garchomp-megaz"
    assert sprite_id("Tauros-Paldea-Combat") == "tauros-paldeacombat"
    rows = parse_verified("| # | 항목 | 결과 | 상태 |\n|---|---|---|---|\n| 5 | 상성 | **4배** | ✅ 게임 확인 |\n")
    assert rows == [{"no": 5, "item": "상성", "result": "**4배**", "state": "✅ 게임 확인"}]
