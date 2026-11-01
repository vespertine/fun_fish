"""Contrat commun des personnalités."""

from __future__ import annotations

import inspect
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
    reality: str | None = None  # verdict d'une analyse profonde, quand la personnalité voit court

    def to_dict(self) -> dict:
        """Sérialise avec les évals du point de vue des Blancs, comme dans toutes les interfaces d'échecs."""
        sign = 1 if self.color == chess.WHITE else -1
        return {
            "uci": self.move.uci(),
            "san": self.san,
            "explanation": self.explanation,
            "reality": self.reality,
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
    PARAM_HELP: ClassVar[dict[str, str]] = {}  # aide affichée dans le panneau de réglages
    DEFAULTS: ClassVar[dict[str, float]] = {}  # profil : réglages par défaut d'une variante

    @classmethod
    def param_specs(cls) -> list[dict]:
        """Réglages de la personnalité, déduits de la signature de son constructeur (ou de celui
        de la personnalité dont elle est une variante, avec ses `DEFAULTS`)."""
        specs = []
        init = next(c.__init__ for c in cls.__mro__ if "__init__" in vars(c) and c.__init__.__code__.co_argcount > 1)
        for name, param in inspect.signature(init).parameters.items():
            if name in ("self", "rng") or param.kind in (param.VAR_KEYWORD, param.VAR_POSITIONAL):
                continue
            default = cls.DEFAULTS.get(name, param.default)
            specs.append({
                "name": name,
                "default": default,
                "type": "int" if isinstance(default, int) else "float",
                "help": cls.PARAM_HELP.get(name, ""),
            })
        return specs

    def params(self) -> dict:
        return {spec["name"]: getattr(self, spec["name"]) for spec in self.param_specs()}

    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()

    @abstractmethod
    def choose_move(self, board: chess.Board, analyser: "Analyser") -> Decision:
        """Choisit un coup pour le camp au trait. Ne doit pas modifier `board`."""


MATE_MOVE_COST = 100  # cp : coût d'un coup de plus pour mater


def regret(best: "Line", line: "Line") -> int:
    """Ce qu'on perd à jouer `line` plutôt que `best` (cp). Entre deux mats, chaque coup de plus
    coûte MATE_MOVE_COST : sinon un mat en 6 vaut presque un mat en 3, et l'agent tourne en rond."""
    if best.mate is not None and best.mate > 0 and line.mate is not None and line.mate > 0:
        return max(0, (line.mate - best.mate) * MATE_MOVE_COST)
    return max(0, best.score - line.score)


@dataclass
class Soundness:
    """Premier étage commun : les coups qui perdent au plus `tolerance` cp si l'adversaire répond juste.

    **Conversion** : quand la position est gagnante (éval ≥ `convert_above` cp, ou mat forcé),
    on revérifie à `convert_depth` avec une tolérance de `convert_tolerance` cp. Les étages
    suivants (attaque, pièges, style) ne choisissent alors qu'entre des coups qui gagnent :
    à faible profondeur, un pion passé qui file à dame reste sinon invisible. 0 = désactivé.
    """

    tolerance: int
    depth: int
    max_candidates: int
    convert_above: int = 500
    convert_depth: int = 14
    convert_tolerance: int = 30

    def lines(self, board: chess.Board, analyser: "Analyser") -> tuple[list["Line"], bool]:
        """(coups sains, le meilleur en premier ; mode conversion ?)."""
        multipv = min(board.legal_moves.count(), self.max_candidates)
        root = analyser.analyse(board, multipv=multipv, depth=self.depth)
        tolerance, converting = self.tolerance, False
        if self.convert_above > 0 and root[0].score >= self.convert_above:
            deep = analyser.analyse(board, multipv=multipv, depth=max(self.depth, self.convert_depth))
            if deep[0].score >= self.convert_above:
                root, tolerance, converting = deep, min(self.tolerance, self.convert_tolerance), True
        best = root[0]
        return [l for l in root if regret(best, l) <= tolerance], converting


CONVERTING = "Position gagnante : il joue sûr pour conclure. "
CONVERT_HELP = {
    "convert_above": "Conversion : au-delà de cette éval (cp), il joue sûr pour conclure (0 = jamais)",
    "convert_depth": "Conversion : profondeur de vérification",
    "convert_tolerance": "Conversion : perte maximale acceptée (centipions)",
}


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
