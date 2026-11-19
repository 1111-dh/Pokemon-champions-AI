from datetime import datetime, timezone

from pokechamp.data import official_news as on

PAYLOAD = {
    "hash": "h1",
    "data": [
        {"id": "877", "title": "12월 2일(수) 배틀 밸런스 조정 안내", "infoKind": 3, "kindTxt": "점검",
         "stAt": 1794967200, "endAt": "1796191199", "pubAt": 1795046715, "link": "page/877.html", "linkRom": ["CHP"]},
        {"id": "816", "title": "「레귤레이션 M-C」에 대해", "infoKind": 12, "kindTxt": "레귤레이션",
         "stAt": 1788127200, "endAt": "1796191199", "pubAt": 1788933000, "link": "page/816.html", "linkRom": ["CHP"]},
        {"id": "850", "title": "캠페인", "infoKind": 6, "kindTxt": "캠페인",
         "stAt": 1, "endAt": "2", "pubAt": 1790000000, "link": "page/850.html", "linkRom": ["CHP"]},
        {"id": "500", "title": "SV 공지", "infoKind": 7, "kindTxt": "소식",
         "stAt": 1, "endAt": "2", "pubAt": 1795046716, "link": "page/500.html", "linkRom": ["SC", "VI"]},
    ],
}


def test_parse_keeps_only_champions_sorted_newest_first():
    h, ns = on.parse_list(PAYLOAD)
    assert h == "h1"
    assert [n.id for n in ns] == ["877", "850", "816"]
    assert ns[0].end_at == 1796191199  # 문자열 endAt도 정수로


def test_importance_by_kind_or_keyword():
    _, ns = on.parse_list(PAYLOAD)
    imp = {n.id: n.important for n in ns}
    assert imp == {"877": True, "816": True, "850": False}  # 877은 '밸런스' 키워드, 816은 레귤레이션 종류


def test_diff_new_and_updated():
    _, ns = on.parse_list(PAYLOAD)
    by = {n.id: n for n in ns}
    prev = {"816": by["816"].to_json() | {"title": "옛 제목"}, "850": by["850"].to_json()}
    new, updated = on.diff(prev, ns)
    assert [n.id for n in new] == ["877"]
    assert [n.id for n in updated] == ["816"]


def test_run_writes_state_and_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(on, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(on, "MD_PATH", tmp_path / "n.md")
    now = datetime(2026, 11, 19, 6, 0, tzinfo=timezone.utc)
    r1 = on.run(payload=PAYLOAD, now=now)
    assert len(r1["new"]) == 3 and r1["hash_changed"]
    md = (tmp_path / "n.md").read_text(encoding="utf-8")
    assert "🆕⚠️ | [877]" in md and "## 종료된 공지" in md
    r2 = on.run(payload=PAYLOAD, now=now)  # 두 번째 실행: 바뀐 것 없음
    assert r2["new"] == [] and r2["updated"] == [] and not r2["hash_changed"]


def test_dry_run_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr(on, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(on, "MD_PATH", tmp_path / "n.md")
    on.run(payload=PAYLOAD, dry_run=True)
    assert not (tmp_path / "s.json").exists()
