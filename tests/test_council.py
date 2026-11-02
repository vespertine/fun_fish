import random

import chess

from fun_fish.personalities.base import Candidate, Decision, Personality
from fun_fish.personalities.council import Council


class Fixed(Personality):
    """Distribution fixe ; note les coups auxquels on l'a restreinte."""

    key, name, description = "fixed", "Fixe", ""

    def __init__(self, probs, rng=None):
        super().__init__(rng=rng)
        self.probs, self.asked = probs, []

    def choose_move(self, board, analyser):
        self.asked.append(None if self.restrict is None else sorted(m.uci() for m in self.restrict))
        moves = [chess.Move.from_uci(u) for u in self.probs if self.restrict is None
                 or chess.Move.from_uci(u) in self.restrict]
        cands = [Candidate(m, board.san(m), 0, probability=self.probs[m.uci()]) for m in moves]
        best = max(cands, key=lambda c: c.probability)
        return Decision(best.move, best.san, board.turn, f"{self.name} : {best.san}", cands)


def council(first, second):
    c = Council.__new__(Council)
    Personality.__init__(c, rng=random.Random(0))
    c.keep, c.floor = 0.5, 0.02
    c.members = [Fixed(first), Fixed(second)]
    return c


def test_second_stage_breaks_ties_only():
    c = council({"e2e4": 0.45, "d2d4": 0.4, "a2a3": 0.15}, {"d2d4": 0.9, "e2e4": 0.1, "a2a3": 1.0})
    d = c.choose_move(chess.Board(), None)
    assert c.members[1].asked == [["d2d4", "e2e4"]]  # a2a3, trop loin du favori, n'est pas rejugé
    probs = {x.move.uci(): x.probability for x in d.candidates}
    assert probs["d2d4"] > 0.8 and probs["a2a3"] == 0.0
    assert "départage" in d.explanation


def test_clear_opinion_is_not_discussed():
    c = council({"e2e4": 0.9, "d2d4": 0.1}, {"d2d4": 1.0})
    d = c.choose_move(chess.Board(), None)
    assert d.san == "e4" and c.members[1].asked == []
