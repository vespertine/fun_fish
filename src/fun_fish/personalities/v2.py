"""DuBotV2 et CapaBotV2 : DuBot et CapaBot plus robustes (essai de novembre 2026).

Les défaites de DuBot contre les bots (30 sur 48 parties, contre 30 victoires sur 30 face aux humains)
se jouaient surtout à l'ouverture, sur ses propres gambits (Englund, Duras) : des pièges évalués contre
un adversaire imaginé à 600 Elo, admis jusqu'à 3 pions de perte. Ici :

- **théorie** : dans un tiers des parties, quelques coups d'ouverture, tirés de l'arbre des maîtres-modèles
  (`data/books`, voir scripts/model_books.py) ou, pour DuBotV2 seulement, des lignes classiques de Fun Fish ;
  le reste du temps, il joue à sa façon ;
- **pièges plus fins** : adversaire imaginé à 1500 (DuBotV2) ou 2000, le haut de Maia (CapaBotV2) ;
- **moins de tolérance** : 2 pions (DuBotV2), 1,5 (CapaBotV2), et 1 pion sur ses trois premiers coups ;
- DuBotV2 : goût des positions un peu atténué (`w_pos` 3), et gagnant, il convertit plus tôt, rend moins
  l'avantage et cherche davantage le mat ; CapaBotV2 garde son goût entier, aussi en conversion.
"""

from __future__ import annotations

from fun_fish.personalities.capabot import CapaBot
from fun_fish.personalities.dubot import DuBot
from fun_fish.theory import Theory


class _V2(Theory):
    THEORY: dict = {}

    def __init__(self, **kwargs):
        super().__init__(**{**self.THEORY, **kwargs})

    @classmethod
    def param_specs(cls) -> list[dict]:
        return [{**s, "default": cls.THEORY.get(s["name"], s["default"])} for s in super().param_specs()]


class DuBotV2(_V2, DuBot):
    key = "dubot_v2"
    name = "DuBotV2"
    description = "DuBot plus robuste : un peu de théorie des maîtres d'attaque, des pièges pour joueurs plus forts."
    THEORY = {"theory_rate": 0.35, "theory_moves": 6, "theory_book": "attaque", "theory_mix": 0.5}
    OVERRIDES = {**DuBot.OVERRIDES, "opponent_elo": 1500, "tolerance": 200, "opening_tolerance": 100, "convert_from": 200,
                 "convert_tolerance": 40, "convert_elo": 2000, "w_mate": 4.0, "mate_within": 5}
    EXTRA = {**DuBot.EXTRA, "w_pos": 3.0}


class CapaBotV2(_V2, CapaBot):
    key = "capabot_v2"
    name = "CapaBotV2"
    description = "CapaBot plus exigeant : l'ouverture des maîtres positionnels, des pièges pour joueurs forts."
    THEORY = {"theory_rate": 0.35, "theory_moves": 6, "theory_book": "solide", "theory_mix": 1.0}
    OVERRIDES = {**CapaBot.OVERRIDES, "opponent_elo": 2000, "tolerance": 150, "opening_tolerance": 100, "convert_elo": 2000}
