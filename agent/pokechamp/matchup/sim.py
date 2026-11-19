"""1:1 상성 정밀 시뮬레이션 (PROJECT_PLAN 6절). 엔진에서 최대 데미지 봇끼리 1:1 배틀을 여러 판 돌려 승률을 추정한다.
상위 후보 검증용이다 (빠른 근사보다 느리지만 특성, 도구, 부가효과, 급소, 난수가 모두 들어간다)."""

from __future__ import annotations

from typing import Any

from pokechamp.engine_client import EngineClient


def sim_matchup(a: dict[str, Any], b: dict[str, Any], reps: int = 100, seed: str = "sim",
                client: EngineClient | None = None) -> dict[str, Any]:
    client = client or EngineClient()
    r = client.simulate(seed=seed, reps=reps, p1=[a], p2=[b], format="custom",
                        picks={"p1": "team 1", "p2": "team 1"}, policy="maxdamage")
    w = r["wins"]
    done = reps - w["unfinished"]
    return {"win_rate": w["p1"] / done if done else 0.5, "wins": w, "reps": reps, "seed": seed}
