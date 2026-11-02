"""Le romantique : le cogneur, avec le goût du développement, des lignes ouvertes et de l'initiative.

Un profil de style (voir `fun_fish.style`) posé sur le cogneur : parmi les coups sains, quand
aucune attaque ne paie tout de suite, il préfère sortir ses pièces, ouvrir le jeu, viser le roi
et garder l'initiative plutôt que le matériel ; à position égale, un sacrifice sain lui plaît.
En ouverture, il aime les pions au centre et gagner du temps sur les pièces adverses ; son
humeur, tirée à chaque partie, varie ses goûts (et donc ses ouvertures). Une partie sur
cinq environ, il a envie d'un gambit connu (voir `fun_fish.repertoire`). Il voit les mats
jusqu'en 4 coups.
"""

from __future__ import annotations

from fun_fish.personalities.attacker import Attacker


class Romantic(Attacker):
    key = "romantic"
    name = "Le romantique"
    description = ("Un cogneur qui développe vite, ouvre les lignes et préfère l'initiative au matériel. "
                   "Un sacrifice sain à position égale ne lui fait pas peur. "
                   "Ses ouvertures changent avec son humeur.")
    DEFAULTS = {"w_develop": 0.6, "w_open": 0.4, "w_king": 0.4, "w_activity": 0.2,
                "w_initiative": 0.4, "w_sacrifice": 0.3,
                "w_center": 0.5, "w_threat": 0.4, "mood": 0.35, "w_repertoire": 2.0, "repertoire_rate": 0.2, "mate_within": 4}

    def __init__(self, **kwargs):
        super().__init__(**{**self.DEFAULTS, **kwargs})
