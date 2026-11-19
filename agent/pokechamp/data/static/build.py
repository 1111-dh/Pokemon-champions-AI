"""공식 허용 목록 + Showdown 정적 데이터 + 한국어 이름 → 정적 데이터 묶음(StaticBundle).

허용 목록 밖의 포켓몬은 담지 않는다(PROJECT_PLAN 4.1). 메가 폼은 허용된 기본 폼의 메가만 담는다.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from typing import Any

from pokechamp.data.static.names import mega_name_ko, to_id
from pokechamp.data.static.official_list import OfficialList
from pokechamp.paths import ALIASES_PATH


@dataclass
class StaticBundle:
    regulation: str
    official_notice_id: str
    regulation_starts_at: int
    regulation_ends_at: int
    showdown_commit: str
    format: str
    species: list[dict[str, Any]]
    moves: list[dict[str, Any]]
    items: list[dict[str, Any]]
    abilities: list[dict[str, Any]]
    natures: list[dict[str, Any]]
    learnsets: dict[str, list[str]]
    mega_forms: list[dict[str, str]]
    warnings: list[str] = field(default_factory=list)


def load_aliases() -> dict[str, dict[str, str]]:
    """data/aliases.csv의 kind=name_ko 행: PokeAPI에 없는 한국어 이름 보충. {종류: {id: 이름}}"""
    out: dict[str, dict[str, str]] = {}
    if not ALIASES_PATH.exists():
        return out
    with ALIASES_PATH.open(encoding="utf-8") as f:
        for r in csv.DictReader(row for row in f if not row.startswith("#")):
            if r.get("purpose") == "name_ko":
                out.setdefault(r["kind"], {})[r["target_id"]] = r["alias"]
    return out


NATURES = [
    "Hardy", "Lonely", "Brave", "Adamant", "Naughty", "Bold", "Docile", "Relaxed", "Impish", "Lax",
    "Timid", "Hasty", "Serious", "Jolly", "Naive", "Modest", "Mild", "Quiet", "Bashful", "Rash",
    "Calm", "Gentle", "Sassy", "Careful", "Quirky",
]


def build_bundle(official: OfficialList, sd: dict[str, Any], names_ko: dict[str, dict[str, str]]) -> StaticBundle:
    warnings: list[str] = []
    aliases = load_aliases()

    def ko(kind: str, id_: str, name: str) -> str | None:
        v = aliases.get(kind, {}).get(id_) or names_ko.get(kind, {}).get(id_)
        if not v:
            warnings.append(f"한국어 이름 없음: {kind} {name}")
        return v

    by_form = {(s["num"], s["form_index"]): s for s in sd["species"] if not s["is_mega"]}
    allowed: list[dict[str, Any]] = []
    for e in official.entries:
        s = by_form.get((e.num, e.form_index))
        if s is None:
            raise ValueError(f"공식 목록 {e.code} {e.name_en}에 대응하는 Showdown 종이 없다 (엔진이 지원하지 않거나 폼 순서가 다름)")
        allowed.append({**s, "name_en": s["name"], "name_ko": e.name_ko, "official_code": e.code})
    allowed_ids = {s["id"] for s in allowed}
    ko_by_id = {s["id"]: s["name_ko"] for s in allowed}

    sd_species = {s["id"]: s for s in sd["species"]}
    mega_forms = [m for m in sd["mega_forms"] if m["base_species"] in allowed_ids]
    megas = []
    for m in mega_forms:
        s = sd_species[m["mega_species"]]
        megas.append({**s, "name_en": s["name"], "name_ko": mega_name_ko(ko_by_id[m["base_species"]], s["forme"]),
                      "official_code": None})
    species = allowed + megas

    learnsets = {sid: sd["learnsets"].get(sid, []) for sid in sorted(allowed_ids)}
    for sid, ls in learnsets.items():
        if not ls:
            warnings.append(f"배울 수 있는 기술이 없음: {sid}")
    used_moves = {m for ls in learnsets.values() for m in ls}
    moves = [{**m, "name_en": m["name"], "name_ko": ko("move", m["id"], m["name"])}
             for m in sd["moves"] if m["id"] in used_moves]

    used_abilities = {to_id(a) for s in species for a in s["abilities"].values()}
    abilities = [{**a, "name_en": a["name"], "name_ko": ko("ability", a["id"], a["name"])}
                 for a in sd["abilities"] if a["id"] in used_abilities]

    mega_stones = {m["stone_item"] for m in mega_forms}
    items = []
    for it in sd["items"]:
        if it["mega_stone"] and it["id"] not in mega_stones:
            continue  # 허용되지 않은 포켓몬의 메가스톤은 담지 않는다
        items.append({**it, "name_en": it["name"], "name_ko": ko("item", it["id"], it["name"])})

    natures = [{"id": to_id(n), "name_en": n, "name_ko": ko("nature", to_id(n), n)} for n in NATURES]

    return StaticBundle(
        regulation=official.regulation,
        official_notice_id=official.notice_id,
        regulation_starts_at=official.starts_at,
        regulation_ends_at=official.ends_at,
        showdown_commit=sd["showdown_commit"],
        format=sd["format"],
        species=species,
        moves=moves,
        items=items,
        abilities=abilities,
        natures=natures,
        learnsets=learnsets,
        mega_forms=mega_forms,
        warnings=warnings,
    )
