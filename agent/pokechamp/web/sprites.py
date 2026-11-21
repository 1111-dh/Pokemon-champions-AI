"""Showdown 스프라이트 파일 이름."""

from __future__ import annotations

import re


def sprite_id(name_en: str) -> str:
    """Showdown 스프라이트 파일 이름: 'Garchomp-Mega-Z' → 'garchomp-megaz', 'Tauros-Paldea-Combat' → 'tauros-paldeacombat'"""
    base, _, forme = name_en.partition("-")
    clean = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())  # noqa: E731
    return clean(base) + (f"-{clean(forme)}" if forme else "")
