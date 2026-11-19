"""포켓몬 챔피언스 공식 공지 확인.

사용자가 직접 실행할 때만 동작한다(자동 실행 없음). 실행하면:
1. 포켓몬 HOME 뉴스 서버의 공지 목록(JSON)을 한 번 받는다.
2. 챔피언스 공지(`linkRom`에 "CHP")만 고른다.
3. 지난 확인 이후 새로 올라왔거나 갱신된 공지를 찾는다.
4. `docs/official_news.json`(상태)과 `docs/OFFICIAL_NEWS.md`(사람이 읽는 목록)를 갱신한다.

실행: `cd agent && uv run python -m pokechamp.data.official_news [--lang ko] [--dry-run]`
출처와 형식 설명: docs/OFFICIAL_SOURCES.md
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
STATE_PATH = ROOT / "docs" / "official_news.json"
MD_PATH = ROOT / "docs" / "OFFICIAL_NEWS.md"
LIST_URL = "https://news.pokemon-home.com/{lang}/json/list.json"
PAGE_URL = "https://news.pokemon-home.com/{lang}/page/{id}.html"
GAME_CODE = "CHP"
USER_AGENT = "pokechamp-personal/0.1"
KST = timezone(timedelta(hours=9))

KIND_NAMES = {
    3: "점검", 4: "업데이트", 6: "캠페인", 7: "소식", 11: "랭크배틀", 12: "레귤레이션",
    13: "온라인 대회", 15: "셀렉트", 16: "배틀 패스", 18: "오류",
}
# 엔진과 데이터에 영향을 주는 공지 종류. 이 종류는 새로 나오면 반드시 사람이 확인한다.
IMPORTANT_KINDS = {4, 11, 12}
IMPORTANT_KEYWORDS = ("밸런스", "레귤레이션", "조정", "업데이트", "balance", "regulation", "update")


@dataclass(frozen=True)
class Notice:
    id: str
    title: str
    kind: int
    kind_txt: str
    st_at: int
    end_at: int
    pub_at: int
    link: str

    @property
    def important(self) -> bool:
        t = self.title.lower()
        return self.kind in IMPORTANT_KINDS or any(k in t for k in IMPORTANT_KEYWORDS)

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


def parse_list(payload: dict[str, Any]) -> tuple[str, list[Notice]]:
    """공지 목록 JSON에서 챔피언스 공지만 뽑는다."""
    out = []
    for it in payload.get("data", []):
        if GAME_CODE not in (it.get("linkRom") or []):
            continue
        out.append(
            Notice(
                id=str(it["id"]),
                title=str(it.get("title", "")).strip(),
                kind=int(it.get("infoKind") or 0),
                kind_txt=str(it.get("kindTxt") or KIND_NAMES.get(int(it.get("infoKind") or 0), "")),
                st_at=int(it.get("stAt") or 0),
                end_at=int(it.get("endAt") or 0),
                pub_at=int(it.get("pubAt") or 0),
                link=str(it.get("link", "")),
            )
        )
    out.sort(key=lambda n: (-n.pub_at, -int(n.id)))
    return str(payload.get("hash", "")), out


def diff(prev: dict[str, dict[str, Any]], notices: list[Notice]) -> tuple[list[Notice], list[Notice]]:
    """(새 공지, 갱신된 공지). 갱신 = 같은 id인데 제목이나 게시 시각이 바뀐 것."""
    new, updated = [], []
    for n in notices:
        old = prev.get(n.id)
        if old is None:
            new.append(n)
        elif old.get("title") != n.title or old.get("pub_at") != n.pub_at:
            updated.append(n)
    return new, updated


def fmt(ts: int) -> str:
    return datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d %H:%M") if ts else "-"


def render_md(lang: str, checked_at: datetime, list_hash: str, notices: list[Notice],
              new_ids: set[str], updated_ids: set[str]) -> str:
    now = int(checked_at.timestamp())
    lines = [
        "# 포켓몬 챔피언스 공식 공지 목록",
        "",
        "> 이 파일은 `uv run python -m pokechamp.data.official_news`가 만든다. 직접 고치지 말 것.",
        "> 출처와 관리 규칙은 [OFFICIAL_SOURCES.md](OFFICIAL_SOURCES.md)에 있다.",
        "",
        f"- 마지막 확인: {checked_at.astimezone(KST).strftime('%Y-%m-%d %H:%M')} KST",
        f"- 언어: `{lang}` · 목록 hash: `{list_hash}` · 챔피언스 공지 {len(notices)}개",
        f"- 이번 확인에서 새 공지 {len(new_ids)}개, 갱신 {len(updated_ids)}개",
        "",
        "표시: 🆕 새 공지 · 🔄 갱신 · ⚠️ 엔진/데이터 영향 가능(레귤레이션, 랭크배틀, 업데이트, 밸런스 조정)",
        "",
        "## 진행 중이거나 예정된 공지",
        "",
        "| | id | 게시 | 종류 | 제목 | 기간 |",
        "|---|---|---|---|---|---|",
    ]

    def row(n: Notice) -> str:
        mark = ("🆕" if n.id in new_ids else "🔄" if n.id in updated_ids else "") + ("⚠️" if n.important else "")
        url = PAGE_URL.format(lang=lang, id=n.id)
        title = n.title.replace("|", "\\|")
        return f"| {mark} | [{n.id}]({url}) | {fmt(n.pub_at)} | {n.kind_txt} | {title} | {fmt(n.st_at)} ~ {fmt(n.end_at)} |"

    active = [n for n in notices if n.end_at >= now]
    ended = [n for n in notices if n.end_at < now]
    lines += [row(n) for n in active]
    lines += ["", "## 종료된 공지", "", "| | id | 게시 | 종류 | 제목 | 기간 |", "|---|---|---|---|---|---|"]
    lines += [row(n) for n in ended]
    lines.append("")
    return "\n".join(lines)


def fetch_list(lang: str) -> dict[str, Any]:
    req = urllib.request.Request(LIST_URL.format(lang=lang), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"lang": None, "hash": None, "checked_at": None, "notices": {}}


def run(lang: str = "ko", dry_run: bool = False, payload: dict[str, Any] | None = None,
        now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    state = load_state()
    payload = payload if payload is not None else fetch_list(lang)
    list_hash, notices = parse_list(payload)
    prev = state.get("notices", {}) if state.get("lang") in (None, lang) else {}
    new, updated = diff(prev, notices)
    report = {
        "checked_at": now.isoformat(timespec="seconds"),
        "hash_changed": list_hash != state.get("hash"),
        "new": [n.to_json() for n in new],
        "updated": [n.to_json() for n in updated],
        "important": [n.to_json() for n in new + updated if n.important],
    }
    if not dry_run:
        STATE_PATH.write_text(json.dumps({
            "lang": lang,
            "hash": list_hash,
            "checked_at": report["checked_at"],
            "notices": {n.id: n.to_json() for n in notices},
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        MD_PATH.write_text(render_md(lang, now, list_hash, notices, {n.id for n in new}, {n.id for n in updated}),
                           encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="포켓몬 챔피언스 공식 공지 확인")
    ap.add_argument("--lang", default="ko")
    ap.add_argument("--dry-run", action="store_true", help="파일을 고치지 않고 결과만 출력")
    args = ap.parse_args(argv)
    r = run(args.lang, args.dry_run)
    print(f"확인 시각 {r['checked_at']} · 목록 변경 {'있음' if r['hash_changed'] else '없음'}")
    print(f"새 공지 {len(r['new'])}개, 갱신 {len(r['updated'])}개, 그중 엔진/데이터 영향 가능 {len(r['important'])}개")
    for n in r["important"]:
        print(f"  ⚠️ [{n['id']}] {n['kind_txt']} | {n['title']} | {PAGE_URL.format(lang=args.lang, id=n['id'])}")
    if not args.dry_run:
        print(f"갱신: {MD_PATH.relative_to(ROOT)}, {STATE_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
