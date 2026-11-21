"""확정 n타 · 난수 n타 계산 (계산기 탭).

엔진 /damage가 주는 난수 16단계를 매번 독립·균등으로 보고, n번 사용한 데미지 합이 방어측 HP 이상일 확률을 정확히 구한다.
연속기는 한 번 사용 = 타마다 난수 하나씩의 합이다. n번 사이의 회복·잔여 데미지는 넣지 않는다.
"""

from __future__ import annotations

from collections import defaultdict


def _convolve(a: dict[int, float], b: dict[int, float], cap: int) -> dict[int, float]:
    out: dict[int, float] = defaultdict(float)
    for x, px in a.items():
        for y, py in b.items():
            out[min(cap, x + y)] += px * py      # HP 이상은 하나로 묶어 상태 수를 줄인다
    return out


def _pct(p: float) -> str:
    """0.0%·100.0%로 보이면 '확률 없음·확정'으로 오해하므로 양 끝을 따로 표시한다."""
    if p < 0.0005:
        return "<0.1%"
    if p > 0.9995:
        return ">99.9%"
    return f"{p * 100:.1f}%"


def ko_chances(rolls: list[int], hp: int, hits: int = 1, max_n: int = 10) -> dict:
    """모든 타의 난수가 같을 때 (단타, 일반 연속기)."""
    return ko_chances_hits([rolls] * max(1, hits), hp, max_n)


def ko_chances_hits(hit_rolls: list[list[int]], hp: int, max_n: int = 10) -> dict:
    """한 번 사용 = 타마다 난수 16단계 하나씩의 합 (트리플악셀·부자유친처럼 타마다 다를 수 있다)."""
    hp = max(1, int(hp))
    use = {0: 1.0}
    for rolls in hit_rolls:
        one: dict[int, float] = defaultdict(float)
        for r in rolls:
            one[min(hp, int(r))] += 1 / len(rolls)
        use = _convolve(use, one, hp)
    out = {"random_n": None, "random_p": None, "guaranteed_n": None, "per_n": [], "label": ""}
    if max((r for rolls in hit_rolls for r in rolls), default=0) <= 0:
        out["label"] = "데미지 없음"
        return out
    total = {0: 1.0}
    for n in range(1, max_n + 1):
        total = _convolve(total, use, hp)
        p = total.get(hp, 0.0)
        p = 1.0 if p > 1 - 1e-12 else p
        out["per_n"].append({"n": n, "p": p})
        if p > 0 and out["random_n"] is None:
            out["random_n"], out["random_p"] = n, p
        if p >= 1.0:
            out["guaranteed_n"] = n
            break
    rn, gn = out["random_n"], out["guaranteed_n"]
    if rn is None:
        out["label"] = f"{max_n}타 안에 못 잡음"
    elif rn == gn:
        out["label"] = f"확정 {gn}타"
    else:
        out["label"] = f"난수 {rn}타 ({_pct(out['random_p'])})" + (f" · 확정 {gn}타" if gn else "")
    return out
