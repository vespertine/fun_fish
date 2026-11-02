"""Décisions à plusieurs étages : une personnalité propose, la suivante départage.

Chaque étage est une personnalité complète, avec sa propre distribution de choix. Le premier
juge tous les coups ; on garde ceux qu'il aime presque autant que son favori (probabilité ≥
`keep` × la meilleure). S'il n'en reste qu'un, il est joué : un avis tranché ne se discute
pas. Sinon l'étage suivant ne juge que ces coups-là, et sa distribution multiplie la
précédente (`floor` évite qu'un coup qu'il n'aime pas du tout soit éliminé d'office). Et
ainsi de suite.

Le premier étage donne le caractère, les suivants affinent quand il hésite. Mesuré sur 147
positions de parties réelles, « cogneur puis arnaqueur » perd en moyenne 7,4 cp contre 10,2 cp
pour le cogneur seul, et ne change qu'un choix sur dix.
"""

from __future__ import annotations

import random
from typing import ClassVar

import chess

from fun_fish.analysis import Analyser
from fun_fish.personalities.base import Candidate, Decision, Personality, pick


class Council(Personality):
    """Base des conseils : `STAGES` donne les personnalités, dans l'ordre."""

    STAGES: ClassVar[tuple[str, ...]] = ()
    SHARED: ClassVar[tuple[str, ...]] = ("opponent_elo", "tolerance", "soundness_depth")
    PARAM_HELP = {
        "keep": "Conseil : un coup reste en lice s'il a au moins cette part de la probabilité du favori (0 à 1)",
        "floor": "Conseil : probabilité plancher donnée par un étage à un coup qu'il n'aime pas",
        "opponent_elo": "Elo de l'adversaire imaginé par tous les étages (0 = adversaire naïf)",
        "tolerance": "Perte maximale acceptée par le premier étage (centipions)",
        "soundness_depth": "Profondeur du filtre de santé, pour tous les étages",
    }

    def __init__(self, keep: float = 0.5, floor: float = 0.02, opponent_elo: int = 1500, tolerance: int = 150,
                 soundness_depth: int = 10, rng: random.Random | None = None):
        super().__init__(rng=rng)
        from fun_fish.personalities import create

        self.keep, self.floor = keep, floor
        self.opponent_elo, self.tolerance, self.soundness_depth = opponent_elo, tolerance, soundness_depth
        shared = {name: getattr(self, name) for name in self.SHARED}
        self.members = [create(key, rng=self.rng, **shared) for key in self.STAGES]

    def choose_move(self, board: chess.Board, analyser: Analyser) -> Decision:
        decisions: list[Decision] = []
        weights: dict[chess.Move, float] = {}
        for member in self.members:
            if weights:
                top = max(weights.values())
                contenders = [m for m, w in weights.items() if w >= self.keep * top]
                if len(contenders) == 1:
                    break
                member.restrict = contenders
            try:
                decision = member.choose_move(board, analyser)
            finally:
                member.restrict = None
            decisions.append(decision)
            opinion = {c.move: c.probability or 0.0 for c in decision.candidates}
            if not weights:
                weights = opinion
            else:
                weights = {m: weights[m] * max(opinion.get(m, 0.0), self.floor) for m in contenders}
            total = sum(weights.values()) or 1.0
            weights = {m: w / total for m, w in weights.items()}

        moves = list(weights)
        chosen = moves[pick([weights[m] for m in moves], self.rng)]
        first = decisions[0]
        notes = {c.move: c for c in first.candidates}
        candidates = [
            Candidate(move=c.move, san=c.san, score=c.score, mate=c.mate, probability=weights.get(c.move, 0.0),
                      note=c.note)
            for c in sorted(first.candidates, key=lambda c: -weights.get(c.move, 0.0))
        ]
        san = board.san(chosen)
        speaker = next((d for d in reversed(decisions) if d.move == chosen), None)
        explanation = speaker.explanation if speaker else f"Joue {san}. {notes[chosen].note}".strip()
        if len(decisions) > 1:
            names = [self.members[i].name for i in range(len(decisions))]
            hesitated = ", ".join(board.san(m) for m in moves)
            explanation = (f"{names[0]} hésite entre {hesitated} ; {', '.join(names[1:])} départage. "
                           + explanation)
        return Decision(move=chosen, san=san, color=board.turn, explanation=explanation, candidates=candidates,
                        reality=first.reality if first.move == chosen else None)


class Duo(Council):
    key = "duo"
    name = "Le duo"
    description = ("Le romantique choisit ; quand il hésite entre plusieurs coups, l'arnaqueur "
                   "départage en faveur de celui qui tend le meilleur piège.")
    STAGES = ("romantic", "hustler")
