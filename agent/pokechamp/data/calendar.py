"""공식 시즌·레귤레이션 달력. 공식 공지 목록(docs/official_news.json)의 랭크배틀(11)과 레귤레이션(12) 공지에서 만든다.

통계 출처가 붙인 시즌 표기는 틀릴 수 있으므로(docs/VERIFIED.md), 데이터 날짜로 공식 시즌을 판정할 때 쓴다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from pokechamp.paths import ROOT

KST = timezone(timedelta(hours=9))
NEWS_STATE = ROOT / "docs" / "official_news.json"
SEASON_RE = re.compile(r"시즌\s*(M-\d+)")
REGULATION_RE = re.compile(r"레귤레이션\s*(M-[A-Z])")


@dataclass(frozen=True)
class Period:
    name: str         # "M-8" 또는 "M-C"
    start: date       # KST 날짜 (시작일 포함)
    end: date         # KST 날짜 (종료일 포함: 종료 시각이 그날 오전이라 그날 데이터는 다음 시즌으로 본다)


def _kst_date(ts: int) -> date:
    return datetime.fromtimestamp(ts, KST).date()


@dataclass
class Calendar:
    seasons: list[Period]
    regulations: list[Period]

    @classmethod
    def from_news(cls, path: Path | None = None) -> "Calendar":
        p = path or NEWS_STATE
        notices = json.loads(p.read_text(encoding="utf-8"))["notices"].values() if p.exists() else []
        seasons, regs = {}, {}
        for n in notices:
            if n["kind"] == 11 and (m := SEASON_RE.search(n["title"])):
                seasons[m.group(1)] = Period(m.group(1), _kst_date(n["st_at"]), _kst_date(n["end_at"]))
            elif n["kind"] == 12 and (m := REGULATION_RE.search(n["title"])):
                regs[m.group(1)] = Period(m.group(1), _kst_date(n["st_at"]), _kst_date(n["end_at"]))
        key = lambda p: p.start  # noqa: E731
        return cls(sorted(seasons.values(), key=key), sorted(regs.values(), key=key))

    def season_on(self, d: date) -> str | None:
        """그 날짜의 공식 시즌. 시작일은 포함, 종료일은 다음 시즌 몫으로 본다."""
        for p in self.seasons:
            if p.start <= d < p.end:
                return p.name
        return None

    def regulation_of_season(self, season: str) -> str | None:
        s = next((p for p in self.seasons if p.name == season), None)
        if s is None:
            return None
        mid = s.start + (s.end - s.start) / 2
        return next((r.name for r in self.regulations if r.start <= mid <= r.end), None)


def normalize_season(label: str | None) -> str | None:
    """'M7' → 'M-7', 'Current' 같은 값은 None."""
    if not label:
        return None
    m = re.fullmatch(r"M-?(\d+)", label.strip(), re.I)
    return f"M-{m.group(1)}" if m else None
