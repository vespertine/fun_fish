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

    **Conversion progressive** : plus la position est gagnante, plus il s'applique, sans changer
    de niveau. Entre `convert_from` et `convert_full` cp (ou mat forcé), le degré de conversion
    passe de 0 à 1 : il vérifie jusqu'à `convert_depth` demi-coups plus loin (un pion passé qui
    file à dame doit rester visible) et sa tolérance descend jusqu'à `convert_tolerance`. Le
    style y ajoute une technique de conversion (simplifier, pousser ses pions passés, freiner
    ceux de l'adversaire, activer son roi) : un plan, pas la force de Stockfish.
    """

    tolerance: int
    depth: int
    max_candidates: int
    convert_from: int = 300
    convert_full: int = 800
    convert_depth: int = 4
    convert_tolerance: int = 60

    def degree(self, line: "Line") -> float:
        """Degré de conversion (0 à 1) d'après l'éval du meilleur coup."""
        if self.convert_from <= 0 or self.convert_full <= self.convert_from:
            return 0.0
        return max(0.0, min(1.0, (line.score - self.convert_from) / (self.convert_full - self.convert_from)))

    def lines(self, board: chess.Board, analyser: "Analyser") -> tuple[list["Line"], float]:
        """(coups sains, le meilleur en premier ; degré de conversion)."""
        multipv = min(board.legal_moves.count(), self.max_candidates)
        root = analyser.analyse(board, multipv=multipv, depth=self.depth)
        tolerance, degree = self.tolerance, self.degree(root[0])
        extra = round(degree * self.convert_depth)
        if extra > 0:
            root = analyser.analyse(board, multipv=multipv, depth=self.depth + extra)
            degree = self.degree(root[0])
        if degree > 0 and self.convert_tolerance < self.tolerance:
            tolerance = round(self.tolerance - degree * (self.tolerance - self.convert_tolerance))
        best = root[0]
        return [l for l in root if regret(best, l) <= tolerance], degree


def converting_note(degree: float) -> str:
    return "Avec un net avantage, il cherche à conclure. " if degree >= 0.5 else ""


CONVERT_HELP = {
    "convert_from": "Conversion : éval (cp) à partir de laquelle il commence à jouer pour conclure (0 = jamais)",
    "convert_full": "Conversion : éval (cp) où elle est complète",
    "convert_depth": "Conversion : demi-coups de vérification en plus, au maximum",
    "convert_tolerance": "Conversion : perte maximale acceptée quand elle est complète (centipions)",
    "w_technique": "Conversion : poids de la technique (simplifier, pions passés, roi actif)",
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
