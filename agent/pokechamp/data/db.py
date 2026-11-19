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
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY,
  captured_at TEXT NOT NULL,           -- 우리가 수집한 시각 (UTC ISO)
  source TEXT NOT NULL,
  format TEXT NOT NULL,                -- 'singles'
  regulation TEXT NOT NULL,            -- 데이터 날짜 기준 공식 레귤레이션
  official_season TEXT,                -- 데이터 날짜 기준 공식 시즌 (공식 공지 달력)
  page_season TEXT,                    -- 출처가 붙인 시즌 표기 그대로
  page_updated_at TEXT NOT NULL,       -- 출처 데이터의 날짜 (YYYY-MM-DD)
  requested_season TEXT,
  verified INTEGER NOT NULL,           -- 출처 표기와 공식 시즌이 일치하는가
  note TEXT,
  UNIQUE (source, format, page_updated_at)
);
CREATE TABLE IF NOT EXISTS usage_species (
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  species_id TEXT NOT NULL,
  rank_position INTEGER,
  usage_rate REAL,
  PRIMARY KEY (snapshot_id, species_id)
);
CREATE TABLE IF NOT EXISTS usage_moves (
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  species_id TEXT NOT NULL, move_id TEXT NOT NULL, rank INTEGER, rate REAL,
  PRIMARY KEY (snapshot_id, species_id, move_id)
);
CREATE TABLE IF NOT EXISTS usage_items (
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  species_id TEXT NOT NULL, item_id TEXT NOT NULL, rank INTEGER, rate REAL,
  PRIMARY KEY (snapshot_id, species_id, item_id)
);
CREATE TABLE IF NOT EXISTS usage_abilities (
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  species_id TEXT NOT NULL, ability_id TEXT NOT NULL, rank INTEGER, rate REAL,
  PRIMARY KEY (snapshot_id, species_id, ability_id)
);
CREATE TABLE IF NOT EXISTS usage_natures (
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  species_id TEXT NOT NULL, nature TEXT NOT NULL, rank INTEGER, rate REAL,
  PRIMARY KEY (snapshot_id, species_id, nature)
);
CREATE TABLE IF NOT EXISTS usage_spreads (
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  species_id TEXT NOT NULL, rank INTEGER NOT NULL, spread_json TEXT NOT NULL, rate REAL,
  PRIMARY KEY (snapshot_id, species_id, rank)
);
CREATE TABLE IF NOT EXISTS usage_teammates (
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  species_id TEXT NOT NULL, mate_id TEXT NOT NULL, rank INTEGER, rate REAL,
  PRIMARY KEY (snapshot_id, species_id, mate_id)
);
CREATE TABLE IF NOT EXISTS team_samples (
  id INTEGER PRIMARY KEY,
  snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  source_team_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('tournament', 'replica', 'manual')),
  regulation TEXT NOT NULL,
  team_json TEXT NOT NULL,               -- [{species, item, ability, nature, evs(SP), moves}]
  rank_or_placement INTEGER,
  source_url TEXT,
  created_at TEXT,
  UNIQUE (source, source_team_id)
);
CREATE TABLE IF NOT EXISTS set_archetypes (
  id INTEGER PRIMARY KEY,
  species_id TEXT NOT NULL,              -- 기본 폼 id
  regulation TEXT NOT NULL,
  built_at TEXT NOT NULL,
  moves_json TEXT NOT NULL,              -- 정렬된 기술 id 4개
  item_id TEXT,
  ability_id TEXT,
  mega_species_id TEXT,                  -- 메가스톤을 든 원형이면 메가 폼 id (평가는 메가 폼으로)
  nature_dist_json TEXT NOT NULL,        -- {성격: 확률}
  spread_dist_json TEXT NOT NULL,        -- [[{hp,atk,def,spa,spd,spe}, 확률], ...]
  weight REAL NOT NULL,                  -- 그 포켓몬 안에서의 확률 (합 1)
  n_support INTEGER NOT NULL,            -- 근거가 된 완성 세트 수
  rule_generated INTEGER NOT NULL,       -- 1 = 주변 분포 + 규칙 필터로 만든 원형
  source_snapshot_date TEXT              -- 보정에 쓴 최신 통계 날짜
);
CREATE INDEX IF NOT EXISTS idx_set_archetypes_species ON set_archetypes (regulation, species_id);
CREATE TABLE IF NOT EXISTS matchup_cache (
  static_version INTEGER NOT NULL,       -- 캐시 키에 정적 데이터 버전을 넣어 오래된 값이 섞이지 않게 한다
  key_a TEXT NOT NULL,
  key_b TEXT NOT NULL,
  score REAL NOT NULL,                   -- A 관점 0~1 (빠른 근사)
  detail_json TEXT,
  PRIMARY KEY (static_version, key_a, key_b)
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


def connect(path: Path | None = None, check_same_thread: bool = True) -> sqlite3.Connection:
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def active_version(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM static_versions WHERE active = 1").fetchone()
