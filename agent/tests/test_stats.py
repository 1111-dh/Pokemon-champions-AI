"""update-stats 단위 테스트 (네트워크 없이)."""

import json
from datetime import date, datetime

import pytest

from pokechamp.data.calendar import KST, Calendar, normalize_season
from pokechamp.data.db import connect
from pokechamp.data.stats import championsbattledata as cbd
from pokechamp.data.stats.update import days_to_fetch, stats_status, store


def ts(y, m, d, h=11):
    return int(datetime(y, m, d, h, tzinfo=KST).timestamp())


NEWS = {"notices": {
    "845": {"id": "845", "title": "「랭크배틀 시즌 M-7」 개최 중!", "kind": 11, "st_at": ts(2026, 10, 7, 15), "end_at": ts(2026, 11, 4, 10)},
    "867": {"id": "867", "title": "「랭크배틀 시즌 M-8」 개최 중!", "kind": 11, "st_at": ts(2026, 11, 4, 13), "end_at": ts(2026, 12, 2, 10)},
    "803": {"id": "803", "title": "「랭크배틀 시즌 M-5」 개최 중!", "kind": 11, "st_at": ts(2026, 8, 5, 15), "end_at": ts(2026, 9, 9, 10)},
    "816": {"id": "816", "title": "「레귤레이션 M-C」에 대해", "kind": 12, "st_at": ts(2026, 8, 31, 7), "end_at": ts(2026, 12, 2, 10)},
    "776": {"id": "776", "title": "「레귤레이션 M-B」에 대해", "kind": 12, "st_at": ts(2026, 6, 17, 15), "end_at": ts(2026, 9, 9, 10)},
}}


@pytest.fixture
def cal(tmp_path):
    p = tmp_path / "news.json"
    p.write_text(json.dumps(NEWS, ensure_ascii=False), encoding="utf-8")
    return Calendar.from_news(p)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.sqlite")
    c.execute("INSERT INTO static_versions (applied_at, regulation, showdown_commit, format, active) VALUES ('x','M-C','c','f',1)")
    for sid in ("garchomp", "primarina"):
        c.execute("INSERT INTO species (version_id,id,name_en,num,base_species,is_mega,types_json,base_stats_json,abilities_json)"
                  " VALUES (1,?,?,1,?,0,'[]','{}','{}')", (sid, sid, sid))
    c.execute("INSERT INTO moves (version_id,id,name_en,type,category,power,pp,max_pp,priority) VALUES (1,'earthquake','Earthquake','Ground','Physical',100,10,12,0)")
    c.execute("INSERT INTO items (version_id,id,name_en,is_mega_stone,is_berry) VALUES (1,'choicescarf','Choice Scarf',0,0)")
    c.execute("INSERT INTO abilities (version_id,id,name_en) VALUES (1,'roughskin','Rough Skin')")
    c.execute("INSERT INTO natures (version_id,id,name_en) VALUES (1,'jolly','Jolly')")
    c.commit()
    return c


def daily(date_str, season, extra_move=None):
    rows = [
        {"category": "move", "rank": 1, "name": "Earthquake", "percentage": "75.2%", "percentage_value": 75.2},
        {"category": "held_item", "rank": 1, "name": "Choice Scarf", "percentage": "22.5%", "percentage_value": 22.5},
        {"category": "ability", "rank": 1, "name": "Rough Skin", "percentage": "99.2%", "percentage_value": 99.2},
        {"category": "stat_alignment", "rank": 1, "name": "Jolly", "percentage": "30.8%", "percentage_value": 30.8,
         "stat_up": "Speed", "stat_down": "Sp. Atk"},
        {"category": "stat_points", "rank": 1, "name": "", "percentage": "33.5%", "percentage_value": 33.5,
         "hp_points": 2, "attack_points": 32, "defense_points": 0, "sp_atk_points": 0, "sp_def_points": 0, "speed_points": 32},
        {"category": "teammate", "rank": 1, "name": "Primarina", "percentage": "", "percentage_value": None},
    ]
    if extra_move:
        rows.append({"category": "move", "rank": 2, "name": extra_move, "percentage": "10%", "percentage_value": 10})
    return {"season": season, "date": date_str, "rows": rows}


