"""Le « fun » d'un coup : son allure, pas sa justesse.

Aucune évaluation de Stockfish ici, seulement l'échiquier (et, si on les a, les probabilités
de Maia). Chaque mesure est en « points de fun », de l'ordre de 0 à 3 :

- **en l'air** : nos pièces laissées en prise par le coup (attaquées par moins fort ou sans
  défense), en valeur sur 3 ;
- **dédain** : nos pièces qui étaient déjà en prise et le restent, sans que le coup s'en occupe ;
- **sans réponse** : l'adversaire vient de prendre et on ne reprend pas, alors qu'on le pourrait ;
- **sacrifice** : la pièce jouée arrive là où elle peut être prise avec profit (ce qu'elle vaut
  moins ce qu'elle prend), sur 3 ;
- **assaut** : un échec, la pression sur le roi adverse qui monte, un pion qui monte à l'assaut
  du roi ;
- **inattendu** : un coup qu'un humain (Maia à l'Elo choisi) ne jouerait presque jamais. Il ne
  compte que si le coup a déjà de l'allure : un coup bizarre et plat n'est pas fun.
"""

from __future__ import annotations

import math

import chess

from fun_fish.style import VALUES, king_pressure

CAP = 1.5  # une dame donnée n'est pas trois fois plus fun qu'une pièce : sinon les gaffes passent devant
FUN_WEIGHTS = ("hanging", "disdain", "unanswered", "sacrifice", "assault", "unexpected")
FUN_LABELS = {"hanging": "il laisse une pièce en l'air", "disdain": "il ignore la menace",
              "unanswered": "il ne reprend pas", "sacrifice": "il sacrifie", "assault": "il monte à l'assaut",
              "unexpected": "personne ne jouerait ça"}


def fun_features(board: chess.Board, move: chess.Move, seen: dict[chess.Move, float] | None = None) -> dict[str, float]:
    us = board.turn
    after = board.copy(stack=False)
    after.push(move)
    moved = after.piece_at(move.to_square)
    before_hanging = _hanging(board, us)
    after_hanging = _hanging(after, us)
    hanging = sum(v for sq, v in after_hanging.items() if sq not in before_hanging and sq != move.to_square)
    disdain = sum(v for sq, v in after_hanging.items() if sq in before_hanging)

    unanswered = 0.0
    last = board.peek() if board.move_stack else None
    if last is not None and _was_capture(board, last):
        recaptures = [m for m in board.legal_moves if m.to_square == last.to_square]
        if recaptures and move.to_square != last.to_square:
            unanswered = 1.0

    sacrifice = 0.0
    if moved is not None and moved.piece_type != chess.KING:
        value = VALUES[moved.piece_type]
        attackers = [VALUES[after.piece_type_at(a)] or 100 for a in after.attackers(not us, move.to_square)]
        defended = bool(after.attackers(us, move.to_square))
        taken = VALUES[board.piece_type_at(move.to_square)] if board.piece_at(move.to_square) else 0
        if move.to_square == board.ep_square and moved.piece_type == chess.PAWN:
            taken = 1
        if attackers:
            exposed = value if not defended else max(0, value - min(attackers))
            sacrifice = max(0.0, exposed - taken) / 3

    assault = 1.0 if after.is_check() else 0.0
    assault += max(0.0, king_pressure(after, us) - king_pressure(board, us)) / 4
    king = board.king(not us)
    if moved is not None and moved.piece_type == chess.PAWN and king is not None:
        rank = chess.square_rank(move.to_square) if us == chess.WHITE else 7 - chess.square_rank(move.to_square)
        if rank >= 3 and abs(chess.square_file(move.to_square) - chess.square_file(king)) <= 2:
            assault += 0.5

    flair = hanging + disdain + unanswered + sacrifice + assault
    unexpected = 0.0
    if seen is not None and flair > 0:
        p = max(seen.get(move, 0.0), 1e-3)
        unexpected = max(0.0, min(2.0, -math.log10(p) - 1))  # 0 à 10 %, 1 à 1 %, 2 à 0,1 %
    hanging, disdain, sacrifice = min(hanging, CAP), min(disdain, CAP), min(sacrifice, CAP)
    return {"hanging": hanging, "disdain": disdain, "unanswered": unanswered, "sacrifice": sacrifice,
            "assault": assault, "unexpected": unexpected}


def _was_capture(board: chess.Board, last: chess.Move) -> bool:
    previous = board.copy(stack=True)
    previous.pop()
    return previous.is_capture(last)


def _hanging(board: chess.Board, color: chess.Color) -> dict[chess.Square, float]:
    """Nos pièces (hors pions et roi) attaquées par moins fort ou sans défense : valeur sur 3."""
    out = {}
    for sq in chess.SquareSet(board.occupied_co[color] & ~board.pawns & ~board.kings):
        target = VALUES[board.piece_type_at(sq)]
        attackers = [VALUES[board.piece_type_at(a)] or 100 for a in board.attackers(not color, sq)]
        if attackers and (min(attackers) < target or not board.attackers(color, sq)):
            out[sq] = target / 3
    return out


def fun_scores(board: chess.Board, weights: dict[str, float],
               seen: dict[chess.Move, float] | None = None) -> dict[chess.Move, tuple[float, str]]:
    """Fun de chaque coup légal (somme pondérée des mesures) et sa raison principale."""
    out = {}
    for move in board.legal_moves:
        features = fun_features(board, move, seen)
        parts = {k: weights.get(k, 0.0) * v for k, v in features.items()}
        main = max(parts, key=parts.get)
        out[move] = (sum(parts.values()), FUN_LABELS[main] if parts[main] > 0 else "")
    return out


__all__ = ["FUN_WEIGHTS", "FUN_LABELS", "fun_features", "fun_scores"]
