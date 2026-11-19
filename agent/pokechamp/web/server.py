"""로컬 웹 화면 서버 (표준 라이브러리만 사용).

`uv run pokechamp web`으로 띄운다. 엔진 서버가 꺼져 있으면 함께 띄우고, 종료할 때 같이 끈다.
모든 동작은 사용자가 화면에서 버튼을 누를 때만 일어난다(자동 갱신 없음).
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from pokechamp.data.db import active_version, connect
from pokechamp.engine_client import EngineClient, EngineError
from pokechamp.paths import ENGINE_DIR, ROOT
from pokechamp.status import status_data
from pokechamp.input.team_text import FIELD_KO, NameIndex, TeamParseError, parse_team
from pokechamp.teams.store import fill_defaults, list_teams, save_team, validate as validate_team
from pokechamp.web.battle import Playground, sprite_id

STATIC = Path(__file__).parent / "static"
_lock = threading.Lock()  # SQLite 연결과 배틀 세션을 스레드 사이에서 보호


class App:
    def __init__(self, client: EngineClient | None = None):
        self.conn = connect(check_same_thread=False)  # 요청 스레드들이 _lock으로 순서를 지켜 쓴다
        self.client = client or EngineClient()
        self.playground = Playground(self.conn, self.client)
        self.get_routes: dict[str, Callable[..., Any]] = {
            "/api/progress": self.progress,
            "/api/status": lambda q: status_data(self.conn),
            "/api/pokemon": self.pokemon_list,
            "/api/news": self.news,
            "/api/engine": self.engine_health,
            "/api/teams": lambda q: [{k: v for k, v in t.items() if k != "team_json"} for t in list_teams(self.conn)],
        }
        self.post_routes: dict[str, Callable[[dict], Any]] = {
            "/api/battle/new": lambda b: self.playground.new(b.get("seed") or None),
            "/api/battle/act": lambda b: self.playground.act(b["id"], b["choice"]),
            "/api/team/validate": self.team_check,
            "/api/team/save": self.team_save,
            "/api/team/recommend": self.team_recommend,
            "/api/team/random": lambda b: self.client.batch("/random_team", [{"seed": b.get("seed") or secrets.token_hex(4)}])[0]["result"],
            "/api/selfplay": self.selfplay,
            "/api/news/check": self.news_check,
            "/api/tests/run": self.run_tests,
        }

    # ---------- 진행 현황 ----------
    def progress(self, q: dict) -> dict:
        prog = json.loads((ROOT / "docs" / "progress.json").read_text(encoding="utf-8"))
        prog["verified"] = parse_verified((ROOT / "docs" / "VERIFIED.md").read_text(encoding="utf-8"))
        prog["git"] = git_info()
        return prog

    # ---------- 도감 ----------
    def pokemon_list(self, q: dict) -> dict:
        v = active_version(self.conn)
        if v is None:
            return {"version": None, "pokemon": []}
        sid = (q.get("id") or [None])[0]
        if sid:
            return self.pokemon_detail(v["id"], sid)
        ab = {r["id"]: r["name_ko"] or r["name_en"] for r in
              self.conn.execute("SELECT id, name_ko, name_en FROM abilities WHERE version_id=?", (v["id"],))}
        megas: dict[str, list[str]] = {}
        for r in self.conn.execute("SELECT base_species_id, mega_species_id FROM mega_forms WHERE version_id=?", (v["id"],)):
            megas.setdefault(r[0], []).append(r[1])
        out = []
        for r in self.conn.execute("SELECT * FROM species WHERE version_id=? ORDER BY num, is_mega, form_index", (v["id"],)):
            abilities = json.loads(r["abilities_json"])
            out.append({
                "id": r["id"], "name_ko": r["name_ko"], "name_en": r["name_en"], "num": r["num"],
                "types": json.loads(r["types_json"]), "stats": json.loads(r["base_stats_json"]),
                "abilities": [ab.get(re.sub(r"[^a-z0-9]", "", a.lower()), a) for a in abilities.values()],
                "is_mega": bool(r["is_mega"]), "base": r["base_species"], "megas": megas.get(r["id"], []),
                "official_code": r["official_code"], "sprite": sprite_id(r["name_en"]),
            })
        return {"version": v["id"], "regulation": v["regulation"], "pokemon": out}

    def pokemon_detail(self, vid: int, sid: str) -> dict:
        base = self.conn.execute("SELECT base_species_id FROM mega_forms WHERE version_id=? AND mega_species_id=?",
                                 (vid, sid)).fetchone()
        learn_id = base[0] if base else sid
        moves = [dict(r) for r in self.conn.execute(
            "SELECT m.id, m.name_ko, m.name_en, m.type, m.category, m.power, m.accuracy, m.max_pp, m.priority, m.description"
            " FROM learnsets l JOIN moves m ON m.version_id=l.version_id AND m.id=l.move_id"
            " WHERE l.version_id=? AND l.species_id=? ORDER BY m.type, m.power DESC", (vid, learn_id))]
        from pokechamp.sets.archetypes import load_archetypes
        reg = self.conn.execute("SELECT regulation FROM static_versions WHERE id=?", (vid,)).fetchone()[0]
        names = {t: {r[0]: r[1] or r[2] for r in self.conn.execute(f"SELECT id, name_ko, name_en FROM {t} WHERE version_id=?", (vid,))}
                 for t in ("moves", "items", "abilities")}
        sets = [{"weight": round(a.weight, 4), "item": names["items"].get(a.item, a.item),
                 "ability": names["abilities"].get(a.ability, a.ability),
                 "moves": [names["moves"].get(m, m) for m in a.moves], "mega": a.mega_species,
                 "rule_generated": a.rule_generated, "n_support": a.n_support,
                 "nature": max(a.nature_dist, key=a.nature_dist.get) if a.nature_dist else None,
                 "spread": max(a.spread_dist, key=lambda x: x[1])[0] if a.spread_dist else None}
                for a in load_archetypes(self.conn, learn_id, reg)[:6]]
        return {"id": sid, "moves": moves, "sets": sets}

    # ---------- 팀 ----------
    def _parse(self, b: dict) -> tuple[list, list[dict]]:
        try:
            sets = parse_team(b["text"], NameIndex(self.conn))
        except TeamParseError as e:
            raise ValueError(str(e)) from e
        notes = fill_defaults(self.conn, sets) if b.get("fill") else []
        missing = [{"species": p.species, "fields": [FIELD_KO[f] for f in p.missing]} for p in sets if p.missing]
        return sets, [{"notes": notes, "missing": missing}]

    def team_check(self, b: dict) -> dict:
        sets, (info,) = self._parse(b)
        team = [p.to_set() for p in sets]
        valid, problems = validate_team(team, self.client)
        return {"valid": valid, "problems": problems, "team": team, **info}

    def team_save(self, b: dict) -> dict:
        name = (b.get("name") or "").strip()
        if not name:
            raise ValueError("팀 이름을 넣으세요")
        sets, (info,) = self._parse(b)
        if info["missing"] and not b.get("keep_empty"):
            raise ValueError("빈 항목이 있다: " + "; ".join(f"{m['species']} {', '.join(m['fields'])}" for m in info["missing"])
                             + ". '빈 항목을 통계 기본값으로 채우기'를 켜거나 비워 둔 채 저장을 고르세요.")
        team = [p.to_set() for p in sets]
        valid, problems = validate_team(team, self.client)
        tid = save_team(self.conn, name, team, "manual", valid, problems if valid is not None else [], replace=bool(b.get("replace")))
        return {"id": tid, "name": name, "valid": valid, "problems": problems, **info}

    def team_recommend(self, b: dict) -> dict:
        """파티 추천 (PROJECT_PLAN 7절). 첫 실행은 상성 행렬 계산에 1분쯤, 이후는 10초 남짓. 검증을 켜면 시뮬레이션이 더 돈다."""
        from pokechamp.teambuild.search import Builder, RecommendError
        from pokechamp.teambuild.validate import validate as validate_sim
        from pokechamp.teams.store import export_text

        idx = NameIndex(self.conn)
        names = lambda key: [x.strip() for x in str(b.get(key) or "").replace("\n", ",").split(",") if x.strip()]  # noqa: E731
        try:
            fixed = [idx.find("species", n)[0] for n in names("fix")]
            exclude = [idx.find("species", n)[0] for n in names("exclude")]
        except TeamParseError as e:
            raise ValueError(str(e)) from e
        try:
            builder = Builder(self.conn, seed=(str(b.get("seed")).strip() or None) if b.get("seed") else None, client=self.client)
            r = builder.run(fixed, [], exclude)
        except RecommendError as e:
            raise ValueError(str(e)) from e
        n = int(b.get("validate") or 0)
        v = validate_sim(builder, r["_units"], min(n, 2000), f"{r['seed']}-check", self.client) if n else None
        r.pop("_units")
        if v:
            for k in ("recommended", "usage_top", "random"):
                v[k].pop("scores", None)
        r["validation"] = v
        r["export"] = export_text(r["team"])
        for m in r["members"]:
            m["sprite"] = sprite_id(m["set"]["species"])
        name = (b.get("save") or "").strip()
        if name:
            valid, problems = validate_team(r["team"], self.client)
            save_team(self.conn, name, r["team"], "recommended", valid, problems if valid is not None else [],
                      replace=bool(b.get("replace")))
            r["saved_as"] = name
        return json.loads(json.dumps(r, default=float))

    # ---------- 공식 공지 ----------
    def news(self, q: dict) -> dict:
        p = ROOT / "docs" / "official_news.json"
        if not p.exists():
            return {"checked_at": None, "notices": []}
        d = json.loads(p.read_text(encoding="utf-8"))
        notices = sorted(d["notices"].values(), key=lambda n: (-n["pub_at"], -int(n["id"])))
        return {"checked_at": d["checked_at"], "lang": d["lang"], "notices": notices}

    def news_check(self, b: dict) -> dict:
        from pokechamp.data import official_news
        return official_news.run(b.get("lang", "ko"))

    # ---------- 엔진 ----------
    def engine_health(self, q: dict) -> dict:
        try:
            return {"up": True, **self.client.health()}
        except Exception as e:  # noqa: BLE001
            return {"up": False, "error": str(e)}

    def selfplay(self, b: dict) -> dict:
        n = max(1, min(int(b.get("n", 50)), 500))
        seed = b.get("seed") or secrets.token_hex(4)
        t0 = time.time()
        teams = self.client.batch("/random_team", [{"seed": f"{seed}-{i}-{p}"} for i in range(n) for p in (1, 2)])
        reqs = [{"p1": teams[2 * i]["result"]["team"], "p2": teams[2 * i + 1]["result"]["team"],
                 "seed": f"{seed}-{i}", "reps": 1} for i in range(n)]
        res = self.client.batch("/simulate", reqs)
        games = [g for r in res if r["ok"] for g in r["result"]["games"]]
        errors = [r["error"] for r in res if not r["ok"]]
        turns = sorted(g["turns"] for g in games)
        return {
            "seed": seed, "games": n, "finished": len(games), "errors": len(errors), "error_samples": errors[:3],
            "p1_wins": sum(g["winner"] == "p1" for g in games), "p2_wins": sum(g["winner"] == "p2" for g in games),
            "turns_median": turns[len(turns) // 2] if turns else 0, "turns_max": turns[-1] if turns else 0,
            "seconds": round(time.time() - t0, 2),
        }

    # ---------- 테스트 실행 ----------
    def run_tests(self, b: dict) -> dict:
        from pokechamp.data.static.showdown_export import find_node
        out = {}
        py = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header"], cwd=ROOT / "agent",
                            capture_output=True, text=True, timeout=300)
        out["python"] = {"ok": py.returncode == 0, "summary": last_line(py.stdout), "tail": py.stdout[-3000:]}
        node = find_node()
        env = {**os.environ, "PATH": f"{Path(node).parent}{os.pathsep}{os.environ.get('PATH', '')}"}
        js = subprocess.run(["npm", "test", "--silent"], cwd=ENGINE_DIR, capture_output=True, text=True, timeout=300, env=env)
        passed = re.search(r"ℹ pass (\d+)", js.stdout)
        failed = re.search(r"ℹ fail (\d+)", js.stdout)
        out["engine"] = {"ok": js.returncode == 0,
                         "summary": f"{passed.group(1) if passed else '?'} passed, {failed.group(1) if failed else '?'} failed",
                         "tail": (js.stdout + js.stderr)[-3000:]}
        return out


def last_line(s: str) -> str:
    lines = [x for x in s.strip().splitlines() if x.strip()]
    return lines[-1] if lines else ""


def parse_verified(md: str) -> list[dict]:
    rows = []
    for line in md.splitlines():
        m = re.match(r"^\| (\d+) \| (.+?) \| (.+) \| (.+?) \|$", line)
        if m:
            rows.append({"no": int(m.group(1)), "item": m.group(2), "result": m.group(3), "state": m.group(4)})
    return rows


def git_info() -> dict:
    def git(*args: str) -> str:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
        return r.stdout.strip()
    log = []
    for line in git("log", "-15", "--format=%h\x1f%ad\x1f%s", "--date=format:%m-%d %H:%M").splitlines():
        h, d, s = line.split("\x1f")
        log.append({"hash": h, "date": d, "subject": s})
    dirty = [l for l in git("status", "--porcelain").splitlines() if l.strip()]
    return {"branch": git("branch", "--show-current"), "log": log, "uncommitted": len(dirty)}


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # 조용히
            pass

        def _json(self, code: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _static(self, path: str) -> None:
            rel = "index.html" if path in ("/", "") else path.removeprefix("/static/")
            f = (STATIC / rel).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = f.read_bytes()
            self.send_response(200)
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ""))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _run(self, fn: Callable, arg: Any) -> None:
            try:
                with _lock:
                    self._json(200, {"ok": True, "result": fn(arg)})
            except EngineError as e:
                self._json(503, {"ok": False, "error": f"엔진 서버 오류: {e}"})
            except (ValueError, KeyError) as e:
                self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

        def do_GET(self) -> None:
            u = urlparse(self.path)
            if u.path in app.get_routes:
                self._run(app.get_routes[u.path], parse_qs(u.query))
            elif u.path.startswith("/api/"):
                self._json(404, {"ok": False, "error": f"없는 경로: {u.path}"})
            else:
                self._static(u.path)

        def do_POST(self) -> None:
            u = urlparse(self.path)
            fn = app.post_routes.get(u.path)
            if not fn:
                self._json(404, {"ok": False, "error": f"없는 경로: {u.path}"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"ok": False, "error": "JSON 해석 실패"})
                return
            self._run(fn, body)

    return Handler


def ensure_engine(client: EngineClient) -> subprocess.Popen | None:
    """엔진 서버가 꺼져 있으면 띄운다. 이미 떠 있으면 그대로 쓴다."""
    try:
        client.health()
        print(f"엔진 서버 사용 중: {client.base_url}")
        return None
    except Exception:  # noqa: BLE001
        pass
    from pokechamp.data.static.showdown_export import find_node
    node = find_node()
    tsx = ENGINE_DIR / "node_modules" / "tsx" / "dist" / "cli.mjs"
    port = urlparse(client.base_url).port or 8787
    proc = subprocess.Popen([node, str(tsx), "src/server.ts"], cwd=ENGINE_DIR, env={**os.environ, "PORT": str(port)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(60):
        try:
            client.health()
            print(f"엔진 서버를 띄웠다: {client.base_url}")
            return proc
        except Exception:  # noqa: BLE001
            if proc.poll() is not None:
                raise RuntimeError(f"엔진 서버 시작 실패:\n{proc.stderr.read().decode()[-2000:]}")
            time.sleep(0.25)
    proc.terminate()
    raise RuntimeError("엔진 서버가 15초 안에 응답하지 않았다")


def serve(port: int = 8790, open_browser: bool = True) -> int:
    client = EngineClient()
    engine = ensure_engine(client)
    app = App(client)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{port}"
    print(f"웹 화면: {url}  (끄려면 Ctrl+C)")
    if open_browser:
        webbrowser.open(url)  # 소켓은 이미 열려 있으므로 바로 열어도 된다
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료한다.")
    finally:
        httpd.server_close()
        if engine:
            engine.terminate()
    return 0
