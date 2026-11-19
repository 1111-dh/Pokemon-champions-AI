"""SQLite 연결과 스키마 (PROJECT_PLAN 4.4).

정적 데이터 표는 모두 version_id를 가진다. update-static은 새 버전을 쌓고 활성 버전만 바꾸므로
이전 버전으로 되돌릴 수 있다. 통계 스냅샷 표(snapshots, usage_*)는 단계 1의 update-stats에서 추가한다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pokechamp.paths import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS static_versions (
  id INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL,
  regulation TEXT NOT NULL,
  official_notice_id TEXT,
  regulation_starts_at INTEGER,
  regulation_ends_at INTEGER,
  showdown_commit TEXT NOT NULL,
  format TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 0,
  note TEXT
);
CREATE TABLE IF NOT EXISTS species (
  version_id INTEGER NOT NULL REFERENCES static_versions(id),
  id TEXT NOT NULL,
  name_en TEXT NOT NULL,
  name_ko TEXT,
  num INTEGER NOT NULL,
  form_index INTEGER,
  base_species TEXT NOT NULL,
  forme TEXT,
  is_mega INTEGER NOT NULL,
  official_code TEXT,
  types_json TEXT NOT NULL,
  base_stats_json TEXT NOT NULL,
  abilities_json TEXT NOT NULL,
  weightkg REAL,
  PRIMARY KEY (version_id, id)
);
CREATE TABLE IF NOT EXISTS moves (
  version_id INTEGER NOT NULL REFERENCES static_versions(id),
  id TEXT NOT NULL,
  name_en TEXT NOT NULL,
  name_ko TEXT,
  type TEXT NOT NULL,
  category TEXT NOT NULL,
  power INTEGER NOT NULL,
  accuracy INTEGER,
  pp INTEGER NOT NULL,        -- 기본 PP (Showdown 값, 20 상한 적용됨)
  max_pp INTEGER NOT NULL,    -- 챔피언스 배틀 최대 PP = (pp/5 + 1) * 4 (docs/VERIFIED.md 6번)
  priority INTEGER NOT NULL,
  target TEXT,
  flags_json TEXT,
  description TEXT,
  PRIMARY KEY (version_id, id)
);
CREATE TABLE IF NOT EXISTS items (
  version_id INTEGER NOT NULL REFERENCES static_versions(id),
  id TEXT NOT NULL,
  name_en TEXT NOT NULL,
  name_ko TEXT,
  is_mega_stone INTEGER NOT NULL,
  is_berry INTEGER NOT NULL,
  description TEXT,
  PRIMARY KEY (version_id, id)
);
CREATE TABLE IF NOT EXISTS abilities (
  version_id INTEGER NOT NULL REFERENCES static_versions(id),
  id TEXT NOT NULL,
  name_en TEXT NOT NULL,
  name_ko TEXT,
  description TEXT,
  PRIMARY KEY (version_id, id)
);
CREATE TABLE IF NOT EXISTS natures (
  version_id INTEGER NOT NULL REFERENCES static_versions(id),
  id TEXT NOT NULL,
  name_en TEXT NOT NULL,
  name_ko TEXT,
  PRIMARY KEY (version_id, id)
);
CREATE TABLE IF NOT EXISTS learnsets (
  version_id INTEGER NOT NULL REFERENCES static_versions(id),
  species_id TEXT NOT NULL,
  move_id TEXT NOT NULL,
  PRIMARY KEY (version_id, species_id, move_id)
);
CREATE TABLE IF NOT EXISTS mega_forms (
  version_id INTEGER NOT NULL REFERENCES static_versions(id),
  base_species_id TEXT NOT NULL,
  mega_species_id TEXT NOT NULL,
  stone_item_id TEXT NOT NULL,
  PRIMARY KEY (version_id, mega_species_id)
);
CREATE TABLE IF NOT EXISTS my_teams (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('recommended', 'manual')),
  team_json TEXT NOT NULL,
  validated_at TEXT,
  valid INTEGER,
  problems_json TEXT
);
CREATE TABLE IF NOT EXISTS update_log (
  id INTEGER PRIMARY KEY,
  ran_at TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('static', 'stats')),
  source TEXT,
  status TEXT NOT NULL,
  duration_s REAL,
  note TEXT
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def active_version(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM static_versions WHERE active = 1").fetchone()
