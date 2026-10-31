"""Lecture « humaine » d'une ligne de Stockfish.

Au lieu de comparer des centipions, on traduit chaque ligne en notions qu'un joueur
manipule : mat en N, bilan matériel (« gagne un fou », « gagne la qualité »),
niveau d'avantage (égal, léger, net, décisif) et quelques signaux dérivés (sacrifice).

Le bilan matériel se lit en rejouant la variation principale. Il est mesuré sur une
position « posée », c'est-à-dire où la ligne ne continue pas par une prise :
compter au milieu d'un échange ne veut rien dire. L'horizon règle jusqu'où on lit :

- `horizon=None` : toute la variation (lecture approfondie) ;
- `horizon=N` : on regarde N demi-coups, puis on termine l'échange en cours
  (lecture court-termiste, celle d'un joueur pressé).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence

import chess

from fun_fish.analysis import Line

PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}

_NAMES = {  # (singulier, pluriel, féminin)
    chess.PAWN: ("pion", "pions", False),
    chess.KNIGHT: ("cavalier", "cavaliers", False),
    chess.BISHOP: ("fou", "fous", False),
    chess.ROOK: ("tour", "tours", True),
    chess.QUEEN: ("dame", "dames", True),
}
_NUMBERS = {2: "deux", 3: "trois", 4: "quatre", 5: "cinq", 6: "six", 7: "sept", 8: "huit"}

# Seuils en centipions (point de vue du camp qui joue). Stockfish normalise son éval
# pour que +1,00 corresponde à environ 50 % de chances de gain.
LEVEL_THRESHOLDS = (30, 80, 200)
LEVEL_LABELS = {
    3: "décisif",
    2: "net avantage",
    1: "léger avantage",
    0: "égalité",
    -1: "léger désavantage",
    -2: "net désavantage",
    -3: "perdant",
}
VISIBLE_MATE = 3  # au-delà, le mat est « compliqué »


def describe_pieces(pieces: Iterable[chess.PieceType]) -> str:
    """[FOU, PION, PION] -> « un fou et deux pions »."""
    counts = Counter(pieces)
    parts = []
    for piece in sorted(counts, key=lambda p: -PIECE_VALUES[p]):
        n = counts[piece]
        singular, plural, feminine = _NAMES[piece]
        if n == 1:
            parts.append(("une " if feminine else "un ") + singular)
        else:
            parts.append(f"{_NUMBERS.get(n, str(n))} {plural}")
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " et " + parts[-1]


@dataclass(frozen=True)
class MaterialChange:
    """Bilan matériel net, du point de vue du camp qui joue le premier coup de la ligne."""

    gained: tuple[chess.PieceType, ...] = ()  # pièces adverses disparues (en net)
    lost: tuple[chess.PieceType, ...] = ()  # pièces perdues (en net)
    plies: int = 0  # demi-coup de la ligne où le bilan a été lu

    @property
    def value(self) -> int:
        """En pions : 1 / 3 / 3 / 5 / 9."""
        return sum(PIECE_VALUES[p] for p in self.gained) - sum(PIECE_VALUES[p] for p in self.lost)

    @property
    def is_exchange_win(self) -> bool:
        return self.gained == (chess.ROOK,) and self.lost in ((chess.KNIGHT,), (chess.BISHOP,))

    @property
    def is_exchange_loss(self) -> bool:
        return self.lost == (chess.ROOK,) and self.gained in ((chess.KNIGHT,), (chess.BISHOP,))

    @property
    def label(self) -> str:
        gained, lost = describe_pieces(self.gained), describe_pieces(self.lost)
        if not self.gained and not self.lost:
            return "matériel égal"
        if self.is_exchange_win:
            return "gagne la qualité"
        if self.is_exchange_loss:
            return "perd la qualité"
        if not self.lost:
            return f"gagne {gained}"
        if not self.gained:
            return f"perd {lost}"
        if self.value == 0:
            return f"échange {lost} contre {gained}"
        if self.value > 0:
            return f"gagne {gained} contre {lost}"
        return f"donne {lost} contre {gained}"


def _counts(board: chess.Board, color: chess.Color) -> dict[chess.PieceType, int]:
    return {p: len(board.pieces(p, color)) for p in PIECE_VALUES}


def material_along(board: chess.Board, pv: Sequence[chess.Move], horizon: int | None = None) -> MaterialChange:
    """Bilan matériel le long de `pv` pour le camp au trait de `board`."""
    if horizon is not None and horizon < 1:
        raise ValueError("horizon doit valoir au moins 1 demi-coup")
    me = board.turn
    b = board.copy(stack=False)
    snapshots = [(_counts(b, me), _counts(b, not me))]
    is_capture: list[bool] = []
    for move in pv:
        if not b.is_legal(move):
            break
        is_capture.append(b.is_capture(move) or move.promotion is not None)
        b.push(move)
        snapshots.append((_counts(b, me), _counts(b, not me)))
    n = len(snapshots) - 1

    def settled(i: int) -> bool:
        # La ligne ne continue pas par une prise depuis cette position ;
        # en fin de ligne, on exige que le dernier coup n'ait pas été une prise.
        return (not is_capture[i]) if i < n else (n == 0 or not is_capture[n - 1])

    if horizon is None:
        index = next((i for i in range(n, -1, -1) if settled(i)), 0)
    else:
        start = min(horizon, n)
        index = next((i for i in range(start, n + 1) if settled(i)),
                     next((i for i in range(start, -1, -1) if settled(i)), 0))

    (mine0, theirs0), (mine1, theirs1) = snapshots[0], snapshots[index]
    gained, lost = [], []
    for piece in PIECE_VALUES:
        net = (mine1[piece] - mine0[piece]) - (theirs1[piece] - theirs0[piece])
        (gained if net > 0 else lost).extend([piece] * abs(net))
    return MaterialChange(tuple(gained), tuple(lost), plies=index)


def eval_level(score: int, mate: int | None) -> int:
    """Niveau d'avantage de -3 (perdant) à +3 (décisif), point de vue du camp qui joue."""
    if mate is not None:
        return 3 if mate > 0 else -3
    magnitude = sum(abs(score) >= t for t in LEVEL_THRESHOLDS)
    return magnitude if score > 0 else -magnitude


