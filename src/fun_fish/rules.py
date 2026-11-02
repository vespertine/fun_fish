"""Fin de partie, comme sur les sites de jeu en ligne."""

from __future__ import annotations

import chess


def outcome(board: chess.Board) -> chess.Outcome | None:
    """Mat, pat, matériel insuffisant, triple répétition ou 50 coups, appliqués automatiquement.

    `board.outcome(claim_draw=True)` de python-chess déclare aussi la nulle quand le camp au
    trait *pourrait* provoquer une triple répétition par son prochain coup : la partie
    s'arrêtait alors qu'il avait d'autres coups, souvent gagnants. Ici, la répétition doit
    avoir eu lieu.
    """
    result = board.outcome(claim_draw=False)
    if result is not None:
        return result
    if board.is_repetition(3):
        return chess.Outcome(chess.Termination.THREEFOLD_REPETITION, None)
    if board.halfmove_clock >= 100:
        return chess.Outcome(chess.Termination.FIFTY_MOVES, None)
    return None
