"""팀 텍스트 해석 (PROJECT_PLAN 4.6). Showdown 가져오기 형식을 영어와 한국어 이름 모두로 받는다.

    한카리아스 @ 구애스카프            Garchomp @ Choice Scarf
    특성: 까칠한피부                   Ability: Rough Skin
    성격: 명랑  (또는 "명랑 성격")      Jolly Nature
    SP: 2 HP / 32 공격 / 32 스피드     EVs: 2 HP / 32 Atk / 32 Spe
    - 지진                             - Earthquake

빈 항목(도구, 특성, 성격, 배분, 기술 4개 미만)은 그대로 비워 두고 missing에 적는다. 조용히 채우지 않는다.
이름을 못 찾으면 비슷한 이름을 제안하는 오류를 낸다. 별칭은 data/aliases.csv(purpose=alias)에서 읽는다.
"""

from __future__ import annotations

import csv
import difflib
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from pokechamp.data.db import active_version
from pokechamp.data.static.names import to_id
from pokechamp.paths import ALIASES_PATH

STAT_LABELS = {
    "hp": "hp", "체력": "hp", "h": "hp",
    "atk": "atk", "attack": "atk", "공격": "atk", "a": "atk",
    "def": "def", "defense": "def", "방어": "def", "b": "def",
    "spa": "spa", "spatk": "spa", "spattack": "spa", "특공": "spa", "특수공격": "spa", "c": "spa",
    "spd": "spd", "spdef": "spd", "spdefense": "spd", "특방": "spd", "특수방어": "spd", "d": "spd",
    "spe": "spe", "speed": "spe", "스피드": "spe", "스핏": "spe", "s": "spe",
}
FIELDS = ("item", "ability", "nature", "evs", "moves")
FIELD_KO = {"item": "도구", "ability": "특성", "nature": "성격", "evs": "Stat Points 배분", "moves": "기술"}


class TeamParseError(ValueError):
    pass


@dataclass
class ParsedSet:
    species: str                       # Showdown 영어 이름
    species_id: str
    item: str | None = None
    ability: str | None = None
    nature: str | None = None
    evs: dict[str, int] | None = None
    moves: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    filled: list[str] = field(default_factory=list)   # 사용자가 동의해 통계 기본값으로 채운 항목

    def to_set(self) -> dict[str, Any]:
        d: dict[str, Any] = {"species": self.species, "item": self.item or "", "ability": self.ability or "",
                             "nature": self.nature or "", "evs": self.evs or {}, "moves": list(self.moves)}
        if self.filled:
            d["filled"] = list(self.filled)
        return d

    def compute_missing(self) -> None:
        self.missing = [f for f in FIELDS if (f == "moves" and len(self.moves) < 4) or (f != "moves" and not getattr(self, f))]


class NameIndex:
    """한국어·영어·id·별칭 → (id, 영어 이름). 표 종류별."""

    def __init__(self, conn: sqlite3.Connection):
        v = active_version(conn)
        vid = v["id"] if v else -1
        self.maps: dict[str, dict[str, tuple[str, str]]] = {}
        self.ko: dict[str, dict[str, str]] = {}
        for table in ("species", "items", "abilities", "moves", "natures"):
            m: dict[str, tuple[str, str]] = {}
            self.ko[table] = {}
            extra = " AND is_mega=0" if table == "species" else ""
            for r in conn.execute(f"SELECT id, name_en, name_ko FROM {table} WHERE version_id=?{extra}", (vid,)):
                if r["name_ko"]:
                    self.ko[table][r["id"]] = r["name_ko"]
                for key in (r["id"], to_id(r["name_en"]), self.norm(r["name_ko"] or "")):
                    if key:
                        m.setdefault(key, (r["id"], r["name_en"]))
                if table == "species" and (fm := re.match(r"^(.+?)\s*\((.+)\)$", r["name_ko"] or "")):
                    # "로토무 (워시로토무)" → "워시로토무", "나인테일 (알로라의 모습)" → "알로라나인테일", "나인테일알로라"
                    base, form = fm.group(1), re.sub(r"(의)?\s*모습$", "", fm.group(2))
                    for key in (self.norm(form) if base in form else "", self.norm(form + base), self.norm(base + form)):
                        if key:
                            m.setdefault(key, (r["id"], r["name_en"]))
            self.maps[table] = m
        if ALIASES_PATH.exists():
            with ALIASES_PATH.open(encoding="utf-8") as f:
                for r in csv.DictReader(row for row in f if not row.startswith("#")):
                    if r.get("purpose") == "alias":
                        table = {"species": "species", "item": "items", "ability": "abilities", "move": "moves",
                                 "nature": "natures"}.get(r["kind"])
                        target = self.maps.get(table, {}).get(r["target_id"])
                        if table and target:
                            self.maps[table][self.norm(r["alias"])] = target

    @staticmethod
    def norm(s: str) -> str:
        return re.sub(r"[\s·_\-’'.:()]", "", s.lower())

    def find(self, table: str, name: str) -> tuple[str, str]:
        m = self.maps[table]
        for key in (self.norm(name), to_id(name)):
            if key in m:
                return m[key]
        close = difflib.get_close_matches(self.norm(name), list(m), n=3, cutoff=0.6)
        shown = sorted({f"{self.ko[table].get(m[c][0], m[c][1])}({m[c][1]})" for c in close})
        hint = f" (비슷한 이름: {', '.join(shown)})" if close else ""
        kind = {"species": "포켓몬", "items": "도구", "abilities": "특성", "moves": "기술", "natures": "성격"}[table]
        raise TeamParseError(f"{kind} '{name}'을(를) 찾지 못했다{hint}")


