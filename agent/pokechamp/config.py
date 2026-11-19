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


def sets_config() -> dict[str, Any]:
    c = dict(load_config().get("sets", {}))
    c.setdefault("samples_per_species", 3000)
    c.setdefault("max_archetypes", 40)
    c.setdefault("ipf_iterations", 30)
    c.setdefault("rule_generated_weight", 0.5)
    c.setdefault("seed", 20261119)
    return c


def teambuild_config() -> dict[str, Any]:
    c = dict(load_config().get("teambuild", {}))
    defaults = {"alpha": 0.5, "beta": 0.5, "cvar_fraction": 0.1, "threat_species": 60, "threat_archetypes": 3,
                "candidate_archetypes": 2, "opponent_teams": 64, "teammate_strength": 3.0,
                "scenario_top_exponent": 1.5, "scenario_trend_strength": 0.5, "assignment_weight": 0.6,
                "max_mega": 2, "beam_width": 6, "prefilter": 24, "local_search_rounds": 2,
                "nash_iterations": 300, "seed": 20261119}
    for k, v in defaults.items():
        c.setdefault(k, v)
    return c


def select_config() -> dict[str, Any]:
    c = dict(load_config().get("select", {}))
    defaults = {"time_budget_s": 20.0, "max_mega_in_pick": 1, "opponent_samples": 32, "precise_samples": 16,
                "lead_weight": 0.2, "initial_candidates": 4, "add_per_iteration": 2, "oracle_epsilon": 0.02,
                "probe_candidates": 4, "probe_rounds": 2, "final_solve": "rows_all",
                "opponent_model_mix": 0.3, "opponent_model_temperature": 5.0, "opponent_usage_exponent": 1.0,
                "games_per_second_estimate": 150.0, "show_min_probability": 0.005, "seed": 20261119}
    for k, v in defaults.items():
        c.setdefault(k, v)
    return c
