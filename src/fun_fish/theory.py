"""Surcharge « théorie » : de temps en temps, quelques coups d'une ligne classique, puis liberté totale.

Dans une partie sur `theory_rate`, la personnalité tire au sort combien de ses coups suivront la
théorie (de 1 à `theory_moves`) et une ligne classique compatible avec les coups déjà joués. Elle la
joue d'office, sans goût ni filtre, tant que l'adversaire reste dedans (s'il en sort, une autre
ligne qui colle encore ; sinon un coup dominant des maîtres, ou à défaut des joueurs Lichess ≥ 2200,
tant que la position est assez fréquente chez eux). Ensuite, ou dans les autres parties, elle joue à
sa façon. Indépendant du répertoire (`fun_fish.repertoire`), qui n'est qu'un goût de plus.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import chess

from fun_fish.repertoire import Opening, _parse

# Lignes classiques, à enrichir. Chaque ligne s'arrête sur notre dernier coup théorique.
# Poids : une ligne tirée au départ reste suivie, donc le poids pèse aussi sur nos coups précédents.
# Les variantes d'une même réponse se partagent un poids total d'environ 1 (0,74 / 0,07...) pour ne
# pas favoriser l'ouverture qui les contient.
THEORY_WHITE = [
    ("Italienne, Giuoco piano", "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 5. d4", 2),
    ("Italienne, deux cavaliers 4.d3", "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. d3 Be7 5. O-O", 1),
    ("Espagnole fermée", "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1", 2),
    ("Espagnole, Berlinoise", "1. e4 e5 2. Nf3 Nc6 3. Bb5 Nf6 4. O-O Nxe4 5. d4", 1),
    ("Écossaise", "1. e4 e5 2. Nf3 Nc6 3. d4 exd4 4. Nxd4 Nf6 5. Nxc6 bxc6 6. e5", 1),
    ("Écossaise 4...Fc5", "1. e4 e5 2. Nf3 Nc6 3. d4 exd4 4. Nxd4 Bc5 5. Nxc6 Qf6 6. Qd2", 1),
    ("Petrov, classique", "1. e4 e5 2. Nf3 Nf6 3. Nxe5 d6 4. Nf3 Nxe4 5. d4", 1),
    ("Philidor", "1. e4 e5 2. Nf3 d6 3. d4 exd4 4. Nxd4 Nf6 5. Nc3", 1),
    ("Gambit du roi accepté", "1. e4 e5 2. f4 exf4 3. Nf3 g5 4. h4", 1),
    ("Sicilienne Najdorf, 6.Fg5", "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6 6. Bg5", 2),
    ("Sicilienne Dragon, yougoslave", "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 g6 6. Be3 Bg7 7. f3", 2),
    ("Sicilienne Scheveningen, Keres", "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 e6 6. g4", 1),
    ("Sicilienne Sveshnikov", "1. e4 c5 2. Nf3 Nc6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 e5 6. Ndb5 d6 7. Bg5", 1),
    ("Sicilienne Taimanov", "1. e4 c5 2. Nf3 e6 3. d4 cxd4 4. Nxd4 Nc6 5. Nc3 Qc7 6. Be3", 1),
    ("Française, classique 4.Fg5", "1. e4 e6 2. d4 d5 3. Nc3 Nf6 4. Bg5", 1),
    ("Française Winawer", "1. e4 e6 2. d4 d5 3. Nc3 Bb4 4. e5 c5 5. a3", 1),
    ("Caro-Kann, avance", "1. e4 c6 2. d4 d5 3. e5 Bf5 4. Nf3 e6 5. Be2", 1),
    ("Caro-Kann, classique", "1. e4 c6 2. d4 d5 3. Nc3 dxe4 4. Nxe4 Bf5 5. Ng3 Bg6 6. h4", 1),
    ("Scandinave 3...Da5", "1. e4 d5 2. exd5 Qxd5 3. Nc3 Qa5 4. d4 Nf6 5. Nf3", 1),
    ("Alekhine, moderne", "1. e4 Nf6 2. e5 Nd5 3. d4 d6 4. Nf3", 1),
    ("Pirc, autrichienne", "1. e4 d6 2. d4 Nf6 3. Nc3 g6 4. f4", 1),
    ("Gambit dame refusé, 4.Fg5", "1. d4 d5 2. c4 e6 3. Nc3 Nf6 4. Bg5 Be7 5. e3", 2),
    ("Slave", "1. d4 d5 2. c4 c6 3. Nf3 Nf6 4. Nc3 dxc4 5. a4", 1),
    ("Gambit dame accepté", "1. d4 d5 2. c4 dxc4 3. e4", 1),
    ("Grünfeld, échange", "1. d4 Nf6 2. c4 g6 3. Nc3 d5 4. cxd5 Nxd5 5. e4 Nxc3 6. bxc3 Bg7 7. Nf3", 2),
    ("Est-indienne, classique", "1. d4 Nf6 2. c4 g6 3. Nc3 Bg7 4. e4 d6 5. Nf3 O-O 6. Be2 e5 7. O-O", 2),
    ("Nimzo-indienne, classique", "1. d4 Nf6 2. c4 e6 3. Nc3 Bb4 4. Qc2 O-O 5. a3", 1),
    ("Benoni moderne", "1. d4 Nf6 2. c4 c5 3. d5 e6 4. Nc3 exd5 5. cxd5 d6 6. e4", 1),
    ("Gambit Benko accepté", "1. d4 Nf6 2. c4 c5 3. d5 b5 4. cxb5 a6 5. bxa6", 1),
    ("Contre l'Englund", "1. d4 e5 2. dxe5", 0.74),
    ("Contre l'Englund, gambit du centre 2.e4", "1. d4 e5 2. e4", 0.07),
    ("Contre l'Englund, 2.Cc3", "1. d4 e5 2. Nc3", 0.08),
    ("Contre l'Englund, 2.Cf3", "1. d4 e5 2. Nf3", 0.08),
    ("Contre l'Englund, 2.Fe3 !?", "1. d4 e5 2. Be3", 0.03),
    ("Hollandaise, contre la Leningrad", "1. d4 f5 2. g3 Nf6 3. Bg2 g6 4. Nf3", 1),
]

THEORY_BLACK = [
    ("Sicilienne Najdorf", "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6", 2),
    ("Sicilienne Dragon", "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 g6", 2),
    ("Sicilienne Sveshnikov", "1. e4 c5 2. Nf3 Nc6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 e5", 1),
    ("Sicilienne Kalachnikov", "1. e4 c5 2. Nf3 Nc6 3. d4 cxd4 4. Nxd4 e5 5. Nb5 d6", 1),
    ("Sicilienne Scheveningen", "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 e6", 1),
    ("Sicilienne contre 2.Fc4", "1. e4 c5 2. Bc4 e6", 0.75),
    ("Sicilienne contre 2.Fc4, gambit 2...b5 !?", "1. e4 c5 2. Bc4 b5", 0.25),
    ("Rossolimo 3...e6, 4.Fxc6", "1. e4 c5 2. Nf3 Nc6 3. Bb5 e6 4. Bxc6 bxc6 5. d4 Ne7", 0.20),
    ("Rossolimo 3...e6, 4.O-O", "1. e4 c5 2. Nf3 Nc6 3. Bb5 e6 4. O-O Nge7", 0.25),
    ("Rossolimo 3...Cf6", "1. e4 c5 2. Nf3 Nc6 3. Bb5 Nf6", 0.25),
    ("Rossolimo 3...Cd4 !?", "1. e4 c5 2. Nf3 Nc6 3. Bb5 Nd4", 0.15),
    ("Rossolimo 3...g6", "1. e4 c5 2. Nf3 Nc6 3. Bb5 g6", 0.15),
    # Contre 2.d4 : la reprise 3 fois sur 4, le reste au prorata de ses goûts (d5 25, Cf6 18, g6 11, e6 6)
    ("Sicilienne contre 2.d4, 2...cxd4", "1. e4 c5 2. d4 cxd4", 0.75),
    ("Sicilienne contre 2.d4, 2...d5 !?", "1. e4 c5 2. d4 d5", 0.10),
    ("Sicilienne contre 2.d4, 2...Cf6 !?", "1. e4 c5 2. d4 Nf6", 0.08),
    ("Sicilienne contre 2.d4, 2...g6", "1. e4 c5 2. d4 g6", 0.045),
    ("Sicilienne contre 2.d4, 2...e6", "1. e4 c5 2. d4 e6", 0.025),
    ("Sicilienne contre Alapine", "1. e4 c5 2. c3 Nf6 3. e5 Nd5 4. d4 cxd4", 1),
    ("Sicilienne fermée", "1. e4 c5 2. Nc3 Nc6 3. g3 g6 4. Bg2 Bg7", 1),
    ("Espagnole fermée", "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7", 1),
    ("Espagnole, Berlinoise", "1. e4 e5 2. Nf3 Nc6 3. Bb5 Nf6 4. O-O Nxe4", 1),
    ("Italienne, Giuoco piano", "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6", 1),
    ("Deux cavaliers", "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6", 1),
    ("Écossaise", "1. e4 e5 2. Nf3 Nc6 3. d4 exd4 4. Nxd4 Nf6", 1),
    ("Quatre cavaliers", "1. e4 e5 2. Nf3 Nc6 3. Nc3 Nf6", 1),
    ("Gambit du roi accepté", "1. e4 e5 2. f4 exf4 3. Nf3 g5", 1),
    ("Viennoise", "1. e4 e5 2. Nc3 Nf6", 1),
    ("Française Winawer", "1. e4 e6 2. d4 d5 3. Nc3 Bb4", 1),
    ("Française, avance", "1. e4 e6 2. d4 d5 3. e5 c5 4. c3 Nc6", 1),
    ("Française Tarrasch, 4.exd5 exd5", "1. e4 e6 2. d4 d5 3. Nd2 c5 4. exd5 exd5 5. Ngf3 Nc6 6. Bb5 Bd6 7. dxc5 Bxc5 8. O-O Nge7", 1),
    ("Française Tarrasch, 4.Cgf3 cxd4", "1. e4 e6 2. d4 d5 3. Nd2 c5 4. Ngf3 cxd4 5. Nxd4 Nc6 6. Bb5 Bd7 7. Nxc6 bxc6", 1),
    ("Caro-Kann, avance, 4.Cf3 e6", "1. e4 c6 2. d4 d5 3. e5 Bf5 4. Nf3 e6", 0.62),
    ("Caro-Kann, avance, 4.Cf3 c5", "1. e4 c6 2. d4 d5 3. e5 Bf5 4. Nf3 c5", 0.14),
    ("Caro-Kann, avance, 4.Cf3 Ca6", "1. e4 c6 2. d4 d5 3. e5 Bf5 4. Nf3 Na6", 0.08),
    ("Caro-Kann, avance, 4.Cf3 Cd7", "1. e4 c6 2. d4 d5 3. e5 Bf5 4. Nf3 Nd7", 0.08),
    ("Caro-Kann, avance, 4.Cf3 Dc7", "1. e4 c6 2. d4 d5 3. e5 Bf5 4. Nf3 Qc7", 0.08),
    ("Caro-Kann, classique", "1. e4 c6 2. d4 d5 3. Nc3 dxe4 4. Nxe4 Bf5", 1),
    ("Caro-Kann, échange 3...cxd5", "1. e4 c6 2. d4 d5 3. exd5 cxd5", 0.50),
    ("Caro-Kann, échange 3...Da5+ !?", "1. e4 c6 2. d4 d5 3. exd5 Qa5+", 0.20),
    ("Caro-Kann, échange 3...Cf6 !?", "1. e4 c6 2. d4 d5 3. exd5 Nf6", 0.20),
    ("Caro-Kann, échange 3...Dxd5", "1. e4 c6 2. d4 d5 3. exd5 Qxd5", 0.10),
    ("Petrov", "1. e4 e5 2. Nf3 Nf6 3. Nxe5 d6 4. Nf3 Nxe4", 1),
    ("Grünfeld", "1. d4 Nf6 2. c4 g6 3. Nc3 d5", 2),
    ("Est-indienne", "1. d4 Nf6 2. c4 g6 3. Nc3 Bg7 4. e4 d6", 2),
    ("Nimzo-indienne", "1. d4 Nf6 2. c4 e6 3. Nc3 Bb4", 2),
    ("Ouest-indienne", "1. d4 Nf6 2. c4 e6 3. Nf3 b6", 1),
    ("Benoni moderne", "1. d4 Nf6 2. c4 c5 3. d5 e6", 1),
    ("Gambit Benko", "1. d4 Nf6 2. c4 c5 3. d5 b5", 1),
    ("Hollandaise Leningrad", "1. d4 f5 2. c4 Nf6 3. g3 g6", 1),
    ("Hollandaise Stonewall", "1. d4 f5 2. c4 Nf6 3. g3 e6 4. Bg2 d5", 1),
    ("Hollandaise 2.Cf3, Ilyin-Jenevsky",
     "1. d4 f5 2. Nf3 Nf6 3. g3 e6 4. Bg2 Be7 5. O-O O-O 6. c4 d6 7. Nc3 Qe8", 0.5),
    ("Hollandaise 2.Cf3, Leningrad",
     "1. d4 f5 2. Nf3 Nf6 3. g3 g6 4. Bg2 Bg7 5. O-O O-O 6. c4 d6 7. Nc3 Qe8", 0.5),
    ("Gambit dame refusé", "1. d4 d5 2. c4 e6 3. Nc3 Nf6", 1),
    ("Gambit dame refusé 3.Cf3 Fb4+ (Polgar–Kramnik)", "1. d4 d5 2. c4 e6 3. Nf3 Bb4+", 0.42),
    ("Gambit dame refusé 3.Cf3 Cf6", "1. d4 d5 2. c4 e6 3. Nf3 Nf6", 0.28),
    ("Gambit dame refusé 3.Cf3 Cd7 !?", "1. d4 d5 2. c4 e6 3. Nf3 Nd7", 0.17),
    ("Gambit dame refusé 3.Cf3 c5", "1. d4 d5 2. c4 e6 3. Nf3 c5", 0.08),
    ("Gambit dame refusé 3.Cf3 b5 !?", "1. d4 d5 2. c4 e6 3. Nf3 b5", 0.05),
    ("Slave", "1. d4 d5 2. c4 c6 3. Nf3 Nf6", 1),
    ("Contre le Londres", "1. d4 Nf6 2. Bf4 c5 3. e3 Qb6", 1),
    ("Contre le Londres 1...d5", "1. d4 d5 2. Bf4 c5 3. e3 Nc6", 1),
    ("Contre 2.Cf3, 2...Cf6", "1. d4 d5 2. Nf3 Nf6", 0.30),
    ("Contre 2.Cf3, Tchigorine 3.Ff4 Fg4", "1. d4 d5 2. Nf3 Nc6 3. Bf4 Bg4 4. e3 e6", 0.08),
    ("Contre 2.Cf3, Tchigorine 3.c4 Fg4", "1. d4 d5 2. Nf3 Nc6 3. c4 Bg4", 0.10),
    ("Contre 2.Cf3, Tchigorine 3.c4 e5 !?", "1. d4 d5 2. Nf3 Nc6 3. c4 e5", 0.05),
    ("Contre 2.Cf3, Tchigorine 3.e3 Fg4", "1. d4 d5 2. Nf3 Nc6 3. e3 Bg4", 0.03),
    ("Contre 2.Cf3, Tchigorine 3.e3 Ff5", "1. d4 d5 2. Nf3 Nc6 3. e3 Bf5", 0.01),
    ("Contre 2.Cf3, Tchigorine 3.g3 Fg4, 5.O-O O-O-O",
     "1. d4 d5 2. Nf3 Nc6 3. g3 Bg4 4. Bg2 Qd7 5. O-O O-O-O", 0.02),
    ("Contre 2.Cf3, Tchigorine 3.g3 Fg4, 5.h3 Fxf3",
     "1. d4 d5 2. Nf3 Nc6 3. g3 Bg4 4. Bg2 Qd7 5. h3 Bxf3", 0.01),
    ("Contre 2.Cf3, 2...Ff5", "1. d4 d5 2. Nf3 Bf5", 0.12),
    ("Contre 2.Cf3, 2...e5 !?", "1. d4 d5 2. Nf3 e5", 0.10),
    ("Contre 2.Cf3, 2...c5", "1. d4 d5 2. Nf3 c5", 0.10),
    ("Contre 2.Cf3, 2...c6", "1. d4 d5 2. Nf3 c6", 0.04),
    ("Contre 2.Cf3, 2...e6", "1. d4 d5 2. Nf3 e6", 0.04),
    ("Anglaise, sicilienne inversée", "1. c4 e5 2. Nc3 Nf6 3. g3 d5", 1),
    ("Anglo-indienne", "1. c4 Nf6 2. Nc3 e6 3. e4 d5", 1),
    ("Contre la Réti", "1. Nf3 d5 2. g3 Nf6 3. Bg2 c6", 1),
    ("Contre 1.Cf3 c4", "1. Nf3 Nf6 2. c4 g6 3. Nc3 d5", 1),
]

THEORY = {chess.WHITE: [_parse(*line) for line in THEORY_WHITE],
          chess.BLACK: [_parse(*line) for line in THEORY_BLACK]}

STRONG_ELO = 2200  # « théorie » du livre : ce que jouent les joueurs Lichess de ce niveau et au-delà
STRONG_GAMES = 30  # en deçà, la position est trop rare chez eux : pas de théorie
STRONG_SHARE = 0.2  # coups retenus : au moins cette part de leurs parties


MASTERS_PATH = Path(__file__).parent / "data" / "masters.json.gz"
_masters: dict | None = None


def masters() -> dict:
    """Extrait de la base des maîtres de Lichess (explorateur) : epd -> coup uci -> nombre de parties.
    Seules les positions de notre arbre de théorie y sont (voir runs/masters_gaps.py)."""
    global _masters
    if _masters is None:
        _masters = {}
        if MASTERS_PATH.exists():
            with gzip.open(MASTERS_PATH, "rt") as f:
                _masters = json.load(f)["positions"]
    return _masters


def _dominant(board: chess.Board, counts: dict[str, int]) -> dict[chess.Move, int]:
    total = sum(counts.values())
    if total < STRONG_GAMES:
        return {}
    legal = {m.uci(): m for m in board.legal_moves}
    return {legal[u]: n for u, n in counts.items() if u in legal and n / total >= STRONG_SHARE}


def strong_moves(board: chess.Board) -> dict[chess.Move, int]:
    """Les coups dominants dans cette position : chez les maîtres si elle y est, sinon chez les
    joueurs Lichess ≥ `STRONG_ELO`, avec leur nombre de parties."""
    from fun_fish import book

    found = _dominant(board, masters().get(board.epd(), {}))
    if found:
        return found
    opening = book.shared()
    if opening is None or board.ply() > book.PLIES:
        return {}
    entry = opening.data.get(book.key(board), {})
    counts: dict[str, int] = {}
    for i, low in enumerate(book.BUCKETS):
        if low >= STRONG_ELO:
            for uci, n in entry.get(str(i), {}).items():
                counts[uci] = counts.get(uci, 0) + n
    return _dominant(board, counts)


THEORY_HELP = {
    "theory_rate": "part des parties où il commence par quelques coups de théorie classique (0 : jamais)",
    "theory_moves": "au plus combien de ses coups suivent la théorie (tiré au sort de 1 à ce nombre à chaque partie)",
}


class Theory:
    """À mettre en tête des bases d'une personnalité : elle joue d'abord quelques coups classiques."""

    def __init__(self, theory_rate: float = 0.25, theory_moves: int = 4, **kwargs):
        super().__init__(**kwargs)
        self.theory_rate, self.theory_moves = theory_rate, theory_moves
        self._theory_left = 0
        self._theory_line: Opening | None = None

    @classmethod
    def param_specs(cls) -> list[dict]:
        own = [{"name": "theory_rate", "default": 0.25, "type": "float", "help": THEORY_HELP["theory_rate"]},
               {"name": "theory_moves", "default": 4, "type": "int", "help": THEORY_HELP["theory_moves"]}]
        return own + super().param_specs()

    def theory_move(self, board: chess.Board) -> tuple[chess.Move, str] | None:
        if board.root() != chess.Board():
            return None
        played = tuple(board.move_stack)
        n = len(played)
        if n < 2:  # nouvelle partie : nouveau tirage
            self._theory_line = None
            active = self.rng.random() < self.theory_rate
            self._theory_left = self.rng.randint(1, max(1, self.theory_moves)) if active else 0
        if self._theory_left <= 0:
            return None

        def fits(o: Opening) -> bool:
            return len(o.moves) > n and o.moves[:n] == played

        if self._theory_line is None or not fits(self._theory_line):
            pool = [o for o in THEORY[board.turn] if fits(o)]
            self._theory_line = self.rng.choices(pool, weights=[o.weight for o in pool])[0] if pool else None
        if self._theory_line is not None:
            self._theory_left -= 1
            return self._theory_line.moves[n], self._theory_line.name
        # plus de ligne écrite : le coup dominant des joueurs forts, s'il y en a un
        strong = strong_moves(board)
        if not strong:
            self._theory_left = 0
            return None
        self._theory_left -= 1
        move = self.rng.choices(list(strong), weights=list(strong.values()))[0]
        return move, "théorie des maîtres et des joueurs forts"

    def choose_move(self, board: chess.Board, analyser):
        from fun_fish.personalities.base import Candidate, Decision  # (import circulaire sinon)

        found = self.theory_move(board)
        if found is None:
            return super().choose_move(board, analyser)
        move, name = found
        lines = analyser.analyse(board, multipv=1, depth=10, moves=[move])
        score = lines[0].score if lines else 0
        san = board.san(move)
        explanation = f"Joue {san} : la théorie ({name})."
        candidate = Candidate(move=move, san=san, score=score, probability=1.0, note="théorie")
        return Decision(move=move, san=san, color=board.turn, explanation=explanation, candidates=[candidate])
