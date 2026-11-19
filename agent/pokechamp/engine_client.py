"""TS 엔진 서버(engine/src/server.ts) 호출 래퍼.

모든 메서드는 단건 호출이고, `batch()`로 같은 엔드포인트에 여러 요청을 한 번에 보낸다.
표준 라이브러리만 쓴다. 서버 실행: `cd engine && npm run server`
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_URL = os.environ.get("POKECHAMP_ENGINE_URL", "http://127.0.0.1:8787")


class EngineError(RuntimeError):
    pass


class EngineClient:
    def __init__(self, base_url: str = DEFAULT_URL, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            payload = json.loads(e.read() or b"{}")
        except urllib.error.URLError as e:
            raise EngineError(f"엔진 서버에 연결할 수 없다 ({self.base_url}): {e.reason}") from e
        return payload

    def _call(self, path: str, body: dict[str, Any]) -> Any:
        payload = self._post(path, body)
        if not payload.get("ok"):
            raise EngineError(payload.get("error", "알 수 없는 오류"))
        return payload["result"]

    def batch(self, path: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """같은 엔드포인트에 여러 요청을 한 번에 보낸다. 항목마다 {ok, result | error}를 돌려준다."""
        payload = self._post(path, {"batch": items})
        if not payload.get("ok"):
            raise EngineError(payload.get("error", "알 수 없는 오류"))
        return payload["results"]

    def health(self) -> dict[str, Any]:
        with urllib.request.urlopen(self.base_url + "/health", timeout=5) as resp:
            return json.loads(resp.read())

    def validate_team(self, team: list[dict[str, Any]] | str) -> dict[str, Any]:
        return self._call("/validate_team", {"team": team})

    def new_battle(self, p1: Any, p2: Any, seed: str, picks: dict[str, str] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"p1": p1, "p2": p2, "seed": seed}
        if picks:
            body["picks"] = picks
        return self._call("/new_battle", body)

    def legal_actions(self, state: Any) -> dict[str, list[dict[str, Any]]]:
        return self._call("/legal_actions", {"state": state})

    def step(self, state: Any, p1: str | None = None, p2: str | None = None) -> dict[str, Any]:
        return self._call("/step", {"state": state, "p1": p1, "p2": p2})

    def simulate(self, seed: str, reps: int = 1, **kwargs: Any) -> dict[str, Any]:
        return self._call("/simulate", {"seed": seed, "reps": reps, **kwargs})

    def damage(self, attacker: dict[str, Any], defender: dict[str, Any], move: str,
               field: dict[str, str] | None = None) -> dict[str, Any]:
        """attacker/defender: {"set": PokemonSet, "mega": bool, "boosts": {...}, "status": "brn", "hpPercent": 100}.
        결과: rolls(16단계), critRolls, percent, typeEffectiveness, immune, status, calc(교차 검증)."""
        body: dict[str, Any] = {"attacker": attacker, "defender": defender, "move": move}
        if field:
            body["field"] = field
        return self._call("/damage", body)
