"""외부 요청 공통 처리: 요청 간격, 파일 캐시, User-Agent.

개인용이지만 출처에 과도한 요청을 보내지 않도록 같은 호스트에는 최소 간격을 두고,
캐시가 유효하면 요청하지 않는다(PROJECT_PLAN 0절, 4.3절).
"""

from __future__ import annotations

import time
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "pokechamp-personal/0.1"
MIN_INTERVAL_S = 1.0
_last_request: dict[str, float] = {}


def fetch(url: str, cache: Path | None = None, max_age_s: float = 0, force: bool = False,
          timeout: float = 30, min_interval_s: float = MIN_INTERVAL_S) -> bytes:
    """URL 내용을 돌려준다. cache가 있고 max_age_s 안이면 캐시를 쓴다."""
    if cache and not force and cache.exists() and time.time() - cache.stat().st_mtime < max_age_s:
        return cache.read_bytes()
    host = urllib.parse.urlparse(url).netloc
    wait = min_interval_s - (time.monotonic() - _last_request.get(host, -1e9))
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    _last_request[host] = time.monotonic()  # 간격은 요청 시작 시각 사이로 잰다 (동시 요청은 하지 않는다)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(body)
    return body
