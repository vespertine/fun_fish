"""Fun Fish : l'agent « public », figé.

Le flibustier avec les réglages retenus après les parties de test (novembre 2026) contre des bots
de 2000 à 2800 : adversaire imaginé à 600 Elo, tolérance de 3 pions, filtre et attaque à
profondeur 6, budget fun de 270 cp. Tous les réglages sont recopiés ici : changer les défauts
des autres profils ne le modifie pas. C'est l'adversaire du mode jeu (`fun-fish play`).
"""

from __future__ import annotations

from fun_fish.personalities.buccaneer import Buccaneer

FROZEN = {"opponent_elo": 600, "tolerance": 300, "soundness_depth": 6, "attack_depth": 6, "attack_moves": 3, "branching": 3, "plausible_mass": 0.85, "min_probability": 0.08, "mate_within": 4, "big_gain": 3, "w_mate": 3, "w_win": 1.5, "w_eval": 0.3, "w_loss": 1, "temperature": 0.3, "max_candidates": 12, "deep_candidates": 5, "convert_from": 300, "convert_full": 800, "convert_depth": 4, "convert_tolerance": 60, "w_technique": 0.6, "w_develop": 0.6, "w_open": 0.4, "w_king": 0.4, "w_activity": 0.2, "w_initiative": 0.4, "w_sacrifice": 0.3, "w_surprise": 0, "w_center": 0.5, "w_threat": 0.4, "mood": 0.3, "w_repertoire": 2, "repertoire_rate": 0.2, "style_margin": 1, "style_first": 0, "vision_elo": 1800, "vision_min": 0.02, "vision_breadth": 6, "vision_panic": 150, "w_human": 0.3, "defend_from": 100, "defend_full": 400, "w_counterplay": 0.6, "impatience": 0, "impatience_half": 20, "w_plan": 0, "convert_elo": 1200, "w_repeat": 1, "instinct": 0.7, "caution": 0.5, "naivety": 60, "w_trap": 0.5, "trap_cap": 3, "w_sharp": 1, "error_margin": 150, "safe_margin": 60, "our_options": 2, "lead_share": 0.3, "fun_budget": 270, "w_fun": 1, "fun_list": 8, "fun_elo": 1600, "w_hanging": 1, "w_disdain": 1, "w_unanswered": 0.5, "w_sacrifice_fun": 1, "w_assault": 1, "w_unexpected": 0.5}


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
