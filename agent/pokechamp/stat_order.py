"""능력치 표기 규칙: 어디서든 H·A·B·C·D·S 순서와 글자로 보인다 (데이터 키 순서와 상관없이).

H=HP, A=공격, B=방어, C=특공, D=특방, S=스피드.
"""

from __future__ import annotations

from typing import Any

STAT_ORDER = ("hp", "atk", "def", "spa", "spd", "spe")
_LETTER = dict(zip(STAT_ORDER, "HABCDS"))


def stat_letter(key: str) -> str:
    return _LETTER.get(key, key)


def ordered(d: dict[str, Any]) -> list[tuple[str, Any]]:
    """H·A·B·C·D·S 순서로 (키, 값). 모르는 키는 뒤에 둔다."""
    return [(k, d[k]) for k in STAT_ORDER if k in d] + [(k, v) for k, v in d.items() if k not in STAT_ORDER]


def sp_text(evs: dict[str, Any]) -> str:
    """Stat Points 한 줄: 'H2 A32 S32' (0은 뺀다)."""
    return " ".join(f"{stat_letter(k)}{v}" for k, v in ordered(evs or {}) if v)
