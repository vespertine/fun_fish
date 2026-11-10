"""Fun Fish : l'agent « public », figé.

Le flibustier avec les réglages retenus après les parties de test (novembre 2026) contre des bots
de 2000 à 2800 : adversaire imaginé à 600 Elo, tolérance de 3 pions, filtre et attaque à
profondeur 6, budget fun de 270 cp. Tous les réglages sont recopiés ici : changer les défauts
des autres profils ne le modifie pas. C'est l'adversaire du mode jeu (`fun-fish play`).
"""

from __future__ import annotations

from fun_fish.personalities.buccaneer import Buccaneer
from fun_fish.theory import Theory

FROZEN = {"opponent_elo": 600, "tolerance": 300, "soundness_depth": 6, "attack_depth": 6, "attack_moves": 3, "branching": 3, "plausible_mass": 0.85, "min_probability": 0.08, "mate_within": 4, "big_gain": 3, "w_mate": 3, "w_win": 1.5, "w_eval": 0.3, "w_loss": 1, "temperature": 0.3, "max_candidates": 12, "deep_candidates": 5, "convert_from": 300, "convert_full": 800, "convert_depth": 4, "convert_tolerance": 60, "w_technique": 0.6, "w_develop": 0.6, "w_open": 0.4, "w_king": 0.4, "w_activity": 0.2, "w_initiative": 0.4, "w_sacrifice": 0.3, "w_surprise": 0, "w_center": 0.5, "w_threat": 0.4, "mood": 0.3, "w_repertoire": 2, "repertoire_rate": 0.25, "style_margin": 1, "style_first": 0, "vision_elo": 1800, "vision_min": 0.02, "vision_breadth": 6, "vision_panic": 150, "w_human": 0.3, "defend_from": 100, "defend_full": 400, "w_counterplay": 0.6, "impatience": 0, "impatience_half": 20, "w_plan": 0, "convert_elo": 1200, "w_repeat": 1, "instinct": 0.7, "caution": 0.5, "naivety": 60, "w_trap": 0.5, "trap_cap": 3, "w_sharp": 1, "error_margin": 150, "safe_margin": 60, "our_options": 2, "lead_share": 0.3, "fun_budget": 270, "w_fun": 1, "fun_list": 8, "fun_elo": 1600, "w_hanging": 1, "w_disdain": 1, "w_unanswered": 0.5, "w_sacrifice_fun": 1, "w_assault": 1, "w_unexpected": 0.5}


class FunFish(Buccaneer):
    key = "fun_fish"
    name = "Fun Fish"
    description = ("Un joueur d'attaque romantique : gambits inventés, sacrifices, pièges et assauts sur le roi. "
                   "Il peut se tromper, mais il ne s'ennuie jamais.")

    def __init__(self, **kwargs):
        super().__init__(**{**FROZEN, **kwargs})

    @classmethod
    def param_specs(cls) -> list[dict]:
        return [{**spec, "default": FROZEN.get(spec["name"], spec["default"])} for spec in super().param_specs()]


# Essai v2 : goût du centre réduit (le premier romantique, sans lui, jouait Cf3, Cc3 puis e4 !?),
# échecs sans suite peu « fun », répertoire propre plus souvent tiré.
FROZEN_2 = {**FROZEN, "mate_sure": 3, "w_pretty": 1.0, "w_center": 0.0, "w_creative": 1.2, "cheap_check": 0.3, "repertoire_rate": 0.35, "creative_slots": 2,
            "w_develop": 0.9, "opening_temperature": 0.8, "opening_plies": 6,
            "vision_from": 10, "opening_vision_min": 0.0}


class FunFish2(FunFish):
    """Ouverture libre : aucun filtre de vision humaine sur les 10 premiers demi-coups (1.Cc3, voire 1.a3,
    sont permis), pas de demi-point de développement pour un simple fou libéré (comme l'ancien : sinon
    1...d5 l'emporte toujours sur les coups de cavalier), ni de réflexe Da4+ ou Fg5 (dame précoce
    pénalisée, menace sur la dame comptée comme sur une tour, pression sur le roi après la réponse)."""
    key = "fun_fish_2"
    name = "Fun Fish 2"
    description = "Fun Fish, plus varié en ouverture : deux cavaliers, gambits célèbres, moins d'échecs gratuits."

    def __init__(self, **kwargs):
        Buccaneer.__init__(self, **{**FROZEN_2, **kwargs})

    def style(self, board):
        style = super().style(board)
        style.bishop_release = 0.0
        style.queen_sortie, style.queen_threat = 0.5, 5  # ni Da4+ ni Fg5 par réflexe
        style.king_after_reply = True
        return style

    @classmethod
    def param_specs(cls) -> list[dict]:
        return [{**spec, "default": FROZEN_2.get(spec["name"], spec["default"])} for spec in Buccaneer.param_specs()]


class FunFishTheory(Theory, FunFish):
    """Fun Fish avec la surcharge « théorie » : une partie sur quatre, 1 à 4 coups classiques d'abord."""
    key = "fun_fish_theorie"
    name = "Fun Fish + théorie (essai)"
    description = "Fun Fish qui commence parfois par quelques coups de théorie classique, puis joue à sa façon."


class FunFish2Theory(Theory, FunFish2):
    """Fun Fish 2 avec la surcharge « théorie »."""
    key = "fun_fish_2_theorie"
    name = "Fun Fish 2 + théorie"
    description = "Fun Fish 2 qui commence parfois par quelques coups de théorie classique, puis joue à sa façon."


# Crazy Fish : réglages trouvés en jouant (parties du 9 novembre 2026 contre le bot Morphy de chess.com,
# perdues sur le papier, gagnées par un mat préparé) : il imagine un adversaire à 500 Elo et croit donc
# à tous ses pièges, voit un peu moins loin (5), propose plus de coups créatifs et s'autorise du luxe
# une fois gagnant. Avec panache : gagnant, il soigne la manière plutôt que le matériel (40 parties contre
# Maia 2000, nuit du 10 novembre : 100 %, sans perte de force mesurable contre Stockfish).
FROZEN_CRAZY = {**FROZEN_2, "opponent_elo": 500, "soundness_depth": 5, "attack_depth": 5, "temperature": 0.35,
                "convert_from": 350, "convert_elo": 1100, "luxury_keep": 250, "luxury_cap": 200,
                "creative_slots": 3, "creative_min": 32, "panache": 1.0,
                "w_pretty": 2.0}


class CrazyFish(Theory, FunFish2):
    """Fun Fish 2 et sa théorie (une partie sur cinq), en plus fou : pièges partout, coups créatifs."""
    key = "crazy_fish"
    name = "Crazy Fish"
    description = ("Fun Fish en plus fou : il croit à tous ses pièges, ose des coups que personne ne jouerait, "
                   "et prépare ses mats même en position perdue.")

    def __init__(self, theory_rate: float = 0.2, **kwargs):
        Theory.__init__(self, theory_rate=theory_rate, **{**FROZEN_CRAZY, **kwargs})

    @classmethod
    def param_specs(cls) -> list[dict]:
        specs = super().param_specs()
        for spec in specs:
            if spec["name"] == "theory_rate":
                spec["default"] = 0.2
            elif spec["name"] in FROZEN_CRAZY:
                spec["default"] = FROZEN_CRAZY[spec["name"]]
        return specs
