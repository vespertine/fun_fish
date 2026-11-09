"""Gestion du temps : combien d'effort un coup peut se permettre à la pendule.

Le budget d'un coup est le temps restant partagé sur les coups qu'il reste probablement à jouer,
plus l'essentiel de l'incrément. Le coût de chaque niveau d'effort (voir `Attacker.EFFORT`) est
relatif à celui de la réflexion complète, mesuré sur le banc (scripts/perfbench.py) ; le coût
absolu, lui, s'apprend en jouant (il dépend de la machine et de la position). On prend le niveau le
plus élevé qui tient dans le budget : pressé, il réfléchit moins loin, comme un humain.
"""

from __future__ import annotations

COST = (1.0, 0.65, 0.48, 0.38, 0.22, 0.15)  # temps de chaque niveau d'effort / niveau 0 (banc, nov. 2026)
OVERHEAD = 0.3  # s par coup : réseau, Lichess


class Pace:
    def __init__(self, full_seconds: float = 3.0, learning: float = 0.3):
        self.full = full_seconds  # estimation du temps d'une réflexion complète (s)
        self.learning = learning

    @staticmethod
    def moves_left(fullmove: int) -> int:
        """Coups qu'il reste probablement à jouer (on prévoit large : mieux vaut finir avec du temps)."""
        return max(15, 45 - fullmove)

    def budget(self, remaining: float, increment: float, fullmove: int) -> float:
        return max(0.0, remaining / self.moves_left(fullmove) + 0.8 * increment - OVERHEAD)

    def effort(self, remaining: float, increment: float, fullmove: int) -> int:
        budget = self.budget(remaining, increment, fullmove)
        for level, cost in enumerate(COST):
            if self.full * cost <= budget:
                return level
        return len(COST) - 1

    def observe(self, seconds: float, effort: int) -> None:
        """Ce coup, à cet effort, a pris tant : on corrige l'estimation de la réflexion complète."""
        if seconds < 0.1:  # coup de répertoire ou forcé : ne dit rien de la vitesse de réflexion
            return
        seen = seconds / COST[effort]
        self.full += self.learning * (seen - self.full)
