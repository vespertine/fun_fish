"""Répertoires d'ouvertures : la signature d'une personnalité, avec un peu de hasard.

Un répertoire est une liste de lignes nommées et pondérées. À chaque partie, la personnalité
tire au sort une ligne compatible avec les coups déjà joués et la suit tant que l'adversaire
reste dedans ; s'il en sort, elle en tire une autre qui colle encore, sinon elle joue à sa
façon. Un coup de répertoire n'est qu'un goût de plus : il doit passer le filtre de santé, et
une vraie occasion d'attaque passe devant (voir `Style.bonuses`).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import chess


@dataclass(frozen=True)
class Opening:
    name: str
    moves: tuple[chess.Move, ...]
    weight: float


def _parse(name: str, san: str, weight: float) -> Opening:
    board = chess.Board()
    moves = []
    for token in san.split():
        if token[0].isdigit():
            continue
        move = board.parse_san(token)
        moves.append(move)
        board.push(move)
    return Opening(name, tuple(moves), weight)


# Lignes vérifiées avec Stockfish : chacun de nos coups perd moins de 1,5 pion.
ROMANTIC_WHITE = [
    ("Gambit du roi", "1. e4 e5 2. f4", 3),
    ("Gambit du roi accepté", "1. e4 e5 2. f4 exf4 3. Nf3 g5 4. h4", 1),
    ("Gambit danois", "1. e4 e5 2. d4 exd4 3. c3", 2),
    ("Gambit Evans", "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. b4", 3),
    ("Deux cavaliers, attaque Fegatello", "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Ng5", 2),
    ("Gambit écossais", "1. e4 e5 2. Nf3 Nc6 3. d4 exd4 4. Bc4", 2),
    ("Gambit viennois", "1. e4 e5 2. Nc3 Nf6 3. f4", 1),
    ("Gambit Smith-Morra", "1. e4 c5 2. d4 cxd4 3. c3", 3),
    ("Française, attaque Alapine", "1. e4 e6 2. d4 d5 3. Nc3 Nf6 4. Bg5", 1),
    ("Caro-Kann, avance", "1. e4 c6 2. d4 d5 3. e5 Bf5 4. Nc3 e6 5. g4", 2),
    ("Scandinave", "1. e4 d5 2. exd5 Qxd5 3. Nc3", 1),
    ("Alekhine, quatre pions", "1. e4 Nf6 2. e5 Nd5 3. d4 d6 4. c4 Nb6 5. f4", 1),
    ("Pirc, attaque autrichienne", "1. e4 d6 2. d4 Nf6 3. Nc3 g6 4. f4", 1),
    ("Moderne, attaque autrichienne", "1. e4 g6 2. d4 Bg7 3. Nc3 d6 4. f4", 1),
    ("Gambit Blackmar-Diemer", "1. d4 d5 2. e4 dxe4 3. Nc3 Nf6 4. f3", 1),
]

ROMANTIC_BLACK = [
    ("Ouverture ouverte", "1. e4 e5", 3),
    ("Deux cavaliers", "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6", 3),
    ("Contre-attaque Traxler", "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Ng5 Bc5", 1),
    ("Défense Schliemann", "1. e4 e5 2. Nf3 Nc6 3. Bb5 f5", 2),
    ("Écossaise", "1. e4 e5 2. Nf3 Nc6 3. d4 exd4", 1),
    ("Gambit du roi accepté", "1. e4 e5 2. f4 exf4", 2),
    ("Contre-gambit Falkbeer", "1. e4 e5 2. f4 d5", 1),
    ("Sicilienne Najdorf", "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6", 2),
    ("Contre-gambit Albin", "1. d4 d5 2. c4 e5", 3),
    ("Gambit Budapest", "1. d4 Nf6 2. c4 e5", 2),
    ("Gambit Benko", "1. d4 Nf6 2. c4 c5 3. d5 b5", 2),
    ("Contre le Londres", "1. d4 d5 2. Bf4 c5", 1),
    ("Anglaise, en miroir inversé", "1. c4 e5", 2),
    ("Contre 1.Cf3", "1. Nf3 d5", 1),
]

REPERTOIRES = {
    "romantic": {chess.WHITE: [_parse(*o) for o in ROMANTIC_WHITE],
                 chess.BLACK: [_parse(*o) for o in ROMANTIC_BLACK]},
}


class Planner:
    """Suit une ligne du répertoire au fil d'une partie."""

    def __init__(self, openings: dict[chess.Color, list[Opening]], rng: random.Random):
        self.openings = openings
        self.rng = rng
        self.current: Opening | None = None

    def next(self, board: chess.Board) -> tuple[chess.Move, str] | None:
        """Le coup prévu par le répertoire dans cette position, avec le nom de la ligne."""
        if board.root() != chess.Board():
            return None  # partie partie d'une position quelconque
        played = tuple(board.move_stack)
        n = len(played)
        if n < 2:
            self.current = None  # nouvelle partie : nouveau tirage

        def fits(o: Opening) -> bool:
            return len(o.moves) > n and o.moves[:n] == played

        if self.current is None or not fits(self.current):
            pool = [o for o in self.openings.get(board.turn, []) if fits(o)]
            if not pool:
                self.current = None
                return None
            self.current = self.rng.choices(pool, weights=[o.weight for o in pool])[0]
        return self.current.moves[n], self.current.name
