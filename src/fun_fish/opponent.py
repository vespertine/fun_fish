"""Modèles d'adversaire : la probabilité que l'adversaire (qui a le trait) joue chaque coup.

Deux modèles, partagés par les personnalités qui raisonnent sur les erreurs humaines :

- `NaiveModel` : instinct (prises, échecs, fourchettes, pièces à sauver ; voir `naive.py`)
  mélangé à l'éval de Stockfish à faible profondeur, passé dans un softmax ;
- `MaiaModel` : Maia-2 à un Elo donné (dépendance optionnelle `sim`).
"""

from __future__ import annotations

from typing import Protocol

import chess

from fun_fish.analysis import Analyser
from fun_fish.naive import Instinct


class OpponentModel(Protocol):
    def probabilities(self, position: chess.Board, analyser: Analyser) -> dict[chess.Move, float]: ...

    def prefetch(self, positions: list[chess.Board]) -> None:
        """Prépare plusieurs positions d'un coup (utile pour Maia, qui travaille en lot)."""


class NaiveModel:
    def __init__(self, instinct: float = 0.7, caution: float = 0.5, shallow_depth: int = 1, naivety: float = 60.0):
        self.instinct = instinct  # 1 = pur instinct, 0 = pur Stockfish
        self.caution = caution  # attention aux reprises et pièces en prise
        self.shallow_depth = shallow_depth  # Stockfish voit de 1 à N demi-coups
        self.naivety = naivety  # cp : plus c'est haut, plus il joue au hasard

    def probabilities(self, position: chess.Board, analyser: Analyser) -> dict[chess.Move, float]:
        from fun_fish.personalities.base import softmax  # import local : personalities importe ce module

        appeal = {m: 100 * a for m, a in Instinct(caution=self.caution).appeals(position).items()}
        moves = list(appeal)
        if self.shallow_depth < 1 or self.instinct >= 1:
            return dict(zip(moves, softmax([appeal[m] for m in moves], self.naivety)))
        result = dict.fromkeys(moves, 0.0)
        for depth in range(1, self.shallow_depth + 1):
            shallow = {l.move: l.score for l in analyser.analyse(position, multipv=len(moves), depth=depth)}
            scores = [self.instinct * appeal[m] + (1 - self.instinct) * shallow.get(m, min(shallow.values()))
                      for m in moves]
            for m, p in zip(moves, softmax(scores, self.naivety)):
                result[m] += p / self.shallow_depth
        return result

    def prefetch(self, positions: list[chess.Board]) -> None:
        pass

    def label(self) -> str:
        return "À courte vue, il"


class MaiaModel:
    def __init__(self, elo: int):
        from fun_fish.sim import maia

        self.elo = elo
        self.maia = maia.shared()

    def probabilities(self, position: chess.Board, analyser: Analyser) -> dict[chess.Move, float]:
        return self.maia.probabilities(position, self.elo)

    def prefetch(self, positions: list[chess.Board]) -> None:
        self.maia.probabilities_many(positions, self.elo)

    def label(self) -> str:
        return f"Un joueur à {self.elo} Elo"


def make_model(opponent_elo: int, **naive) -> OpponentModel:
    """Maia à `opponent_elo` si c'est demandé et installé, sinon l'adversaire naïf à l'instinct."""
    if opponent_elo > 0:
        from fun_fish.sim import maia

        if maia.available():
            return MaiaModel(opponent_elo)
    return NaiveModel(**naive)
