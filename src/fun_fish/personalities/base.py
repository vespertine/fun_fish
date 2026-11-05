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
        self.restrict: list[chess.Move] | None = None  # posé par un conseil : ne juger que ces coups

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


ENDGAME_PIECES = 6  # pièces (hors pions et rois) : en deçà, c'est une finale


@dataclass
class Soundness:
    """Premier étage commun : les coups qui perdent au plus `tolerance` cp si l'adversaire répond juste.

    **Conversion progressive** : plus la position est gagnante, plus il s'applique, sans changer
    de niveau. Entre `convert_from` et `convert_full` cp (ou mat forcé), le degré de conversion
    passe de 0 à 1 : il vérifie jusqu'à `convert_depth` demi-coups plus loin (un pion passé qui
    file à dame doit rester visible) et sa tolérance descend jusqu'à `convert_tolerance`. Le
    style y ajoute une technique de conversion (simplifier, pousser ses pions passés, freiner
    ceux de l'adversaire, activer son roi) : un plan, pas la force de Stockfish. En finale
    (au plus `ENDGAME_PIECES` pièces), la vérification plus profonde s'applique toujours.

    **Vision** : si `seen` est donné (probabilité de chaque coup chez un humain d'un certain
    niveau), seuls les coups qu'il jouerait avec au moins `vision_min` de probabilité sont
    envisagés, plus ses `vision_breadth` coups les plus naturels et tous les coups forcés (échecs, prises, promotions), qu'un attaquant regarde
    toujours ; la tolérance se mesure au meilleur d'entre eux. Un coup calme trop « machine »
    (prophylaxie, attente, retraite subtile) passe alors inaperçu, sauf quand ça brûle : si le
    meilleur coup vu perd au moins `vision_panic` cp sur le vrai meilleur, il voit tout.
    **Défense** : de `defend_from` à `defend_full` cp de retard, la tolérance descend comme en
    conversion ; le contre-jeu doit rester sain. `truth` garde le vrai meilleur coup, pour le verdict.
    """

    tolerance: int
    depth: int
    max_candidates: int
    convert_from: int = 300
    convert_full: int = 800
    convert_depth: int = 4
    convert_tolerance: int = 60
    vision_min: float = 0.02
    vision_breadth: int = 6
    vision_panic: int = 150
    defend_from: int = 0
    defend_full: int = 0
    forcing_depth: int = 10
    truth: "Line | None" = None

    def analyse_only(self, board: chess.Board, analyser: "Analyser", only: list[chess.Move]) -> list["Line"]:
        root = analyser.analyse(board, multipv=len(only), depth=self.depth, moves=list(only))
        self.truth = None
        return root

    def forcing_check(self, board: chess.Board, analyser: "Analyser", root: list["Line"]) -> list["Line"]:
        """Échecs et prises revus à `forcing_depth` : un sacrifice qui mate (Dd8+ de Réti) paraît
        perdant à faible profondeur et serait écarté. Restreinte aux coups forcés, l'analyse coûte peu."""
        forcing_moves = [m for m in board.legal_moves if forcing(board, m)]
        if not forcing_moves:
            return root
        deep = analyser.analyse(board, multipv=len(forcing_moves), depth=self.forcing_depth, moves=forcing_moves)
        merged = {line.move: line for line in root}
        merged.update({line.move: line for line in deep})
        return sorted(merged.values(), key=lambda line: -line.score)  # le score encode les mats

    def degree(self, line: "Line") -> float:
        """Degré de conversion (0 à 1) d'après l'éval du meilleur coup."""
        if self.convert_from <= 0 or self.convert_full <= self.convert_from:
            return 0.0
        return max(0.0, min(1.0, (line.score - self.convert_from) / (self.convert_full - self.convert_from)))

    def lines(self, board: chess.Board, analyser: "Analyser",
              seen: dict[chess.Move, float] | None = None,
              only: list[chess.Move] | None = None) -> tuple[list["Line"], float]:
        """(coups sains, le meilleur en premier ; degré de conversion).

        `only` : coups déjà jugés sains par un étage précédent (voir `council.py`) ; ils sont
        tous gardés, sans nouveau filtre ni vision."""
        if only:
            root = self.analyse_only(board, analyser, only)
            return root, self.degree(root[0])
        multipv = min(board.legal_moves.count(), self.max_candidates)
        root = analyser.analyse(board, multipv=multipv, depth=self.depth)
        if self.forcing_depth > self.depth:
            root = self.forcing_check(board, analyser, root)
        visible = None
        if seen:
            likely = sorted(seen, key=seen.get, reverse=True)[: max(1, self.vision_breadth)]
            visible = set(likely) | {m for m in board.legal_moves
                                     if seen.get(m, 0.0) >= self.vision_min or forcing(board, m)}
            self.truth, full = root[0], root
            root = [l for l in root if l.move in visible]
            if len(root) < min(len(visible), multipv):  # coups vus hors des meilleures lignes
                root = analyser.analyse(board, multipv=min(len(visible), multipv), depth=self.depth,
                                        moves=list(visible))
            if self.vision_panic > 0 and self.truth.score - root[0].score >= self.vision_panic:
                root, visible = full, None  # ça brûle : il cherche plus loin que ses coups naturels
        else:
            self.truth = root[0]
        tolerance, degree = self.tolerance, self.degree(root[0])
        extra = round(degree * self.convert_depth)
        if chess.popcount(board.occupied & ~board.pawns & ~board.kings) <= ENDGAME_PIECES:
            extra = self.convert_depth  # finale : vérifier plus loin coûte peu, et les pions passés vont vite
        if extra > 0:
            root = analyser.analyse(board, multipv=multipv if visible is None else min(len(visible), multipv),
                                    depth=self.depth + extra, moves=None if visible is None else list(visible))
            if visible is None:
                self.truth = root[0]
            degree = self.degree(root[0])
        if degree > 0 and self.convert_tolerance < self.tolerance:
            tolerance = round(self.tolerance - degree * (self.tolerance - self.convert_tolerance))
        trouble = defending(root[0].score, self.defend_from, self.defend_full)
        if trouble > 0 and self.convert_tolerance < self.tolerance:  # en difficulté, chaque erreur compte
            tolerance = round(self.tolerance - trouble * (self.tolerance - self.convert_tolerance))
        best = root[0]
        return [l for l in root if regret(best, l) <= tolerance], degree


