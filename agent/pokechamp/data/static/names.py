"""다국어 이름 매핑. 기술, 도구, 특성, 성격, 타입의 한국어 이름은 PokeAPI CSV에서 가져온다.

포켓몬 이름은 공식 참가 가능 목록의 한국어 이름을 쓴다(build.py). PokeAPI에 없는 이름은 별칭 테이블
(data/aliases.csv)로 보충하고, 그래도 없으면 비워 두고 보고한다.
"""

from __future__ import annotations

import csv
import io
import re

from pokechamp.data.http import fetch
from pokechamp.paths import RAW_DIR

POKEAPI_CSV = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/{name}.csv"
CACHE = RAW_DIR / "pokeapi"
CACHE_AGE_S = 7 * 24 * 3600
LANG_KO, LANG_EN = "3", "9"

# (식별자 CSV, 이름 CSV, 이름 CSV의 id 열)
TABLES = {
    "move": ("moves", "move_names", "move_id"),
    "item": ("items", "item_names", "item_id"),
    "ability": ("abilities", "ability_names", "ability_id"),
    "nature": ("natures", "nature_names", "nature_id"),
    "type": ("types", "type_names", "type_id"),
}


def to_id(name: str) -> str:
    """Showdown 방식 id: 소문자 영숫자만."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _csv(name: str, force: bool) -> list[dict[str, str]]:
    raw = fetch(POKEAPI_CSV.format(name=name), CACHE / f"{name}.csv", CACHE_AGE_S, force)
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))


def load_names(force: bool = False) -> dict[str, dict[str, str]]:
    """{종류: {showdown_id: 한국어 이름}}"""
    out: dict[str, dict[str, str]] = {}
    for kind, (ident_csv, names_csv, idcol) in TABLES.items():
        ident = {r["id"]: r["identifier"] for r in _csv(ident_csv, force)}
        ko: dict[str, str] = {}
        en: dict[str, str] = {}
        for r in _csv(names_csv, force):
            if r["local_language_id"] == LANG_KO:
                ko[r[idcol]] = r["name"]
            elif r["local_language_id"] == LANG_EN:
                en[r[idcol]] = r["name"]
        out[kind] = {to_id(en.get(i, ident[i])): ko[i] for i in ident if i in ko}
    return out


def mega_name_ko(base_ko: str, forme: str) -> str:
    """메가 폼 한국어 이름. 예: ("한카리아스", "Mega-Z") → "메가한카리아스Z",
    ("냐오닉스 (수컷의 모습)", "M-Mega") → "메가냐오닉스 (수컷의 모습)"."""
    suffix = forme.split("Mega", 1)[1].lstrip("-") if "Mega" in forme else ""
    m = re.match(r"^(.*?)(\s*\(.*\))?$", base_ko)
    stem, paren = m.group(1), m.group(2) or ""
    return f"메가{stem}{suffix}{paren}"
