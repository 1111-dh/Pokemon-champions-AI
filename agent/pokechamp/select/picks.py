"""선출 열거 (PROJECT_PLAN 8절 1번, 5.1절).

선출 = 6마리 중 3마리 + 선봉. 슬롯 번호(0부터)로 (선봉, 나머지 둘)을 들고, 나머지 둘은 슬롯 순서로 둔다.
앞 포켓몬이 쓰러진 뒤 나오는 순서는 엔진 봇이 정하므로(무작위) 선봉만 구분한다 → 6 × C(5,2) = 60가지.
내 쪽은 메가스톤 소지자 수를 제한하고, 상대 쪽은 제한하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations


@dataclass(frozen=True)
class Pick:
    lead: int
    rest: tuple[int, int]

    @property
    def order(self) -> tuple[int, int, int]:
        return (self.lead, *self.rest)

    @property
    def members(self) -> frozenset[int]:
        return frozenset(self.order)

    def choice(self) -> str:
        """Showdown 팀 프리뷰 선택 문자열. 첫 번호가 선봉이다 (엔진에서 확인: 'team 523' → 5번 선봉, 5·2·3번 출전)."""
        return "team " + "".join(str(i + 1) for i in self.order)


def enumerate_picks(n: int, holders: set[int] | None = None, max_mega: int | None = None) -> list[Pick]:
    """n마리 팀의 선출 목록. max_mega가 있으면 메가스톤 소지자(holders)가 그보다 많은 선출을 뺀다.
    제한 때문에 남는 선출이 없으면(소지자가 너무 많은 팀) 제한 없이 돌려준다 (경고는 호출한 쪽이 낸다)."""
    if n < 3:
        raise ValueError(f"선출하려면 3마리 이상이 필요하다 (지금 {n}마리)")
    holders = holders or set()
    out: list[Pick] = []
    for lead in range(n):
        others = [i for i in range(n) if i != lead]
        for rest in combinations(others, 2):
            out.append(Pick(lead, rest))
    if max_mega is None:
        return out
    limited = [p for p in out if len(p.members & holders) <= max_mega]
    return limited or out


def mega_slot(p: Pick, holders: set[int]) -> int | None:
    """그 선출에서 메가진화하는 슬롯: 출전 순서상 첫 소지자 (배틀당 1회, 최대 데미지 봇은 처음 나온 소지자로 메가진화한다)."""
    for i in p.order:
        if i in holders:
            return i
    return None