HEAD = re.compile(r"^(?P<name>[^@]+?)(?:\s*\((?P<gender>[MF])\))?\s*(?:@\s*(?P<item>.+))?$")


def parse_evs(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in re.split(r"[/,]", text):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*([^\d\s].*)$", part) or re.match(r"^([^\d\s].*?)\s*(\d+)$", part)
        if not m:
            raise TeamParseError(f"배분을 해석할 수 없다: '{part}'")
        num, label = (m.group(1), m.group(2)) if m.group(1).isdigit() else (m.group(2), m.group(1))
        stat = STAT_LABELS.get(re.sub(r"[\s.]", "", label.lower()))
        if not stat:
            raise TeamParseError(f"능력치 이름을 알 수 없다: '{label}'")
        out[stat] = int(num)
    return out


def parse_team(text: str, idx: NameIndex) -> list[ParsedSet]:
    blocks = [b for b in re.split(r"\n\s*\n", text.strip().replace("\r", "")) if b.strip()]
    if not blocks:
        raise TeamParseError("팀 텍스트가 비어 있다")
    errors: list[str] = []
    sets: list[ParsedSet] = []
    for bi, block in enumerate(blocks, 1):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        try:
            head = HEAD.match(lines[0])
            if not head:
                raise TeamParseError(f"첫 줄을 해석할 수 없다: '{lines[0]}'")
            name = head.group("name").strip()
            nick = re.match(r"^.+?\((.+)\)$", name)      # 별명 (종) 형식
            if nick and not re.fullmatch(r"[MF]", nick.group(1)):
                name = nick.group(1)
            sid, sname = idx.find("species", name)
            ps = ParsedSet(species=sname, species_id=sid)
            if head.group("item"):
                ps.item = idx.find("items", head.group("item").strip())[1]
            for l in lines[1:]:
                low = l.lower()
                if l.startswith("-") or l.startswith("•"):
                    mv = re.sub(r"^[-•]\s*", "", l).split("/")[0].strip()
                    ps.moves.append(idx.find("moves", mv)[1])
                elif re.match(r"^(ability|특성)\s*[:：]", low):
                    ps.ability = idx.find("abilities", l.split(":", 1)[-1].split("：", 1)[-1].strip())[1]
                elif re.match(r"^(nature|성격)\s*[:：]", low):
                    ps.nature = idx.find("natures", l.split(":", 1)[-1].split("：", 1)[-1].strip())[1]
                elif re.search(r"(nature|성격)$", low):
                    ps.nature = idx.find("natures", re.sub(r"\s*(nature|성격)$", "", l, flags=re.I).strip())[1]
                elif re.match(r"^(evs|sp|stat points|노력치|배분)\s*[:：]", low):
                    ps.evs = parse_evs(re.split(r"[:：]", l, maxsplit=1)[1])
                elif re.match(r"^(ivs|level|shiny|tera type|happiness|gigantamax)\s*:", low):
                    continue                              # 챔피언스에서 의미 없는 줄
                else:
                    raise TeamParseError(f"해석할 수 없는 줄: '{l}'")
            if len(ps.moves) > 4:
                raise TeamParseError(f"{sname}: 기술이 4개보다 많다")
            ps.compute_missing()
            sets.append(ps)
        except TeamParseError as e:
            errors.append(f"{bi}번째 포켓몬: {e}")
    if errors:
        raise TeamParseError("\n".join(errors))
    return sets
