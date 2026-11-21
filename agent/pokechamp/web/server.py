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
from pokechamp.web.sprites import sprite_id

STATIC = Path(__file__).parent / "static"
_lock = threading.Lock()  # SQLite 연결과 배틀 세션을 스레드 사이에서 보호


class App:
    def __init__(self, client: EngineClient | None = None):
        self.conn = connect(check_same_thread=False)  # 요청 스레드들이 _lock으로 순서를 지켜 쓴다
        self.client = client or EngineClient()
        self.live: dict[str, Any] = {}       # 실전 세션 (id → LiveBattle)
        self.get_routes: dict[str, Callable[..., Any]] = {
            "/api/progress": self.progress,
            "/api/status": lambda q: status_data(self.conn),
            "/api/pokemon": self.pokemon_list,
            "/api/news": self.news,
            "/api/engine": self.engine_health,
            "/api/teams": lambda q: [{k: v for k, v in t.items() if k != "team_json"} for t in list_teams(self.conn)],
            "/api/battles": self.battles,
            "/api/builder/options": self.builder_options,
            "/api/builder/species": self.builder_species,
        }
        self.post_routes: dict[str, Callable[[dict], Any]] = {
            "/api/team/validate": self.team_check,
            "/api/team/save": self.team_save,
            "/api/team/recommend": self.team_recommend,
            "/api/review": self.review,
            "/api/live/new": self.live_new,
            "/api/live/start": lambda b: self._live(b).start([int(x) for x in b.get("slots", [])], str(b.get("opp_lead") or ""),
                                                             list(b.get("opp_lead_entry") or [])),
            "/api/live/entry": lambda b: self._live(b).entry_options(str(b.get("species") or "")),
            "/api/live/preview": lambda b: self._live(b).preview(str(b.get("text") or "")),
            "/api/live/apply": lambda b: json.loads(json.dumps(self._live(b).apply(str(b.get("text") or "")), default=float)),
            "/api/live/why": lambda b: self._live(b).explain(),
            "/api/live/view": lambda b: json.loads(json.dumps(self._live(b).view(), default=float)),
            "/api/live/end": lambda b: self._live(b).end(str(b.get("result") or ""), bool(b.get("save", True))),
            "/api/battle/delete": self.battle_delete,
            "/api/team/delete": self.team_delete,
            "/api/team/show": self.team_show,
            "/api/team/save_sets": self.team_save_sets,
            "/api/team/parse_ids": self.team_parse_ids,
            "/api/calc/damage": self.calc_damage,
            "/api/calc/stats": self.calc_stats,
            "/api/live/estimate": lambda b: self._live(b).estimate(b.get("me") or {}, b.get("opp") or {}),
            "/api/live/turn": lambda b: json.loads(json.dumps(self._live(b).submit_turn(b.get("form") or {}), default=float)),
            "/api/live/replace": lambda b: json.loads(json.dumps(self._live(b).submit_replace(b.get("me"), b.get("opp"),
                                                                                              list(b.get("opp_entry") or [])), default=float)),
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

    # ---------- 실전 (배틀 중 추천) ----------
    def _live(self, b: dict):
        lb = self.live.get(str(b.get("id") or ""))
        if lb is None:
            raise ValueError("실전 세션이 없다 (서버를 다시 띄웠으면 새로 시작하세요)")
        return lb

    def live_new(self, b: dict) -> dict:
        """상대 6마리 → 선출 추천 (시간 예산 안에서 끝난다)."""
        from pokechamp.battle.live import LiveBattle
        from pokechamp.select.recommend import SelectError, parse_opponents
        from pokechamp.teams.store import load_team

        try:
            t = load_team(self.conn, str(b.get("team") or ""))
            opp, notes = parse_opponents(self.conn, str(b.get("opp") or "").replace("\n", ","))
            if len(opp) != 6:
                raise SelectError(f"상대 포켓몬은 6마리여야 한다 (지금 {len(opp)}마리)")
            lb = LiveBattle(self.conn, self.client, t["team"], opp, team_name=t["name"])
            sel = lb.selection(float(b["budget"]) if b.get("budget") else None)
        except SelectError as e:
            raise ValueError(str(e)) from e
        sid = secrets.token_hex(6)
        self.live[sid] = lb
        from pokechamp.battle.events import SpeciesResolver
        res = SpeciesResolver(self.conn)
        mine = [{"slot": i + 1, "species": s["species"], "name": lb.ko("species", res(s["species"])), "sprite": sprite_id(s["species"]),
                 "item": lb.ko("items", re.sub(r"[^a-z0-9]", "", (s.get("item") or "").lower()))} for i, s in enumerate(t["team"])]
        opp_list = [{"id": sp, "name": lb.ko("species", sp), "sprite": sprite_id(lb.en(sp))} for sp in opp]
        return json.loads(json.dumps({"id": sid, "notes": notes, "selection": sel, "mine": mine, "opp": opp_list}, default=float))

    def battle_delete(self, b: dict) -> dict:
        from pokechamp.battle.store import delete_battle
        return {"deleted": delete_battle(self.conn, int(b["id"]))}

    def team_delete(self, b: dict) -> dict:
        from pokechamp.teams.store import delete_team
        name = str(b.get("name") or "")
        if not delete_team(self.conn, name):
            raise ValueError(f"'{name}' 팀이 없다")
        return {"deleted": name}

    # ---------- 팀 만들기 (선택 방식) ----------
    NATURE_STATS = {  # 성격 → (올라가는 능력, 내려가는 능력). 무보정 성격은 없음
        "lonely": ("atk", "def"), "brave": ("atk", "spe"), "adamant": ("atk", "spa"), "naughty": ("atk", "spd"),
        "bold": ("def", "atk"), "relaxed": ("def", "spe"), "impish": ("def", "spa"), "lax": ("def", "spd"),
        "timid": ("spe", "atk"), "hasty": ("spe", "def"), "jolly": ("spe", "spa"), "naive": ("spe", "spd"),
        "modest": ("spa", "atk"), "mild": ("spa", "def"), "quiet": ("spa", "spe"), "rash": ("spa", "spd"),
        "calm": ("spd", "atk"), "gentle": ("spd", "def"), "sassy": ("spd", "spe"), "careful": ("spd", "spa")}
    STAT_KO = {"hp": "HP", "atk": "공격", "def": "방어", "spa": "특공", "spd": "특방", "spe": "스피드"}

    def builder_options(self, q: dict) -> dict:
        v = active_version(self.conn)
        if v is None:
            return {"species": [], "items": [], "natures": [], "megas": []}
        species = [{"id": r["id"], "name": r["name_ko"] or r["name_en"], "name_en": r["name_en"], "sprite": sprite_id(r["name_en"]),
                    "types": json.loads(r["types_json"])}
                   for r in self.conn.execute("SELECT * FROM species WHERE version_id=? AND is_mega=0 ORDER BY num, form_index", (v["id"],))]
        items = [{"id": r["id"], "name": r["name_ko"] or r["name_en"], "name_en": r["name_en"], "mega": bool(r["is_mega_stone"]),
                  "berry": bool(r["is_berry"])}
                 for r in self.conn.execute("SELECT * FROM items WHERE version_id=? ORDER BY name_ko", (v["id"],))]
        natures = []
        for r in self.conn.execute("SELECT * FROM natures WHERE version_id=? ORDER BY name_ko", (v["id"],)):
            up, down = self.NATURE_STATS.get(r["id"], (None, None))
            natures.append({"id": r["id"], "name": r["name_ko"] or r["name_en"], "name_en": r["name_en"],
                            "note": f"{self.STAT_KO[up]}↑ {self.STAT_KO[down]}↓" if up else "무보정"})
        megas = [{"id": r["mega_species_id"], "base": r["base_species_id"], "stone": r["stone_item_id"],
                  "name": r["name_ko"] or r["name_en"], "name_en": r["name_en"], "sprite": sprite_id(r["name_en"]),
                  "types": json.loads(r["types_json"])}
                 for r in self.conn.execute(
                     "SELECT f.*, s.name_ko, s.name_en, s.types_json FROM mega_forms f JOIN species s"
                     " ON s.version_id=f.version_id AND s.id=f.mega_species_id WHERE f.version_id=? ORDER BY s.num, s.form_index", (v["id"],))]
        return {"species": species, "items": items, "natures": natures, "megas": megas}

    def builder_species(self, q: dict) -> dict:
        """그 포켓몬이 가질 수 있는 특성과 배울 수 있는 기술, 통계상 많이 쓰는 선택지 (위에 먼저 보인다)."""
        from pokechamp.data.priors import Priors
        v = active_version(self.conn)
        sid = (q.get("id") or [""])[0]
        r = self.conn.execute("SELECT * FROM species WHERE version_id=? AND id=?", (v["id"], sid)).fetchone()
        if r is None:
            raise ValueError(f"포켓몬 '{sid}'이(가) 없다")
        ab_names = {x["id"]: x["name_ko"] or x["name_en"] for x in self.conn.execute(
            "SELECT id, name_ko, name_en FROM abilities WHERE version_id=?", (v["id"],))}
        abilities = []
        for a in json.loads(r["abilities_json"]).values():
            aid = re.sub(r"[^a-z0-9]", "", a.lower())
            abilities.append({"id": aid, "name": ab_names.get(aid, a)})
        moves = [{"id": m["id"], "name": m["name_ko"] or m["name_en"], "type": m["type"], "category": m["category"], "power": m["power"]}
                 for m in self.conn.execute(
                     "SELECT m.id, m.name_ko, m.name_en, m.type, m.category, m.power FROM learnsets l JOIN moves m"
                     " ON m.version_id=l.version_id AND m.id=l.move_id WHERE l.version_id=? AND l.species_id=? ORDER BY m.name_ko",
                     (v["id"], sid))]
        pri = Priors(self.conn)
        top = lambda kind, n: [k for k, p in pri.marginal(sid, kind).values[:n] if k]   # noqa: E731
        spreads = pri.marginal(sid, "spreads").values[:3]
        return {"id": sid, "name": r["name_ko"], "abilities": abilities, "moves": moves,
                "popular": {"items": top("items", 8), "moves": top("moves", 12), "abilities": top("abilities", 3),
                            "natures": top("natures", 3),
                            "spreads": [json.loads(s) if isinstance(s, str) else s for s, _ in spreads]}}

    def team_parse_ids(self, b: dict) -> dict:
        """Showdown 텍스트 → 팀 만들기 칸 (id)."""
        from pokechamp.battle.events import SpeciesResolver
        sets, _ = self._parse(b)
        res = SpeciesResolver(self.conn)
        return {"sets": [{"species": res(p.species_id), "item": re.sub(r"[^a-z0-9]", "", (p.item or "").lower()),
                          "ability": re.sub(r"[^a-z0-9]", "", (p.ability or "").lower()),
                          "nature": re.sub(r"[^a-z0-9]", "", (p.nature or "").lower()), "evs": p.evs or {},
                          "moves": [re.sub(r"[^a-z0-9]", "", m.lower()) for m in p.moves]} for p in sets]}

    def _set_from_ids(self, s: dict) -> dict:
        """선택 상자의 id(species, item, ability, nature, moves) → 엔진·저장용 세트(영어 이름)."""
        v = active_version(self.conn)
        en = lambda table, x: (self.conn.execute(f"SELECT name_en FROM {table} WHERE version_id=? AND id=?", (v["id"], x)).fetchone()  # noqa: E731
                               or [""])[0] if x else ""
        return {"species": en("species", s.get("species")) or s.get("species", ""), "item": en("items", s.get("item")),
                "ability": en("abilities", s.get("ability")), "nature": en("natures", s.get("nature")),
                "evs": {k: int(x) for k, x in (s.get("evs") or {}).items() if str(x).strip() not in ("", "0")},
                "moves": [en("moves", m) or m for m in (s.get("moves") or []) if m]}

    # ---------- 계산기 (데미지 · 실수치) ----------
    WEATHERS = {"sunnyday", "raindance", "sandstorm", "snowscape"}
    TERRAINS = {"electricterrain", "grassyterrain", "psychicterrain", "mistyterrain"}
    SCREENS = {"reflect", "lightscreen", "auroraveil"}
    CALC_NOTES = ["n타 사이의 회복·잔여 데미지(먹다남은음식, 모래바람, 독 등)는 넣지 않습니다"]

    def _calc_side(self, side: dict) -> dict:
        out: dict[str, Any] = {"set": self._set_from_ids(side.get("set") or {})}
        if side.get("mega"):
            out["mega"] = True
        boosts = {k: int(x) for k, x in (side.get("boosts") or {}).items() if int(x or 0)}
        if boosts:
            out["boosts"] = boosts
        if side.get("status"):
            out["status"] = side["status"]
        if side.get("hp_pct") not in (None, "", 100):
            out["hpPercent"] = max(1, min(100, int(side["hp_pct"])))
        return out

    def calc_damage(self, b: dict) -> dict:
        """계산기: 엔진 /damage(난수 16단계) + 확정/난수 n타. 방어측 현재 HP(hp_pct) 기준."""
        from pokechamp.calc.ko import ko_chances_hits
        move = re.sub(r"[^a-z0-9]", "", str(b.get("move") or "").lower())
        if not move:
            raise ValueError("기술을 고르세요")
        att, dfd = self._calc_side(b.get("attacker") or {}), self._calc_side(b.get("defender") or {})
        f = b.get("field") or {}
        field: dict[str, Any] = {k: v for k, v in (("weather", f.get("weather")), ("terrain", f.get("terrain")))
                                 if v in (self.WEATHERS if k == "weather" else self.TERRAINS)}
        screens = [s for s in (f.get("screens") or []) if s in self.SCREENS]
        if screens:
            field["screens"] = screens
        r = self.client.batch("/damage", [{"attacker": att, "defender": dfd, "move": move, "field": field, "crossCheck": False}])[0]
        if not r.get("ok"):
            raise ValueError(r.get("error") or "데미지 계산 실패")
        d = r["result"]
        info = self.client.batch("/moves_info", [{"moves": [move]}])[0]["result"].get(move, {})
        options = d.get("hitOptions") or [1]
        default = options[-1] if d.get("attackerAbility") == "skilllink" else options[0]   # 스킬링크는 항상 최대 타수
        hits = int(b["hits"]) if b.get("hits") and int(b["hits"]) in options else default
        maxhp = d["defender"]["maxhp"]
        hp = maxhp if "hpPercent" not in dfd else max(1, maxhp * dfd["hpPercent"] // 100)
        per_hit = d["perHit"][:hits]

        def part(key: str) -> dict:
            hit_rolls = [h[key] for h in per_hit]
            lo, hi = sum(x[0] for x in hit_rolls), sum(x[-1] for x in hit_rolls)
            return {"min": lo, "max": hi, "min_pct": round(100 * lo / maxhp, 1), "max_pct": round(100 * hi / maxhp, 1),
                    "rolls": hit_rolls[0], "ko": ko_chances_hits(hit_rolls, hp)}
        return {"move": d["move"], "type": info.get("type"), "category": info.get("category"),
                "attacker": d["attacker"], "defender": {**d["defender"], "hp": hp},
                "immune": d["immune"], "status": d["status"], "effectiveness": d["typeEffectiveness"],
                "normal": part("rolls"), "crit": part("critRolls"), "hits": hits, "hit_options": options, "per_hit": per_hit,
                "notes": self.CALC_NOTES, "screens": screens}

    def calc_stats(self, b: dict) -> dict:
        req = {"set": self._set_from_ids(b.get("set") or {}), "mega": bool(b.get("mega"))}
        if b.get("status"):
            req["status"] = b["status"]
        r = self.client.batch("/stats", [req])[0]
        if not r.get("ok"):
            raise ValueError(r.get("error") or "실수치 계산 실패")
        return {**r["result"], "spe_status": r["result"].get("speStatus", r["result"]["spe"]),
                "field_hint": self._field_hint(r["result"].get("ability") or "")}

    def _field_hint(self, ability: str) -> dict | None:
        """날씨·필드를 까는 특성이면 그 날씨·필드 (계산기는 자동으로 켜지 않고 안내만 한다: 턴이 지나거나 덮일 수 있다)."""
        from pokechamp.battle.live import TERRAIN_SETTERS, WEATHER_SETTERS
        w = {"sun": "sunnyday", "rain": "raindance", "sand": "sandstorm", "snow": "snowscape"}
        t = {"electric": "electricterrain", "psychic": "psychicterrain", "grassy": "grassyterrain", "misty": "mistyterrain"}
        if ability in WEATHER_SETTERS:
            return {"weather": w[WEATHER_SETTERS[ability]], "ability": ability}
        if ability in TERRAIN_SETTERS:
            return {"terrain": t[TERRAIN_SETTERS[ability]], "ability": ability}
        return None

    def team_save_sets(self, b: dict) -> dict:
        """선택 방식으로 만든 팀 저장: 칸마다 id(species, item, ability, nature, moves)와 Stat Points(evs)."""
        name = (b.get("name") or "").strip()
        if not name:
            raise ValueError("팀 이름을 넣으세요")
        team, notes = [], []
        for i, s in enumerate(b.get("sets") or []):
            if not s.get("species"):
                continue
            st = self._set_from_ids(s)
            missing = [k for k, label in (("item", "도구"), ("ability", "특성"), ("nature", "성격")) if not st[k]]
            if len(st["moves"]) < 4:
                missing.append("moves")
            if missing:
                notes.append(f"{i + 1}번 {st['species']}: 비어 있는 항목 {', '.join(missing)}")
            team.append(st)
        if not team:
            raise ValueError("포켓몬을 한 마리 이상 고르세요")
        valid, problems = validate_team(team, self.client)
        if b.get("check_only"):
            return {"valid": valid, "problems": problems, "notes": notes}
        save_team(self.conn, name, team, "manual", valid, problems if valid is not None else [], replace=bool(b.get("replace")))
        return {"name": name, "valid": valid, "problems": problems, "notes": notes}

    def team_show(self, b: dict) -> dict:
        from pokechamp.battle.events import SpeciesResolver
        from pokechamp.teams.store import export_text, load_team
        t = load_team(self.conn, str(b.get("name") or ""))
        names = NameIndex(self.conn)
        res = SpeciesResolver(self.conn)
        members = [{"species": s["species"], "name": names.ko["species"].get(res(s["species"]), s["species"]), "sprite": sprite_id(s["species"]),
                    "item": names.ko["items"].get(re.sub(r"[^a-z0-9]", "", (s.get("item") or "").lower()), s.get("item") or ""),
                    "filled": s.get("filled") or []} for s in t["team"]]
        ids = lambda table, x: names.maps[table].get(names.norm(x or ""), (re.sub(r"[^a-z0-9]", "", (x or "").lower()), ""))[0] if x else ""   # noqa: E731
        sets = [{"species": res(s["species"]), "item": ids("items", s.get("item")), "ability": ids("abilities", s.get("ability")),
                 "nature": ids("natures", s.get("nature")), "evs": s.get("evs") or {},
                 "moves": [ids("moves", m) for m in s.get("moves", [])]} for s in t["team"]]
        return {"name": t["name"], "source": t["source"], "valid": t["valid"], "problems": t["problems"],
                "created_at": t["created_at"], "members": members, "export": export_text(t["team"]), "sets": sets}

    # ---------- 복기 ----------
    def battles(self, q: dict) -> list:
        from pokechamp.battle.store import list_battles
        return [{k: v for k, v in r.items() if k != "opp_team_json"} for r in list_battles(self.conn)]

    def review(self, b: dict) -> dict:
        """복기 (PROJECT_PLAN 10절): 턴마다 탐색을 다시 돌리므로 턴 수 × 몇 초가 걸린다."""
        from pokechamp.battle.review import label, review
        from pokechamp.battle.search import SearchConfig
        from pokechamp.input.battle_text import BattleText
        bid = b.get("id") or "latest"
        r = review(self.conn, bid, self.client, SearchConfig(seed=f"review-{bid}", model_reps=6))
        bt = BattleText(self.conn)
        rows = [{"turn": p["turn"], "phase": p["phase"], "win": p.get("win"), "regret": p.get("regret"),
                 "actual": label(p.get("actual"), bt), "best": label(p.get("best"), bt), "flag": p.get("flag"),
                 "input": p["input"], "error": p.get("error")} for p in r["points"]]
        return {"battle_id": r["battle_id"], "result": r["result"], "source": r["source"], "svg": r["svg"], "rows": rows,
                "mistakes": r["mistakes"], "unlucky": r["unlucky"]}

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
