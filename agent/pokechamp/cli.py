"""사용자 CLI (PROJECT_PLAN 3.1). 모든 갱신은 사용자가 명령으로 실행할 때만 일어난다.

    uv run pokechamp status
    uv run pokechamp update-static [--yes] [--dry-run] [--force]
    uv run pokechamp update-stats [--force] [--backfill N]
    uv run pokechamp build-sets [--check N]
    uv run pokechamp matchup <포켓몬A> <포켓몬B> [--sim N]
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
