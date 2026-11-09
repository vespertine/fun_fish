"""Non-régression du choix de coup : quelques positions réelles, graines fixes, coups attendus.

Partagé par tests/test_regression.py et `python scripts/perfbench.py golden` (qui régénère
tests/data/golden.json quand un changement de comportement est voulu).
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import chess

GOLDEN = Path(__file__).parent / "data" / "golden.json"
PERSONALITY = "fun_fish_2"


def decide(case: dict, stockfish: str) -> dict:
    """Coup et distribution de FF2 dans ce cas : agent neuf, Stockfish neuf (rien ne dépend de l'ordre)."""
    from fun_fish import personalities
    from fun_fish.analysis import Analyser

    board = chess.Board()
    for uci in case["moves"]:
        board.push_uci(uci)
    agent = personalities.create(PERSONALITY, rng=random.Random(case["seed"]), effort=case["effort"])
    with Analyser(stockfish, threads=1, hash_mb=64) as analyser:
        decision = agent.choose_move(board, analyser)
    return {"move": decision.move.uci(),
            "candidates": {c.move.uci(): round(c.probability, 4) for c in decision.candidates}}


def load() -> list[dict]:
    return json.loads(GOLDEN.read_text())
