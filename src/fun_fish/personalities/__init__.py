"""Registre des personnalités disponibles."""

from __future__ import annotations

import random

from fun_fish.personalities.base import Candidate, Decision, Personality
from fun_fish.personalities.blitz import Blitz
from fun_fish.personalities.reference import Casual, PureStockfish

PERSONALITIES: dict[str, type[Personality]] = {cls.key: cls for cls in (Blitz, Casual, PureStockfish)}
DEFAULT_PERSONALITY = Blitz.key


def create(key: str, rng: random.Random | None = None) -> Personality:
    try:
        cls = PERSONALITIES[key]
    except KeyError:
        raise ValueError(f"Personnalité inconnue : {key!r} (connues : {', '.join(PERSONALITIES)})") from None
    return cls(rng=rng)


def catalogue() -> list[dict]:
    return [{"key": cls.key, "name": cls.name, "description": cls.description} for cls in PERSONALITIES.values()]


__all__ = ["Candidate", "Decision", "Personality", "PERSONALITIES", "DEFAULT_PERSONALITY", "create", "catalogue"]
