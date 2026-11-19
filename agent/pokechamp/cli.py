"""사용자 CLI (PROJECT_PLAN 3.1). 모든 갱신은 사용자가 명령으로 실행할 때만 일어난다.

    uv run pokechamp status
    uv run pokechamp update-static [--yes] [--dry-run] [--force]
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
    print("  없음. (update-stats는 아직 구현 전)")
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
