"""Ce qui attire l'œil d'un joueur pressé, sans calcul.

Stockfish, même à profondeur 1, n'est pas un bon modèle d'adversaire naïf : il suit toutes
les séquences de captures et son évaluation est surhumaine. Ici on note l'« attrait » d'un
coup avec des indices qu'un humain remarque au premier coup d'œil :

- prendre du matériel (en se méfiant à moitié de la reprise : `caution`) ;
- donner échec, et surtout mater ;
- attaquer une pièce plus grosse ou non défendue, a fortiori deux à la fois (fourchette) ;
- mettre à l'abri une pièce attaquée ;
- ne pas laisser une pièce en prise de façon trop visible (`caution` encore).

L'attrait s'exprime en pions. Il ne regarde que la position juste après le coup.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess

VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


@dataclass(frozen=True)
class Instinct:
    caution: float = 0.5  # part de la reprise / de la pièce en prise qu'il remarque (0 = aveugle, 1 = lucide)
    check: float = 0.6  # attrait d'un échec, en pions
    mate: float = 20.0  # attrait d'un mat en 1
    threat: float = 0.3  # part de la valeur de la plus grosse pièce attaquée
    fork: float = 0.5  # part de la valeur de la deuxième pièce attaquée (fourchette)
    rescue: float = 0.5  # part de la valeur d'une pièce attaquée qu'on met à l'abri

    def appeal(self, board: chess.Board, move: chess.Move) -> float:
        """Attrait du coup `move` pour le camp au trait de `board`, en pions."""
        me, them = board.turn, not board.turn
        piece = board.piece_at(move.from_square)
        assert piece is not None
        own_value = VALUES[piece.piece_type]
        after = board.copy(stack=False)
        after.push(move)

        if after.is_checkmate():
            return self.mate

        appeal = 0.0
        exposed = after.is_attacked_by(them, move.to_square)
        defended = after.is_attacked_by(me, move.to_square)

        if board.is_capture(move):
            victim = board.piece_at(move.to_square)
            gain = VALUES[victim.piece_type] if victim else 1  # None : prise en passant
            if exposed:
                gain -= self.caution * own_value
            appeal += gain
        elif exposed and not defended and piece.piece_type != chess.KING:
            appeal -= self.caution * own_value  # pièce laissée en prise

        if move.promotion:
            appeal += VALUES[move.promotion] - 1
        if after.is_check():
            appeal += self.check

        targets = sorted(
            (VALUES[target.piece_type] for square in after.attacks(move.to_square)
             if (target := after.piece_at(square)) is not None and target.color == them
             and target.piece_type != chess.KING
             and (VALUES[target.piece_type] > own_value or not after.is_attacked_by(them, square))),
            reverse=True,
        )
        if targets:
            appeal += self.threat * targets[0]
        if len(targets) >= 2:
            appeal += self.fork * targets[1]

        if (piece.piece_type not in (chess.PAWN, chess.KING) and board.is_attacked_by(them, move.from_square)
                and not exposed):
            appeal += self.rescue * own_value
        return appeal

    def appeals(self, board: chess.Board) -> dict[chess.Move, float]:
        return {move: self.appeal(board, move) for move in board.legal_moves}
