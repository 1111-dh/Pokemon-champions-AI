"""통계 출처: championsbattledata.com (인게임 랭크배틀 데이터, 공개 JSON API).

API 규칙(https://championsbattledata.com/api-rules/): 개인 사용 허용, 적당한 캐시 허용, 재배포 금지.
포켓몬별 /api/battle/Singles/<showdown id>?days=N 이 일별 스냅샷을 준다. 카테고리마다 상위 10개만 있다.
출처의 시즌 표기(예: 'M7')는 공식 시즌과 어긋날 수 있다 → 시즌은 날짜로 판정한다(update.py).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from pokechamp.data.http import fetch
from pokechamp.paths import RAW_DIR

NAME = "championsbattledata"
BASE = "https://championsbattledata.com"
CACHE = RAW_DIR / "stats" / NAME
CATEGORY = {"move": "moves", "held_item": "items", "ability": "abilities", "stat_alignment": "natures",
            "stat_points": "spreads", "teammate": "teammates"}
SPREAD_COLS = {"hp": "hp_points", "atk": "attack_points", "def": "defense_points", "spa": "sp_atk_points",
               "spd": "sp_def_points", "spe": "speed_points"}


@dataclass
class DailyData:
    page_date: date
    page_season: str | None
    # species_id → 범주 → [(이름, 순위, 비율 0~1 또는 None, 부가정보)]
    rows: dict[str, dict[str, list[tuple[str, int, float | None, dict]]]] = field(default_factory=dict)


def newest_daily_date(index: dict[str, Any]) -> date | None:
    """색인의 dailyDataFolders(예: 'M7/19_11_2026')에서 출처의 가장 최근 데이터 날짜."""
    ds = []
    for f in index.get("dailyDataFolders", []):
        try:
            ds.append(datetime.strptime(f.split("/")[-1], "%d_%m_%Y").date())
        except ValueError:
            continue
    return max(ds) if ds else None


@dataclass
class CollectResult:
    source: str
    format: str
    days: dict[date, DailyData] = field(default_factory=dict)
    rankings: dict[str, int] = field(default_factory=dict)
    generated_at: str | None = None
    newest: date | None = None
    errors: list[str] = field(default_factory=list)
    partial: bool = False
    seconds: float = 0.0


def parse_daily(d: dict[str, Any], species_id: str, out: dict[date, DailyData]) -> None:
    pdate = datetime.strptime(d["date"], "%d_%m_%Y").date()
    day = out.setdefault(pdate, DailyData(pdate, d.get("season")))
    cats: dict[str, list] = {}
    for r in d.get("rows", []):
        cat = CATEGORY.get(r.get("category"))
        if not cat:
            continue
        pct = r.get("percentage_value")
        if pct is None and r.get("percentage"):
            pct = float(str(r["percentage"]).rstrip("%"))
        rate = pct / 100 if pct is not None else None
        extra: dict[str, Any] = {}
        if cat == "spreads":
            extra = {k: int(r.get(col) or 0) for k, col in SPREAD_COLS.items()}
        elif cat == "natures":
            extra = {"up": r.get("stat_up") or "", "down": r.get("stat_down") or ""}
        cats.setdefault(cat, []).append((r.get("name") or "", int(r.get("rank") or 0), rate, extra))
    day.rows[species_id] = cats


def collect(species_ids: list[str], days: int, fmt: str = "Singles", *, force: bool = False,
            interval_s: float = 0.5, cache_hours: float = 6, timeout_s: float = 600,
            progress: Callable[[int, int], None] | None = None,
            have_through: date | None = None) -> CollectResult:
    """have_through: 이미 저장한 가장 최근 데이터 날짜. 출처에 그보다 새 날짜가 없으면 포켓몬별 요청을 하지 않는다."""
    res = CollectResult(source=NAME, format=fmt.lower())
    t0 = time.time()
    get = lambda path, name: json.loads(fetch(  # noqa: E731
        BASE + path, CACHE / name, cache_hours * 3600, force, min_interval_s=interval_s))
    try:
        idx = get("/api", "index.json")
        res.generated_at = idx.get("generatedAt")
        res.newest = newest_daily_date(idx)
        if have_through and res.newest and res.newest <= have_through and not force:
            res.seconds = round(time.time() - t0, 1)
            return res  # 새 데이터 없음
        rk = get(f"/api/rankings?format={fmt}", f"rankings_{fmt}.json")
        res.rankings = {r["showdownId"]: int(r["position"]) for r in rk.get("rankings", [])}
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"색인/순위 요청 실패: {e}")
    for i, sid in enumerate(species_ids):
        if time.time() - t0 > timeout_s:
            res.partial = True
            res.errors.append(f"시간 상한 {timeout_s}초 초과: {i}/{len(species_ids)}마리까지만 받음")
            break
        try:
            data = get(f"/api/battle/{fmt}/{sid}?days={days}", f"{fmt}_{sid}_d{days}.json")
            for d in data.get("daily", []):
                parse_daily(d, sid, res.days)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"{sid}: {e}")
        if progress:
            progress(i + 1, len(species_ids))
    res.seconds = round(time.time() - t0, 1)
    return res
