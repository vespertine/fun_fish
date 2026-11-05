"""L'ancien : le premier romantique, celui de la partie du 2 novembre 2026.

Cette partie (1.Cf3 Cf6 2.Cc3 d5 3.e4 !? dxe4…) avait un charme que les versions suivantes ont
perdu : les cavaliers d'abord, puis le pion central donné ; des coups calmes inattendus (montées de
tour, replis de dame) ; un jeu plus faible, mais plus libre. On le retrouve en reprenant ses
réglages de l'époque et en coupant ce qui est venu après : goût du centre, vision et naturalité
humaines, contre-jeu, humeur, répertoire, défense resserrée, écart type plancher des goûts,
développement compté pour un simple fou libéré.
"""

from __future__ import annotations

import chess

from fun_fish.personalities.romantic import Romantic
from fun_fish.style import Style

THEN = {"opponent_elo": 300, "tolerance": 300, "soundness_depth": 5, "attack_depth": 4, "attack_moves": 3,
        "branching": 3, "plausible_mass": 0.85, "min_probability": 0.08, "mate_within": 3, "big_gain": 3,
        "w_mate": 3, "w_win": 1.5, "w_eval": 0.3, "w_loss": 1, "temperature": 0.3, "max_candidates": 12,
        "deep_candidates": 5, "convert_from": 300, "convert_full": 800, "convert_depth": 4,
        "convert_tolerance": 60, "w_technique": 1, "w_develop": 0.6, "w_open": 0.4, "w_king": 0.4,
        "w_activity": 0.2, "w_initiative": 0.4, "w_sacrifice": 0.3, "w_surprise": 0, "style_first": 0,
        "instinct": 0.7, "caution": 0.5, "naivety": 60,
        # venus après : coupés
        "w_center": 0, "w_threat": 0, "mood": 0, "vision_elo": 0, "w_human": 0, "w_counterplay": 0,
        "w_repertoire": 0, "repertoire_rate": 0, "defend_from": 0, "defend_full": 0, "convert_elo": 0,
        "w_repeat": 0, "impatience": 0, "w_plan": 0}


class Ancien(Romantic):
    key = "ancien"
    name = "L'ancien"
    description = ("Le premier romantique : les cavaliers d'abord, puis le pion central donné ; des coups calmes "
                   "inattendus. Plus faible, plus libre.")

    def __init__(self, **kwargs):
        super().__init__(**{**THEN, **kwargs})

    @classmethod
    def param_specs(cls) -> list[dict]:
        return [{**spec, "default": THEN.get(spec["name"], spec["default"])} for spec in Romantic.param_specs()]

    def style(self, board: chess.Board) -> Style:
        style = super().style(board)
        style.floor_scale = 0.0
        style.bishop_release = 0.0
        return style
