"""Contrat commun des personnalités."""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Sequence

import chess

if TYPE_CHECKING:
    from fun_fish.analysis import Analyser, Line


@dataclass
class Candidate:
    """Un coup envisagé, avec son éval du point de vue du camp qui joue."""

    move: chess.Move
    san: str
    score: int
    mate: int | None = None
    probability: float | None = None  # probabilité de tirage ; None = écarté
    note: str = ""

    @classmethod
    def from_line(cls, board: chess.Board, line: "Line", **kwargs) -> "Candidate":
        return cls(move=line.move, san=board.san(line.move), score=line.score, mate=line.mate, **kwargs)


@dataclass
class Decision:
    """Le coup retenu par une personnalité, et pourquoi."""

    move: chess.Move
    san: str
    color: chess.Color  # camp qui joue le coup
    explanation: str
    candidates: list[Candidate] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Sérialise avec les évals du point de vue des Blancs, comme dans toutes les interfaces d'échecs."""
        sign = 1 if self.color == chess.WHITE else -1
        return {
            "uci": self.move.uci(),
            "san": self.san,
            "explanation": self.explanation,
            "candidates": [
                {
                    "uci": c.move.uci(),
                    "san": c.san,
                    "score": sign * c.score,
                    "mate": None if c.mate is None else sign * c.mate,
                    "probability": c.probability,
                    "note": c.note,
                    "chosen": c.move == self.move,
                }
                for c in self.candidates
            ],
        }


class Personality(ABC):
    key: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]

    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()

    @abstractmethod
    def choose_move(self, board: chess.Board, analyser: "Analyser") -> Decision:
        """Choisit un coup pour le camp au trait. Ne doit pas modifier `board`."""


def softmax(scores: Sequence[float], temperature: float) -> list[float]:
    """Probabilités proportionnelles à exp(score / température). Température ≤ 0 : argmax."""
    if not scores:
        return []
    if temperature <= 0:
        best = max(range(len(scores)), key=scores.__getitem__)
        return [1.0 if i == best else 0.0 for i in range(len(scores))]
    top = max(scores)
    weights = [math.exp((s - top) / temperature) for s in scores]
    total = sum(weights)
    return [w / total for w in weights]


def pick(probabilities: Sequence[float], rng: random.Random) -> int:
    return rng.choices(range(len(probabilities)), weights=probabilities, k=1)[0]


def format_score(score: int, mate: int | None) -> str:
    if mate is not None:
        return f"#{mate}" if mate > 0 else f"#-{-mate}"
    return f"{score / 100:+.2f}"
