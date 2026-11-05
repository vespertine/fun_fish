"""Le flibustier : le corsaire, avec un troisième regard qui ne juge que l'allure des coups.

Le composant fun (`fun_fish.fun`) classe tous les coups légaux sans regarder Stockfish :
pièces laissées en l'air, menace ignorée, prise non reprise, sacrifice, assaut sur le roi,
coup inattendu pour un humain de `fun_elo`. Ses `fun_list` favoris sont ajoutés aux candidats
(Stockfish les évalue, même s'ils sont hors des meilleures lignes ou invisibles pour sa
vision), et l'agrégateur **paie** pour leur allure : il accepte d'y perdre jusqu'à `fun_budget`
cp par rapport au meilleur coup. Le bonus vaut `w_fun` × fun, et s'amenuise à mesure que la
perte approche du budget ; au-delà, l'allure ne compte plus.
"""

from __future__ import annotations

import inspect

import chess

from fun_fish.analysis import Analyser, Line
from fun_fish.fun import FUN_WEIGHTS, fun_scores
from fun_fish.personalities.base import regret
from fun_fish.personalities.corsair import Corsair
from fun_fish.style import Style


class Buccaneer(Corsair):
    key = "buccaneer"
    name = "Le flibustier"
    description = ("Le corsaire, qui paie volontiers quelques centipions pour un coup qui a de l'allure : "
                   "pièce en l'air, menace ignorée, sacrifice, assaut, coup que personne ne jouerait.")
    FUN_HELP = {
        "fun_budget": "Fun : perte maximale (cp) qu'il accepte pour un coup qui a de l'allure (0 = aucune)",
        "w_fun": "Fun : poids de l'allure des coups dans son choix",
        "fun_list": "Fun : ses coups préférés (selon l'allure seule) ajoutés aux candidats",
        "fun_elo": "Fun : un coup est inattendu s'il est rare chez un humain de cet Elo (Maia ; 0 = celui de la vision)",
        "w_hanging": "Fun : laisser une pièce en l'air",
        "w_disdain": "Fun : ignorer une menace",
        "w_unanswered": "Fun : ne pas reprendre",
        "w_sacrifice_fun": "Fun : sacrifier une pièce (sans calcul)",
        "w_assault": "Fun : échec, pression sur le roi, pion à l'assaut",
        "w_unexpected": "Fun : coup inattendu (s'il a déjà de l'allure)",
    }

    def __init__(self, fun_budget: int = 100, w_fun: float = 1.0, fun_list: int = 5, fun_elo: int = 1600,
                 w_hanging: float = 1.0, w_disdain: float = 1.0, w_unanswered: float = 0.5,
                 w_sacrifice_fun: float = 1.0, w_assault: float = 1.0, w_unexpected: float = 0.5, **kwargs):
        super().__init__(**kwargs)
        self.fun_budget, self.w_fun, self.fun_list, self.fun_elo = fun_budget, w_fun, fun_list, fun_elo
        self.w_hanging, self.w_disdain, self.w_unanswered = w_hanging, w_disdain, w_unanswered
        self.w_sacrifice_fun, self.w_assault, self.w_unexpected = w_sacrifice_fun, w_assault, w_unexpected
        self._fun: dict[chess.Move, tuple[float, str]] = {}
        self._fun_maia = None

    @classmethod
    def param_specs(cls) -> list[dict]:
        specs = Corsair.param_specs()
        for name, param in inspect.signature(cls.__init__).parameters.items():
            if name in cls.FUN_HELP:
                specs.append({"name": name, "default": param.default,
                              "type": "int" if isinstance(param.default, int) else "float", "help": cls.FUN_HELP[name]})
        return specs

    def fun_weights(self) -> dict[str, float]:
        return {"hanging": self.w_hanging, "disdain": self.w_disdain, "unanswered": self.w_unanswered,
                "sacrifice": self.w_sacrifice_fun, "assault": self.w_assault, "unexpected": self.w_unexpected}

    def surprise_basis(self, board: chess.Board, analyser: Analyser,
                       seen: dict[chess.Move, float] | None) -> dict[chess.Move, float] | None:
        """Ce qu'un humain de `fun_elo` jouerait (sinon les probabilités de la vision, si on les a)."""
        if self.fun_elo <= 0 or not self.w_unexpected:
            return seen
        if self._fun_maia is None:
            from fun_fish.opponent import MaiaModel
            from fun_fish.sim import maia

            if not maia.available():
                return seen
            self._fun_maia = MaiaModel(self.fun_elo)
        return self._fun_maia.probabilities(board, analyser)

    def more_candidates(self, board: chess.Board, analyser: Analyser, sound: list[Line],
                        seen: dict[chess.Move, float] | None) -> list[Line]:
        self._fun = {}
        if self.w_fun <= 0 or self.fun_budget <= 0 or self.restrict:
            return sound
        self._fun = fun_scores(board, self.fun_weights(), self.surprise_basis(board, analyser, seen))
        have = {line.move for line in sound}
        favourites = [m for m, (f, _) in sorted(self._fun.items(), key=lambda x: -x[1][0])[: self.fun_list]
                      if f > 0 and m not in have]
        if not favourites:
            return sound
        extra = analyser.analyse(board, multipv=len(favourites), depth=self.soundness_depth, moves=favourites)
        kept = [line for line in extra if regret(sound[0], line) <= self.fun_budget]
        return sorted(sound + kept, key=lambda line: -line.score) if kept else sound

    def more_bonus(self, board: chess.Board, sound: list[Line], best: Line, bonus: dict[chess.Move, float],
                   style: Style) -> None:
        if not self._fun:
            return
        for line in sound:
            fun, reason = self._fun.get(line.move, (0.0, ""))
            share = max(0.0, 1.0 - regret(best, line) / self.fun_budget)
            extra = self.w_fun * fun * share
            if extra <= 0:
                continue
            bonus[line.move] = bonus.get(line.move, 0.0) + extra
            if extra >= 0.5 and reason:
                style.reasons[line.move] = reason
