"""사전확률 모듈 테스트."""

from datetime import date

import pytest

from pokechamp.data.db import connect
from pokechamp.data.priors import Priors

CFG = {"half_life_days": 2.0, "stale_after_days": 2, "trend_min_gap_days": 3, "trend_max_gap_days": 14,
       "previous_regulation_weight": 0.2, "rank_weight_exponent": 1.0}


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.sqlite")
    c.execute("INSERT INTO static_versions (applied_at, regulation, showdown_commit, format, active) VALUES ('x','M-C','c','f',1)")
    c.commit()
    return c


def snap(c, d, reg="M-C", items=None, ranks=None, species="garchomp"):
    sid = c.execute("INSERT INTO snapshots (captured_at, source, format, regulation, official_season, page_season,"
                    " page_updated_at, verified) VALUES ('now','src','singles',?,?,?,?,1)", (reg, "M-8", "M7", d)).lastrowid
    for i, (item, rate) in enumerate((items or {}).items()):
        c.execute("INSERT INTO usage_items VALUES (?,?,?,?,?)", (sid, species, item, i + 1, rate))
    for s, r in (ranks or {}).items():
        c.execute("INSERT INTO usage_species VALUES (?,?,?,NULL)", (sid, s, r))
    c.commit()
    return sid


def test_decay_by_elapsed_days_not_snapshot_count(conn):
    # 오래된 스냅샷 3개(스카프)와 최신 1개(메가스톤). 개수가 아니라 경과 일수로 가중치를 준다.
    for d in ("2026-11-01", "2026-11-02", "2026-11-03"):
        snap(conn, d, items={"choicescarf": 1.0})
    snap(conn, "2026-11-19", items={"garchompitez": 1.0})
    p = Priors(conn, today=date(2026, 11, 19), cfg=CFG)
    d = p.marginal("garchomp", "items")
    assert d.values[0][0] == "garchompitez" and d.values[0][1] > 0.99
    assert d.freshness.latest_date == "2026-11-19" and d.freshness.age_days == 0
    assert sum(pr for _, pr in d.values) == pytest.approx(1.0)


def test_equal_age_snapshots_average(conn):
    snap(conn, "2026-11-19", items={"choicescarf": 0.6, "focussash": 0.4})
    p = Priors(conn, today=date(2026, 11, 19), cfg=CFG)
    d = p.marginal("garchomp", "items")
    assert d.prob("choicescarf") == pytest.approx(0.6) and d.other == pytest.approx(0.0)


def test_regulation_filter_and_previous_fallback(conn):
    snap(conn, "2026-08-20", reg="M-B", items={"lifeorb": 1.0}, species="garchomp")
    snap(conn, "2026-11-19", reg="M-C", items={"choicescarf": 1.0}, species="primarina")
    p = Priors(conn, today=date(2026, 11, 19), cfg=CFG)
    g = p.marginal("garchomp", "items")      # 현재 레귤레이션 자료 없음 → 이전 레귤레이션, 표시
    assert g.values[0][0] == "lifeorb" and g.freshness.previous_regulation
    assert "이전 레귤레이션" in g.freshness.label()
    pr = p.marginal("primarina", "items")
    assert not pr.freshness.previous_regulation
    assert p.marginal("nobody", "items").fallback


def test_stale_warning(conn):
    snap(conn, "2026-11-15", items={"choicescarf": 1.0})
    assert "4일 전" in Priors(conn, today=date(2026, 11, 19), cfg=CFG).warning()
    assert Priors(conn, today=date(2026, 11, 16), cfg=CFG).warning() is None


def test_species_weights_and_trend(conn):
    snap(conn, "2026-11-10", ranks={"garchomp": 3, "primarina": 1})
    snap(conn, "2026-11-18", ranks={"garchomp": 2, "primarina": 1})
    snap(conn, "2026-11-19", ranks={"garchomp": 1, "primarina": 2})
    p = Priors(conn, today=date(2026, 11, 19), cfg=CFG)
    w = p.species_weights()
    assert list(w) == ["garchomp", "primarina"] and w["garchomp"] == pytest.approx(2 / 3)
    t = p.trend("garchomp")            # 11-18은 간격 1일이라 비교 대상이 아니고 11-10(9일 전)과 비교
    assert t.known and t.rank_before == 3 and t.delta == 2 and t.gap_days == 9


def test_trend_unknown_with_single_snapshot(conn):
    snap(conn, "2026-11-19", ranks={"garchomp": 1})
    t = Priors(conn, today=date(2026, 11, 19), cfg=CFG).trend("garchomp")
    assert not t.known and t.delta is None and t.rank_now == 1


def test_local_meta_blend_and_bring_rates():
    from pokechamp.data.local_meta import blend_weights, bring_rates, bring_stats, species_counts
    pub = {"garchomp": 0.5, "primarina": 0.3, "lucario": 0.2}
    assert blend_weights(pub, {}, 0, 30) == pub                              # 기록이 없으면 공개 통계 그대로
    recs = [[{"species": "dragonite", "brought": True}, {"species": "garchomp", "brought": False}]] * 10
    w = blend_weights(pub, species_counts(recs), len(recs), 30)
    assert abs(sum(w.values()) - 1) < 1e-9
    assert w["dragonite"] > 0 and w["garchomp"] > pub["garchomp"] * 0.75   # 로컬에서 자주 본 종이 생기고 공개 비중은 크게 안 깎인다
    w_many = blend_weights(pub, species_counts(recs * 30), len(recs) * 30, 30)
    assert w_many["dragonite"] > w["dragonite"]                             # 판이 쌓일수록 로컬 비중이 커진다
    r = bring_rates(bring_stats(recs), 0.5, 4)
    assert r["dragonite"] > 0.8 and r["garchomp"] < 0.2
    r1 = bring_rates(bring_stats(recs[:1]), 0.5, 4)
    assert 0.5 < r1["dragonite"] < 0.7                                      # 표본이 적으면 0.5에 가깝다
