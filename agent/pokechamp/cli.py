"""사용자 CLI (PROJECT_PLAN 3.1). 모든 갱신은 사용자가 명령으로 실행할 때만 일어난다.

    uv run pokechamp status
    uv run pokechamp update-static [--yes] [--dry-run] [--force]
    uv run pokechamp update-stats [--force] [--backfill N]
    uv run pokechamp build-sets [--check N]
    uv run pokechamp matchup <포켓몬A> <포켓몬B> [--sim N]
    uv run pokechamp import-team <이름> [--file 파일] [--fill-defaults | --keep-empty]
    uv run pokechamp teams [list | show <이름> | delete <이름>]
    uv run pokechamp rollback-static <버전 id>
    uv run pokechamp web
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

from pokechamp.data.db import connect

KST = timezone(timedelta(hours=9))


def _kst(ts: int | None) -> str:
    return datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d %H:%M") if ts else "-"


def _ago(iso: str) -> str:
    delta = datetime.now(timezone.utc) - datetime.fromisoformat(iso)
    days = delta.total_seconds() / 86400
    return f"{days:.1f}일 전" if days >= 1 else f"{delta.total_seconds() / 3600:.1f}시간 전"


def cmd_status(args: argparse.Namespace) -> int:
    from pokechamp.status import status_data
    d = status_data(connect())
    st = d["static"]
    print("[정적 데이터]")
    if st is None:
        print("  없음. `pokechamp update-static`을 실행하세요.")
    else:
        c = st["counts"]
        print(f"  버전 {st['version']} · 레귤레이션 {st['regulation']} (공지 {st['official_notice_id']}) · "
              f"Showdown {st['showdown_commit'][:7]} · 적용 {_ago(st['applied_at'])}")
        print(f"  레귤레이션 기간 {_kst(st['regulation_starts_at'])} ~ {_kst(st['regulation_ends_at'])} (KST)")
        print(f"  포켓몬 {c['species']} (메가 {c['mega_forms']}), 기술 {c['moves']}, 도구 {c['items']}, 특성 {c['abilities']}")
        if st["regulation_ended"]:
            print("  ⚠️ 이 레귤레이션은 끝났다. 새 레귤레이션 공지를 확인하고 `pokechamp update-static` 실행을 권장한다.")
    print("[통계 스냅샷]")
    if not d["stats"]:
        print("  없음. 배틀 전에 `pokechamp update-stats`를 실행하세요.")
    for x in d["stats"]:
        flag = "" if x["verified"] else f" (출처 표기 {x['page_season']}, 날짜 기준으로 판정)"
        print(f"  {x['source']} {x['format']}: 데이터 {x['latest_date']} ({x['age_days']}일 전) · 시즌 {x['official_season']}"
              f" · 레귤레이션 {x['regulation']} · 스냅샷 {x['snapshots']}개{flag}")
        if x["age_days"] >= 2:
            print(f"  ⚠️ 통계가 {x['age_days']}일 전 것이다. `pokechamp update-stats` 실행을 권장한다.")
    print("[최근 갱신 기록]")
    for r in d["update_log"][:5]:
        print(f"  {r['ran_at']} {r['kind']} {r['status']} {r['duration_s']}s {r['note'] or ''}")
    return 0


def cmd_update_static(args: argparse.Namespace) -> int:
    from pokechamp.data.static.build import build_bundle
    from pokechamp.data.static.names import load_names
    from pokechamp.data.static.official_list import fetch_official_list
    from pokechamp.data.static.showdown_export import export_static
    from pokechamp.data.static.update import (
        apply_bundle, compute_diff, format_diff, log_update, revalidate_my_teams,
    )

    started = time.time()
    conn = connect()
    print("공식 참가 가능 목록, Showdown 데이터, 한국어 이름을 모으는 중…")
    try:
        bundle = build_bundle(fetch_official_list(force=args.force), export_static(), load_names(force=args.force))
    except Exception as e:  # noqa: BLE001
        log_update(conn, "failed", started, str(e)[:500])
        print(f"실패: {e}", file=sys.stderr)
        return 1
    diff = compute_diff(conn, bundle)
    print(format_diff(diff, bundle))
    if args.dry_run:
        return 0
    if diff.empty:
        log_update(conn, "unchanged", started)
        return 0
    if not args.yes and input("이대로 적용할까요? [y/N] ").strip().lower() not in ("y", "yes"):
        print("적용하지 않았다.")
        log_update(conn, "declined", started)
        return 0
    vid = apply_bundle(conn, bundle)
    print(f"정적 데이터 버전 {vid}을 적용했다. 상성 캐시를 무효화했다.")
    for msg in revalidate_my_teams(conn):
        print(f"⚠️ {msg}")
    log_update(conn, "applied", started, f"version {vid}")
    return 0


def cmd_update_stats(args: argparse.Namespace) -> int:
    from pokechamp.data.stats.update import run_update_stats
    conn = connect()
    last = {}

    def progress(src: str, i: int, n: int) -> None:
        if i == n or i - last.get(src, 0) >= 50:
            last[src] = i
            print(f"  {src}: {i}/{n}")

    print("통계를 수집하는 중… (출처마다 요청 간격을 둔다)")
    try:
        reports = run_update_stats(conn, force=args.force, backfill=args.backfill, progress=progress)
    except Exception as e:  # noqa: BLE001
        print(f"실패: {e}", file=sys.stderr)
        return 1
    for r in reports:
        print(f"[{r.source}] {r.status} · {r.seconds}초 · 최신 데이터 {r.latest_date or '-'} · 저장 {len(r.stored)}일 {', '.join(r.stored)}")
        for w in r.warnings:
            print(f"  ⚠️ {w}")
        for e in r.errors[:10]:
            print(f"  ✖ {e}")
        if len(r.errors) > 10:
            print(f"  … 오류 {len(r.errors) - 10}건 더")
        if r.skipped:
            print(f"  건너뜀 {len(r.skipped)}건: {r.skipped[0]}{' …' if len(r.skipped) > 1 else ''}")
    if any(r.stored for r in reports):
        from pokechamp.sets.archetypes import build_all
        print("새 통계로 세트 원형을 다시 만드는 중…")
        b = build_all(conn)
        print(f"  원형 {b['archetypes']}개 (통계 날짜 {b['stats_date']})")
    return 0 if all(r.status != "failed" for r in reports) else 1


def cmd_build_sets(args: argparse.Namespace) -> int:
    from pokechamp.sets.archetypes import build_all, check_samples
    conn = connect()
    print("세트 원형을 만드는 중… (포켓몬마다 후보를 뽑고 규칙 필터와 IPF 보정)")
    r = build_all(conn)
    print(f"포켓몬 {r['species']}, 원형 {r['archetypes']}개, 통계 날짜 {r['stats_date']}, "
          f"완성 세트가 있는 포켓몬 {len(r['with_samples'])}, 통계 없이 기본 세트로 대체 {len(r['fallback'])}")
    if r["fallback"]:
        print(f"  기본 세트 대체: {', '.join(r['fallback'][:20])}")
    c = check_samples(conn, args.check)
    print(f"검사: 세트 {c['samples']}개 중 규칙 위반 {c['violations']}개")
    for e in c["examples"]:
        print(f"  ✖ {e}")
    return 0 if c["violations"] == 0 else 1


def _find_species(conn, name: str) -> str:
    from pokechamp.data.db import active_version
    from pokechamp.data.static.names import to_id
    v = active_version(conn)
    q = name.strip()
    rows = conn.execute("SELECT id, name_ko, name_en FROM species WHERE version_id=? AND is_mega=0", (v["id"],)).fetchall()
    for r in rows:
        if r["id"] == to_id(q) or (r["name_ko"] or "").replace(" ", "") == q.replace(" ", ""):
            return r["id"]
    hits = [r["id"] for r in rows if q in (r["name_ko"] or "") or to_id(q) in r["id"]]
    if len(hits) == 1:
        return hits[0]
    raise SystemExit(f"포켓몬을 찾지 못했다: {name}" + (f" (후보: {', '.join(hits[:8])})" if hits else ""))


def cmd_matchup(args: argparse.Namespace) -> int:
    from pokechamp.matchup.candidates import candidates
    from pokechamp.matchup.fast import MatchupCache
    from pokechamp.matchup.sim import sim_matchup
    conn = connect()
    a_id, b_id = _find_species(conn, args.a), _find_species(conn, args.b)
    ca, cb = candidates(conn, a_id, args.top), candidates(conn, b_id, args.top)
    if not ca or not cb:
        print("세트 원형이 없다. `pokechamp build-sets`를 먼저 실행하세요.")
        return 1
    cache = MatchupCache(conn)
    scores = cache.scores([((x.set, x.mega), (y.set, y.mega)) for x in ca for y in cb])
    print(f"{a_id} 관점 1:1 점수 (빠른 근사, 0~1). 행={a_id} 세트, 열={b_id} 세트. 새로 계산 {cache.computed}쌍")
    print(" " * 34 + "".join(f"{y.label[:22]:>24}" for y in cb))
    k = 0
    for x in ca:
        row = f"{x.label[:30]:<30} {x.weight:>4.0%}"
        for _ in cb:
            row += f"{scores[k]:>24.2f}"
            k += 1
        print(row)
    exp = sum(x.weight * y.weight * scores[i * len(cb) + j] for i, x in enumerate(ca) for j, y in enumerate(cb))
    wsum = sum(x.weight for x in ca) * sum(y.weight for y in cb)
    print(f"원형 가중 평균: {exp / wsum:.2f}")
    if args.sim:
        r = sim_matchup(ca[0].set, cb[0].set, reps=args.sim, seed=args.seed)
        print(f"정밀 시뮬레이션 (최대 데미지 봇, 첫 세트끼리, {args.sim}판, 시드 {args.seed}): {a_id} 승률 {r['win_rate']:.2f}")
    return 0


def cmd_import_team(args: argparse.Namespace) -> int:
    from pathlib import Path
    from pokechamp.input.team_text import FIELD_KO, NameIndex, TeamParseError, parse_team
    from pokechamp.teams.store import fill_defaults, save_team, validate
    conn = connect()
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    else:
        if sys.stdin.isatty():
            print("팀을 붙여 넣고 빈 줄 두 번 뒤 Ctrl+D (Windows는 Ctrl+Z, Enter)로 끝내세요.")
        text = sys.stdin.read()
    try:
        sets = parse_team(text, NameIndex(conn))
    except TeamParseError as e:
        print(f"해석 실패:\n{e}", file=sys.stderr)
        return 1
    if len(sets) != 6:
        print(f"⚠️ 포켓몬이 {len(sets)}마리다 (게임 규칙은 6마리). 그래도 저장한다.")
    missing = [(p, p.missing) for p in sets if p.missing]
    if missing:
        print("빈 항목:")
        for p, m in missing:
            print(f"  {p.species}: {', '.join(FIELD_KO[f] for f in m)}")
        choice = "fill" if args.fill_defaults else "keep" if args.keep_empty else None
        if choice is None:
            if args.file and sys.stdin.isatty():
                ans = input("빈 항목을 최신 통계의 기본값으로 채울까요? [y=채움 / n=비워 둠 / q=취소] ").strip().lower()
                choice = {"y": "fill", "n": "keep"}.get(ans, "quit")
            else:
                print("빈 항목을 어떻게 할지 정해 다시 실행하세요: --fill-defaults(통계 기본값으로 채움) 또는 --keep-empty(비워 둠)")
                return 2
        if choice == "quit":
            print("취소했다.")
            return 0
        if choice == "fill":
            for note in fill_defaults(conn, sets):
                print(f"  채움 · {note}")
    team = [p.to_set() for p in sets]
    valid, problems = validate(team)
    if valid is None:
        print(f"⚠️ {problems[0]}")
    elif valid:
        print("검증: 규칙 통과")
    else:
        print(f"⚠️ 규칙 위반 {len(problems)}개 (경고만 하고 저장한다):")
        for pr in problems:
            print(f"  - {pr}")
    tid = save_team(conn, args.name, team, "manual", valid, problems if valid is not None else [], replace=args.replace)
    print(f"팀 '{args.name}'을(를) 저장했다 (id {tid}, 직접 입력).")
    return 0


def cmd_teams(args: argparse.Namespace) -> int:
    from pokechamp.teams.store import delete_team, export_text, list_teams, load_team
    conn = connect()
    if args.action == "list":
        teams = list_teams(conn)
        if not teams:
            print("저장된 팀이 없다. `pokechamp import-team <이름>`으로 넣으세요.")
        for t in teams:
            state = {None: "검증 안 함", 1: "규칙 통과", 0: f"위반 {len(t['problems'])}개"}[t["valid"]]
            filled = sum(1 for s in t["team"] if s.get("filled"))
            print(f"{t['name']:<20} {t['source']:<11} {state:<10} {', '.join(s['species'] for s in t['team'])}"
                  + (f"  (기본값으로 채운 포켓몬 {filled})" if filled else ""))
    elif args.action == "show":
        t = load_team(conn, args.name)
        print(export_text(t["team"]))
        for s in t["team"]:
            if s.get("filled"):
                print(f"# {s['species']}: 통계 기본값으로 채운 항목 {', '.join(s['filled'])}")
        for p in t["problems"]:
            print(f"# 경고: {p}")
    elif args.action == "delete":
        print("삭제했다." if delete_team(conn, args.name) else "그런 팀이 없다.")
    return 0


def cmd_recommend_team(args: argparse.Namespace) -> int:
    import json
    from pokechamp.input.team_text import NameIndex, TeamParseError
    from pokechamp.teambuild.report import text_report
    from pokechamp.teambuild.search import Builder, RecommendError
    from pokechamp.teambuild.validate import validate
    from pokechamp.teams.store import load_team, save_team, validate as validate_team
    conn = connect()
    idx = NameIndex(conn)
    try:
        fixed = [idx.find("species", n)[0] for n in args.fix]
        exclude = [idx.find("species", n)[0] for n in args.exclude]
    except TeamParseError as e:
        print(e)
        return 1
    locked = []
    if args.keep_team:
        team = load_team(conn, args.keep_team)["team"]
        slots = [int(x) for x in args.keep.split(",")] if args.keep else range(1, len(team) + 1)
        locked = [team[i - 1] for i in slots]
    log = (lambda s: None) if args.json else (lambda s: print(s, file=sys.stderr, flush=True))
    try:
        b = Builder(conn, seed=args.seed, log=log)
        r = b.run(fixed, locked, exclude)
    except RecommendError as e:
        print(e)
        return 1
    v = validate(b, r["_units"], args.validate, f"{r['seed']}-check") if args.validate else None
    r.pop("_units")
    if args.save:
        ok, problems = validate_team(r["team"])
        save_team(conn, args.save, r["team"], source="recommended", valid=ok, problems=problems, replace=args.replace)
        r["saved_as"] = args.save
    if args.json:
        if v:
            for k in ("recommended", "usage_top", "random"):
                v[k].pop("scores", None)
        print(json.dumps({**r, "validation": v}, ensure_ascii=False, indent=2, default=float))
    else:
        print(text_report(r, v))
        if args.save:
            print(f"\n'{args.save}' 이름으로 저장했다 (source=recommended).")
    return 0


def cmd_rollback_static(args: argparse.Namespace) -> int:
    from pokechamp.data.static.update import activate_version
    conn = connect()
    activate_version(conn, args.version)
    print(f"정적 데이터 버전 {args.version}을 활성화했다. 상성 캐시를 무효화했다.")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    from pokechamp.web.server import serve
    return serve(port=args.port, open_browser=not args.no_browser)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pokechamp", description="포켓몬 챔피언스 싱글배틀 보조 에이전트")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="정적 데이터 버전과 통계 스냅샷 신선도").set_defaults(fn=cmd_status)
    us = sub.add_parser("update-static", help="정적 데이터 갱신 (차이 목록을 보여 주고 승인 후 적용)")
    us.add_argument("--yes", action="store_true", help="확인 없이 적용")
    us.add_argument("--dry-run", action="store_true", help="차이 목록만 보여 주고 적용하지 않음")
    us.add_argument("--force", action="store_true", help="캐시를 무시하고 다시 받음")
    us.set_defaults(fn=cmd_update_static)
    ust = sub.add_parser("update-stats", help="통계 스냅샷 수집 (배틀 전에 직접 실행)")
    ust.add_argument("--force", action="store_true", help="캐시를 무시하고, 이미 저장한 날짜도 다시 받아 교체")
    ust.add_argument("--backfill", type=int, help="처음 실행 시 받아 올 일별 스냅샷 수 (기본은 설정값)")
    ust.set_defaults(fn=cmd_update_stats)
    bs = sub.add_parser("build-sets", help="세트 원형(결합 분포)을 다시 만들고 규칙 위반을 검사")
    bs.add_argument("--check", type=int, default=10_000, help="검사할 샘플 세트 수")
    bs.set_defaults(fn=cmd_build_sets)
    mu = sub.add_parser("matchup", help="두 포켓몬의 대표 세트끼리 1:1 상성 (빠른 근사, 선택적으로 정밀 시뮬레이션)")
    mu.add_argument("a")
    mu.add_argument("b")
    mu.add_argument("--top", type=int, default=3, help="포켓몬당 원형 상위 몇 개")
    mu.add_argument("--sim", type=int, default=0, help="첫 세트끼리 정밀 시뮬레이션 판 수")
    mu.add_argument("--seed", default="cli")
    mu.set_defaults(fn=cmd_matchup)
    it = sub.add_parser("import-team", help="내 팀 6마리 가져오기 (Showdown 형식, 한국어/영어)")
    it.add_argument("name", help="저장할 팀 이름")
    it.add_argument("--file", help="팀 텍스트 파일 (없으면 표준 입력)")
    it.add_argument("--fill-defaults", action="store_true", help="빈 항목을 최신 통계 기본값으로 채움")
    it.add_argument("--keep-empty", action="store_true", help="빈 항목을 비워 둔 채 저장")
    it.add_argument("--replace", action="store_true", help="같은 이름의 팀을 덮어씀")
    it.set_defaults(fn=cmd_import_team)
    tm = sub.add_parser("teams", help="저장한 팀 목록, 보기, 삭제")
    tm.add_argument("action", choices=["list", "show", "delete"], nargs="?", default="list")
    tm.add_argument("name", nargs="?")
    tm.set_defaults(fn=cmd_teams)
    rt = sub.add_parser("recommend-team", help="파티 추천: 고정한 0~5마리를 기준으로 나머지를 채운다")
    rt.add_argument("--fix", action="append", default=[], help="꼭 넣을 포켓몬 (세트는 탐색이 고른다, 여러 번 가능)")
    rt.add_argument("--keep-team", help="세트까지 고정할 포켓몬을 가져올 저장된 팀 이름")
    rt.add_argument("--keep", help="--keep-team에서 고정할 슬롯 번호 (예: 1,3). 없으면 전부")
    rt.add_argument("--exclude", action="append", default=[], help="뺄 포켓몬 (여러 번 가능)")
    rt.add_argument("--seed", help="시드 (기본은 설정값, 같은 시드면 같은 결과)")
    rt.add_argument("--validate", type=int, default=0, metavar="N",
                    help="정밀 시뮬레이션으로 검증: 메타 팀 N개와 짝지은 대전 (사용률 상위·무작위 팀과 비교)")
    rt.add_argument("--save", help="이 이름으로 내 팀에 저장")
    rt.add_argument("--replace", action="store_true", help="같은 이름의 팀이 있으면 덮어쓰기")
    rt.add_argument("--json", action="store_true", help="JSON으로 출력")
    rt.set_defaults(fn=cmd_recommend_team)
    rb = sub.add_parser("rollback-static", help="이전 정적 데이터 버전으로 되돌리기")
    rb.add_argument("version", type=int)
    rb.set_defaults(fn=cmd_rollback_static)
    wb = sub.add_parser("web", help="엔진 서버와 웹 화면을 띄운다 (진행 현황, 도감, 배틀 해보기)")
    wb.add_argument("--port", type=int, default=8790)
    wb.add_argument("--no-browser", action="store_true", help="브라우저를 자동으로 열지 않음")
    wb.set_defaults(fn=cmd_web)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
