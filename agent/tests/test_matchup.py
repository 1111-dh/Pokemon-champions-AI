"""1:1 상성 캐시 테스트 (가짜 엔진 클라이언트)."""

import pytest

from pokechamp.data.db import connect
from pokechamp.data.static.update import invalidate_matchup_cache
from pokechamp.matchup.fast import MatchupCache, canonical, set_key

A = {"species": "Garchomp", "item": "Choice Scarf", "ability": "Rough Skin", "nature": "Jolly",
     "evs": {"hp": 2, "atk": 32, "spe": 32, "def": 0}, "moves": ["Outrage", "Earthquake"]}
B = {"species": "Primarina", "item": "Sitrus Berry", "ability": "Torrent", "nature": "Modest",
     "evs": {"hp": 32, "spa": 32}, "moves": ["Moonblast"]}


class FakeClient:
    def __init__(self):
        self.calls = 0

    def batch(self, path, items):
        assert path == "/matchup_fast"
        self.calls += len(items)
        return [{"ok": True, "result": {"score": 0.3 if it["a"]["set"]["species"] == "Garchomp" else 0.7}} for it in items]


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.sqlite")
    c.execute("INSERT INTO static_versions (applied_at, regulation, showdown_commit, format, active) VALUES ('x','M-C','c','f',1)")
    c.commit()
    return c


def test_key_is_canonical():
    shuffled = {**A, "moves": ["earthquake", "outrage"], "evs": {"spe": 32, "atk": 32, "hp": 2}, "item": "choicescarf"}
    assert set_key(A) == set_key(shuffled)
    assert set_key(A) != set_key(A, mega=True)          # 메가 전후는 별도 키
    assert canonical(A)["evs"] == {"atk": 32, "hp": 2, "spe": 32}


def test_cache_computes_once_and_stores_reverse(conn):
    fake = FakeClient()
    cache = MatchupCache(conn, fake)
    assert cache.score(A, B) == 0.3
    assert cache.score(A, B) == 0.3 and fake.calls == 1      # 캐시 적중
    assert cache.score(B, A) == pytest.approx(0.7) and fake.calls == 1   # 반대 방향은 대칭으로 저장
    cache.scores([((A, False), (B, True)), ((A, False), (B, True))])
    assert fake.calls == 2                                    # 같은 쌍은 한 번만


def test_static_version_change_invalidates(conn):
    fake = FakeClient()
    MatchupCache(conn, fake).score(A, B)
    conn.execute("INSERT INTO static_versions (applied_at, regulation, showdown_commit, format, active) VALUES ('y','M-C','d','f',0)")
    conn.execute("UPDATE static_versions SET active = (id = 2)")
    conn.commit()
    MatchupCache(conn, fake).score(A, B)
    assert fake.calls == 2                                    # 새 버전에서는 다시 계산
    invalidate_matchup_cache(conn)
    assert conn.execute("SELECT COUNT(*) FROM matchup_cache").fetchone()[0] == 0
