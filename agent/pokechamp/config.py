"""config/pokechamp.toml 읽기."""

from __future__ import annotations

import tomllib
from functools import lru_cache
from typing import Any

from pokechamp.paths import ROOT

CONFIG_PATH = ROOT / "config" / "pokechamp.toml"


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


def stats_config() -> dict[str, Any]:
    c = dict(load_config().get("stats", {}))
    c.setdefault("backfill_days", 7)
    c.setdefault("max_age_days", 2)
    c.setdefault("season_label_policy", "date")
    c.setdefault("request_interval_s", 0.5)
    c.setdefault("cache_hours", 6)
    c.setdefault("source_timeout_s", 600)
    c.setdefault("sources", {})
    return c


def priors_config() -> dict[str, Any]:
    c = dict(load_config().get("priors", {}))
    c.setdefault("half_life_days", 3.0)
    c.setdefault("stale_after_days", 2)
    c.setdefault("trend_min_gap_days", 3)
    c.setdefault("trend_max_gap_days", 14)
    c.setdefault("previous_regulation_weight", 0.2)
    c.setdefault("rank_weight_exponent", 1.0)
    return c
