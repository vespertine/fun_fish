"""Le romantique : le cogneur, avec le goût du développement, des lignes ouvertes et de l'initiative.

Un profil de style (voir `fun_fish.style`) posé sur le cogneur : parmi les coups sains, quand
aucune attaque ne paie tout de suite, il préfère sortir ses pièces, ouvrir le jeu, viser le roi
et garder l'initiative plutôt que le matériel ; à position égale, un sacrifice sain lui plaît.
En ouverture, il suit son répertoire de gambits (voir `fun_fish.repertoire`), une ligne tirée
au sort par partie, et voit les mats jusqu'en 4 coups.
"""

from __future__ import annotations

from fun_fish.personalities.attacker import Attacker


class Romantic(Attacker):
    key = "romantic"
    name = "Le romantique"
    description = ("Un cogneur qui développe vite, ouvre les lignes et préfère l'initiative au matériel. "
                   "Un sacrifice sain à position égale ne lui fait pas peur. "
                   "En ouverture, il joue ses gambits.")
    DEFAULTS = {"w_develop": 0.6, "w_open": 0.4, "w_king": 0.4, "w_activity": 0.2,
                "w_initiative": 0.4, "w_sacrifice": 0.3,
                "w_repertoire": 3.0, "mate_within": 4}

    def __init__(self, **kwargs):
        super().__init__(**{**self.DEFAULTS, **kwargs})