def result(*dailies):
    res = cbd.CollectResult(source=cbd.NAME, format="singles", rankings={"garchomp": 1, "primarina": 2, "ghost": 3})
    for d in dailies:
        cbd.parse_daily(d, "garchomp", res.days)
    return res


def test_calendar_and_season_labels(cal):
    assert cal.season_on(date(2026, 11, 3)) == "M-7"
    assert cal.season_on(date(2026, 11, 4)) == "M-8"     # 시즌 시작일 데이터는 새 시즌
    assert cal.season_on(date(2026, 12, 2)) is None      # 종료일은 다음 시즌 몫 (달력에 없음)
    assert cal.regulation_of_season("M-8") == "M-C" and cal.regulation_of_season("M-5") == "M-B"
    assert normalize_season("M7") == "M-7" and normalize_season("Current") is None


def test_parse_daily_shapes():
    res = result(daily("19_11_2026", "M7"))
    day = res.days[date(2026, 11, 19)]
    cats = day.rows["garchomp"]
    assert cats["moves"][0][:3] == ("Earthquake", 1, pytest.approx(0.752))
    assert cats["spreads"][0][3] == {"hp": 2, "atk": 32, "def": 0, "spa": 0, "spd": 0, "spe": 32}
    assert cats["teammates"][0][2] is None
    assert cbd.newest_daily_date({"dailyDataFolders": ["M7/19_11_2026", "M6/06_10_2026", "bad"]}) == date(2026, 11, 19)


def test_store_date_policy_records_mislabel(conn, cal):
    rep = store(conn, result(daily("18_11_2026", "M7"), daily("19_11_2026", "M7", "Mystery Move")), cal,
                regulation="M-C", policy="date", force=False, max_age_days=2, today=date(2026, 11, 19))
    assert rep.stored == ["2026-11-18", "2026-11-19"] and rep.status == "ok"
    assert any("M7" in w and "M-8" in w for w in rep.warnings)
    assert any("Mystery Move" in w for w in rep.warnings)          # 정적 데이터에 없는 기술은 경고
    s = conn.execute("SELECT * FROM snapshots WHERE page_updated_at='2026-11-19'").fetchone()
    assert s["official_season"] == "M-8" and s["page_season"] == "M7" and s["verified"] == 0
    assert conn.execute("SELECT COUNT(*) FROM usage_species WHERE snapshot_id=?", (s["id"],)).fetchone()[0] == 2  # ghost 제외
    assert conn.execute("SELECT rate FROM usage_moves WHERE snapshot_id=? AND move_id='earthquake'", (s["id"],)).fetchone()[0] == pytest.approx(0.752)
    assert json.loads(conn.execute("SELECT spread_json FROM usage_spreads WHERE snapshot_id=?", (s["id"],)).fetchone()[0])["atk"] == 32
    # 같은 날짜는 다시 저장하지 않는다
    rep2 = store(conn, result(daily("19_11_2026", "M7")), cal, regulation="M-C", policy="date", force=False,
                 max_age_days=2, today=date(2026, 11, 19))
    assert rep2.stored == [] and rep2.status == "unchanged"
    st = stats_status(conn, today=date(2026, 11, 21))
    assert st[0]["latest_date"] == "2026-11-19" and st[0]["age_days"] == 2 and st[0]["snapshots"] == 2


def test_store_strict_policy_rejects_mislabel(conn, cal):
    rep = store(conn, result(daily("19_11_2026", "M7")), cal, regulation="M-C", policy="strict", force=False, max_age_days=2)
    assert rep.stored == [] and "strict" in rep.skipped[0]
    rep = store(conn, result(daily("19_11_2026", "M8")), cal, regulation="M-C", policy="strict", force=False, max_age_days=2)
    assert rep.stored == ["2026-11-19"]


def test_store_rejects_other_regulation_and_warns_stale(conn, cal):
    rep = store(conn, result(daily("20_08_2026", "M5")), cal, regulation="M-C", policy="date", force=False,
                max_age_days=2, today=date(2026, 11, 19))
    assert rep.stored == [] and "M-B" in rep.skipped[0]
    assert any("일 전" in w for w in rep.warnings)


def test_days_to_fetch():
    assert days_to_fetch(None, date(2026, 11, 19), 7) == 7
    assert days_to_fetch(date(2026, 11, 18), date(2026, 11, 19), 7) == 2
    assert days_to_fetch(date(2026, 1, 1), date(2026, 11, 19), 7) == 31
