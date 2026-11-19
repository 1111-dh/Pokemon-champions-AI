"""프로젝트 경로 모음."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE_DIR = ROOT / "engine"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "db" / "pokechamp.sqlite"
ALIASES_PATH = DATA_DIR / "aliases.csv"