@dataclass(frozen=True)
class Reading:
    move: chess.Move
    san: str
    score: int
    mate: int | None
    level: int
    material: MaterialChange

    @property
    def level_label(self) -> str:
        return LEVEL_LABELS[self.level]

    @property
    def mate_label(self) -> str | None:
        if self.mate is None:
            return None
        return f"mat en {self.mate}" if self.mate > 0 else f"se fait mater en {-self.mate}"

    @property
    def visible_mate(self) -> bool:
        return self.mate is not None and 0 < self.mate <= VISIBLE_MATE

    @property
    def sacrifice(self) -> bool:
        """Lâche au moins la qualité sans que la position en souffre."""
        return self.material.value <= -2 and self.level >= 0

    @property
    def summary(self) -> str:
        if self.mate is not None:
            complicated = self.mate > VISIBLE_MATE
            return self.mate_label + (" (compliqué)" if complicated else "")
        material = self.material.label
        if self.sacrifice:
            material = "sacrifie " + describe_pieces(self.material.lost)
            if self.material.gained:
                material += " contre " + describe_pieces(self.material.gained)
        return f"{material} · {self.level_label}"


def read_line(board: chess.Board, line: Line, horizon: int | None = None) -> Reading:
    return Reading(
        move=line.move,
        san=board.san(line.move),
        score=line.score,
        mate=line.mate,
        level=eval_level(line.score, line.mate),
        material=material_along(board, line.pv, horizon),
    )


def read_lines(board: chess.Board, lines: Iterable[Line], horizon: int | None = None) -> list[Reading]:
    return [read_line(board, line, horizon) for line in lines]
