"""공식 레귤레이션 공지에서 '참가할 수 있는 포켓몬' 목록을 가져온다 (허용 목록의 기준, PROJECT_PLAN 4.1).

흐름: 공지 목록 JSON → 지금 유효한 챔피언스 레귤레이션 공지 → 본문의 pokemon.html 링크 → 페이지 안의 `const pokemons` 배열.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from pokechamp.data.http import fetch
from pokechamp.paths import RAW_DIR

NEWS_LIST = "https://news.pokemon-home.com/{lang}/json/list.json"
NEWS_PAGE = "https://news.pokemon-home.com/{lang}/page/{id}.html"
POKEMON_LINK = re.compile(r'href="(https://web-view\.app\.pokemonchampions\.jp/battle/pages/events/[^"/]+/)ko/pokemon\.html"')
POKEMONS_ARRAY = re.compile(r"const pokemons = (\[.*?\]);", re.S)
REGULATION_TITLE = re.compile(r"레귤레이션\s*(M-[A-Z])")
REGULATION_KIND = 12
CACHE = RAW_DIR / "official"
CACHE_AGE_S = 6 * 3600


@dataclass(frozen=True)
class OfficialEntry:
    code: str          # "0080-002" = 전국도감번호-폼번호
    num: int
    form_index: int
    name_ko: str
    name_en: str


@dataclass(frozen=True)
class OfficialList:
    regulation: str    # "M-C"
    notice_id: str
    starts_at: int
    ends_at: int
    event_url: str
    entries: list[OfficialEntry]


def parse_pokemons(html: str) -> list[list]:
    m = POKEMONS_ARRAY.search(html)
    if not m:
        raise ValueError("참가 가능 포켓몬 페이지에서 목록 배열을 찾지 못했다 (페이지 구조가 바뀌었을 수 있다)")
    return json.loads(m.group(1))


def pick_regulation_notice(news: dict, now: float | None = None) -> dict:
    """지금 유효한 레귤레이션 공지. 없으면 가장 최근에 시작한 것."""
    now = now if now is not None else time.time()
    regs = [
        it for it in news.get("data", [])
        if "CHP" in (it.get("linkRom") or []) and int(it.get("infoKind") or 0) == REGULATION_KIND
        and REGULATION_TITLE.search(it.get("title", ""))
    ]
    if not regs:
        raise ValueError("챔피언스 레귤레이션 공지를 찾지 못했다")
    active = [it for it in regs if int(it["stAt"]) <= now <= int(it["endAt"])]
    return max(active or regs, key=lambda it: int(it["stAt"]))


def fetch_official_list(force: bool = False, now: float | None = None) -> OfficialList:
    news = json.loads(fetch(NEWS_LIST.format(lang="ko"), CACHE / "news_ko.json", CACHE_AGE_S, force))
    notice = pick_regulation_notice(news, now)
    regulation = REGULATION_TITLE.search(notice["title"]).group(1)
    page = fetch(NEWS_PAGE.format(lang="ko", id=notice["id"]), CACHE / f"notice_{notice['id']}.html",
                 CACHE_AGE_S, force).decode("utf-8")
    m = POKEMON_LINK.search(page)
    if not m:
        raise ValueError(f"레귤레이션 공지 {notice['id']}에서 참가 가능 포켓몬 링크를 찾지 못했다")
    event_url = m.group(1)
    lists = {}
    for lang in ("ko", "en"):
        html = fetch(event_url + f"{lang}/pokemon.html", CACHE / f"{regulation}_pokemon_{lang}.html",
                     CACHE_AGE_S, force).decode("utf-8")
        lists[lang] = parse_pokemons(html)
    ko, en = lists["ko"], lists["en"]
    if [r[0] for r in ko] != [r[0] for r in en]:
        raise ValueError("한국어/영어 참가 가능 목록의 순서나 구성이 다르다")
    entries = []
    for (code, _, name_ko), (_, _, name_en) in zip(ko, en):
        num, form = (int(x) for x in code.split("-"))
        entries.append(OfficialEntry(code, num, form, name_ko, name_en))
    return OfficialList(regulation, str(notice["id"]), int(notice["stAt"]), int(notice["endAt"]), event_url, entries)
