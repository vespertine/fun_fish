"""Registre des personnalités disponibles."""

from __future__ import annotations

import random

from fun_fish.personalities.attacker import Attacker
from fun_fish.personalities.base import Candidate, Decision, Personality
from fun_fish.personalities.blitz import Blitz
from fun_fish.personalities.buccaneer import Buccaneer
from fun_fish.personalities.corsair import Corsair
from fun_fish.personalities.council import Duo
from fun_fish.personalities.ancien import Ancien
from fun_fish.personalities.funfish import FunFish, FunFish2, FunFish2Theory, FunFishTheory
from fun_fish.personalities.hustler import Hustler
from fun_fish.personalities.reference import Casual, PureStockfish
from fun_fish.personalities.romantic import Romantic

PERSONALITIES: dict[str, type[Personality]] = {cls.key: cls for cls in (Blitz, Hustler, Attacker, Romantic, Corsair, Buccaneer, FunFish, FunFish2, FunFishTheory, FunFish2Theory, Ancien, Duo, Casual, PureStockfish)}
DEFAULT_PERSONALITY = Blitz.key
PUBLIC_PERSONALITIES = (FunFish.key,)  # celles du mode jeu


def create(key: str, rng: random.Random | None = None, **params) -> Personality:
    """Instancie une personnalité ; `params` surcharge ses réglages (tolerance=150, depth=6...)."""
    try:
        cls = PERSONALITIES[key]
    except KeyError:
        raise ValueError(f"Personnalité inconnue : {key!r} (connues : {', '.join(PERSONALITIES)})") from None
    types = {spec["name"]: spec["type"] for spec in cls.param_specs()}
    unknown = set(params) - set(types)
    if unknown:
        raise ValueError(f"Réglage inconnu pour {key} : {', '.join(sorted(unknown))}")
    try:
        params = {k: int(round(float(v))) if types[k] == "int" else float(v) for k, v in params.items()}
    except (TypeError, ValueError):
        raise ValueError(f"Valeur non numérique dans les réglages de {key}") from None
    try:
        return cls(rng=rng, **params)
    except TypeError as exc:
        raise ValueError(f"Réglage invalide pour {key} : {exc}") from None


def catalogue() -> list[dict]:
    return [{"key": cls.key, "name": cls.name, "description": cls.description, "params": cls.param_specs()}
            for cls in PERSONALITIES.values()]


__all__ = ["Candidate", "Decision", "Personality", "PERSONALITIES", "DEFAULT_PERSONALITY", "PUBLIC_PERSONALITIES", "create", "catalogue"]
