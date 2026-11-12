"""CapaBot : DuBot à l'envers, attiré par les positions des joueurs solides (voir `fun_fish.position_style`).

Même mécanique que DuBot, avec `w_pos` −4 : parmi les coups sains de Fun Fish 2, il préfère ceux qui
mènent aux positions de Karpov, Petrosian, Capablanca… Libre en ouverture, sans théorie ni répertoire.
Mesures de novembre 2026 (40 parties par série) : 100 % contre Maia 2000, 59 % contre Fun Fish 2,
51 % contre Crazy Fish et 71 % contre DuBot : le goût du solide ne lui coûte rien, au contraire.
"""

from __future__ import annotations

from fun_fish.personalities.dubot import DuBot


class CapaBot(DuBot):
    key = "capabot"
    name = "CapaBot"
    description = "Fun Fish 2 qui recherche les positions des joueurs solides : clair, patient, sans prise de risque."
    EXTRA = {"w_pos": -4.0, "pos_from": 0}