def forcing(board: chess.Board, move: chess.Move) -> bool:
    """Échec, prise ou promotion : ce qu'un humain regarde d'abord, même quand c'est improbable."""
    return board.is_capture(move) or move.promotion is not None or board.gives_check(move)


def converting_note(degree: float) -> str:
    return "Avec un net avantage, il cherche à conclure. " if degree >= 0.5 else ""


def defending(score: int, start: int, full: int) -> float:
    """Degré de difficulté (0 à 1) : de −`start` à −`full` cp pour le meilleur coup."""
    if start <= 0 or full <= start:
        return 0.0
    return max(0.0, min(1.0, (-score - start) / (full - start)))


CONVERT_HELP = {
    "convert_from": "Conversion : éval (cp) à partir de laquelle il commence à jouer pour conclure (0 = jamais)",
    "convert_full": "Conversion : éval (cp) où elle est complète",
    "convert_depth": "Conversion et finales : demi-coups de vérification en plus, au maximum",
    "convert_tolerance": "Conversion : perte maximale acceptée quand elle est complète (centipions)",
    "w_technique": "Conversion : poids de la technique (simplifier, pions passés, roi actif)",
    "defend_from": "Défense : retard (cp) à partir duquel il cherche du contre-jeu (0 = jamais)",
    "defend_full": "Défense : retard (cp) où la recherche de contre-jeu est complète",
    "w_counterplay": "Défense : poids du contre-jeu (échecs, menaces, roi adverse visé), × degré de difficulté",
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
