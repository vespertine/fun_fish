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
    name = "CapaBot V1"
    description = "Fun Fish 2 qui recherche les positions des joueurs solides : clair, patient, avec de temps en temps un coup à la Fun Fish."
    OVERRIDES = {"w_repertoire": 0, "theory_rate": 0}  # la V1 telle qu'elle a joué sur Lichess
    EXTRA = {"w_pos": -4.0, "pos_from": 0}
