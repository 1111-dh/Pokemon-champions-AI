"""engine/scripts/export-static.ts를 실행해 Showdown 정적 데이터를 받는다."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from pokechamp.paths import ENGINE_DIR

MIN_NODE_MAJOR = 22


def _node_major(node: str) -> int:
    out = subprocess.run([node, "--version"], capture_output=True, text=True, check=True).stdout
    return int(re.match(r"v(\d+)", out.strip()).group(1))


def find_node() -> str:
    """Node 22 이상을 찾는다: POKECHAMP_NODE → PATH → ~/.nvm의 가장 높은 버전."""
    candidates = [os.environ.get("POKECHAMP_NODE"), shutil.which("node")]
    nvm = Path.home() / ".nvm" / "versions" / "node"
    if nvm.exists():
        vers = sorted(nvm.iterdir(), key=lambda p: [int(x) for x in re.findall(r"\d+", p.name)], reverse=True)
        candidates += [str(v / "bin" / "node") for v in vers]
    for c in candidates:
        if c and Path(c).exists() and _node_major(c) >= MIN_NODE_MAJOR:
            return c
    raise RuntimeError(f"Node {MIN_NODE_MAJOR} 이상을 찾지 못했다 (nvm install 24 또는 POKECHAMP_NODE 지정)")


def export_static() -> dict:
    node = find_node()
    tsx = ENGINE_DIR / "node_modules" / "tsx" / "dist" / "cli.mjs"
    if not tsx.exists():
        raise RuntimeError("engine 의존성이 없다: cd engine && npm install && npm run setup:showdown")
    env = {**os.environ, "PATH": f"{Path(node).parent}{os.pathsep}{os.environ.get('PATH', '')}"}
    out = subprocess.run([node, str(tsx), "scripts/export-static.ts"], cwd=ENGINE_DIR, env=env,
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"Showdown 정적 데이터 내보내기 실패:\n{out.stderr[-2000:]}")
    return json.loads(out.stdout)
