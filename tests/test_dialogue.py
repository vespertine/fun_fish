import chess

from fun_fish.analysis import Line
from fun_fish.personalities.attacker import Attacker, CandidateEval, Outcome
from fun_fish.personalities.romantic import Romantic
from fun_fish.style import center, threats


def play(*sans):
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board


def test_style_cannot_overturn_a_clear_attack():
    a = Attacker(style_margin=1.0)
    m1, m2 = chess.Move.from_uci("e2e4"), chess.Move.from_uci("d2d4")
    attack = CandidateEval(Line(m1, 50, None, None, (m1,), 10), 0, Outcome(win=0.4, value=500))
    quiet = CandidateEval(Line(m2, 40, None, None, (m2,), 10), 10, Outcome(value=40))
    for e in (attack, quiet):
        a.scored(e, attack.line)
    scores = a.combine([attack, quiet], {m1: -1.0, m2: 2.5})
    assert scores[0] > scores[1]
    flat = a.combine([quiet, quiet], {m2: 2.5})
    assert flat[0] == quiet.score + 2.5  # sans idée d'attaque, le style parle à plein


def test_center_and_threats():
    assert center(play("e4"), chess.WHITE) > center(play("Nf3"), chess.WHITE)
    board = chess.Board("rnb1kbnr/pppp1ppp/8/4p1q1/4P3/2N5/PPPP1PPP/R1BQKBNR w KQkq - 0 1")
    after = board.copy()
    after.push_san("Nf3")  # le cavalier attaque la dame
    assert threats(after, chess.WHITE) >= 3
    assert threats(board, chess.WHITE) == 0


def test_mood_is_drawn_once_per_game():
    r = Romantic(mood=0.35)
    first = r.style(chess.Board())
    later = r.style(play("e4", "e5", "Nf3"))
    assert first == later  # même partie, même humeur
    other = r.style(chess.Board())  # nouvelle partie
    assert other != first
    assert Romantic(mood=0).style(chess.Board()).w_develop == 0.6


def test_repertoire_is_an_occasional_wish():
    import random

    from fun_fish.repertoire import REPERTOIRES, Planner
    for openings in REPERTOIRES.values():
        for color, lines in openings.items():
            for o in lines:
                assert len(o.moves) % 2 == (1 if color == chess.WHITE else 0), o.name
    always = Planner(REPERTOIRES["romantic"], random.Random(1), rate=1.0)
    assert always.next(chess.Board()) is not None
    move, name = always.next(play("e4", "c5"))
    assert move == chess.Move.from_uci("d2d4") and "Morra" in name
    assert always.next(play("e4", "c5", "d4", "e6")) is None  # sorti de toutes les lignes
    sometimes = Planner(REPERTOIRES["romantic"], random.Random(2), rate=0.2)
    wished = sum(sometimes.next(chess.Board()) is not None for _ in range(500))
    assert 60 < wished < 140


class FakeAnalyser:
    """Scores fixes par coup ; respecte multipv et la restriction `moves`."""

    def __init__(self, scores):
        self.scores = scores

    def analyse(self, board, multipv=1, depth=None, moves=None, **_):
        pool = [m for m in board.legal_moves if moves is None or m in moves]
        lines = [Line(m, self.scores.get(m.uci(), -500), None, None, (m,), depth or 0) for m in pool]
        return sorted(lines, key=lambda l: -l.score)[:multipv]


def test_vision_hides_moves_a_human_would_not_play():
    from fun_fish.personalities.base import Soundness
    board = chess.Board()
    fake = FakeAnalyser({"g1h3": 300, "e2e4": 30, "d2d4": 25, "c2c4": 10})
    s = Soundness(tolerance=50, depth=5, max_candidates=12, convert_from=0, vision_breadth=3, vision_panic=0)
    assert [l.move.uci() for l in s.lines(board, fake)[0]] == ["g1h3"]  # tout voir : seul Ch3 est sain
    seen = {chess.Move.from_uci(u): p for u, p in {"e2e4": 0.6, "d2d4": 0.3, "c2c4": 0.09, "g1h3": 0.01}.items()}
    lines, _ = s.lines(board, fake, seen)
    assert [l.move.uci() for l in lines] == ["e2e4", "d2d4", "c2c4"]  # Ch3 n'est pas vu
    assert s.truth.move.uci() == "g1h3"
    s.vision_panic = 200  # Ch3 vaut 2,7 pions de plus : ça brûle, il voit tout
    assert [l.move.uci() for l in s.lines(board, fake, seen)[0]] == ["g1h3"]



def test_vision_always_sees_forcing_moves():
    from fun_fish.personalities.base import Soundness
    board = chess.Board("4k3/8/8/8/3q4/5N2/8/R6K w - - 0 1")
    fake = FakeAnalyser({"a1a8": 900, "f3d4": 880, "a1a2": -500})
    s = Soundness(tolerance=50, depth=5, max_candidates=12, convert_from=0, convert_depth=0)
    seen = {chess.Move.from_uci("a1a2"): 0.9, chess.Move.from_uci("f3d4"): 0.01, chess.Move.from_uci("a1a8"): 0.01}
    lines, _ = s.lines(board, fake, seen)
    assert [l.move.uci() for l in lines] == ["a1a8", "f3d4"]  # l'échec et la prise sont vus, même improbables


def test_naturalness_spares_forcing_moves():
    from fun_fish.personalities.attacker import Attacker
    board = chess.Board()
    a = Attacker(vision_min=0.02)
    seen = {chess.Move.from_uci("e2e4"): 0.5, chess.Move.from_uci("a2a3"): 0.05}
    value = a.naturalness(board, list(seen) + [chess.Move.from_uci("h2h3")], seen)
    assert value[chess.Move.from_uci("e2e4")] == 0.0
    assert value[chess.Move.from_uci("a2a3")] < 0
    assert value[chess.Move.from_uci("h2h3")] < value[chess.Move.from_uci("a2a3")]  # jamais vu : plancher


def test_counterplay_when_in_trouble():
    from fun_fish.personalities.base import defending
    from fun_fish.style import Style
    assert defending(-100, 150, 500) == 0.0 and defending(-500, 150, 500) == 1.0
    board = chess.Board("4k3/8/8/8/8/8/1q6/R3K3 w - - 0 1")  # en retard d'une dame
    quiet, check = chess.Move.from_uci("e1f1"), chess.Move.from_uci("a1a8")
    lines = [Line(quiet, -800, None, None, (quiet,), 5), Line(check, -810, None, None, (check,), 5)]
    calm = Style().bonuses(board, lines, lines[0])
    assert calm[quiet] == calm[check] == 0.0
    fight = Style().bonuses(board, lines, lines[0], counterplay=0.6)
    assert fight[check] > fight[quiet]  # l'échec pose une question


def test_forcing_moves_are_checked_deeper():
    from fun_fish.personalities.base import Soundness

    class Shallow(FakeAnalyser):
        def analyse(self, board, multipv=1, depth=None, moves=None, **_):
            lines = super().analyse(board, multipv, depth, moves)
            if depth >= 10:  # en profondeur, le sacrifice mate
                lines = [Line(l.move, 99_000, 3, None, l.pv, depth) if l.move.uci() == "a1a8" else l for l in lines]
            return sorted(lines, key=lambda l: -l.score)

    board = chess.Board("4k3/8/8/8/3q4/5N2/8/R6K w - - 0 1")
    fake = Shallow({"a1a8": -900, "f3d4": 880, "a1a2": -500})
    s = Soundness(tolerance=50, depth=5, max_candidates=12, convert_from=0, convert_depth=0, forcing_depth=0)
    assert "a1a8" not in [l.move.uci() for l in s.lines(board, fake)[0]]
    s.forcing_depth = 10
    assert s.lines(board, fake)[0][0].move.uci() == "a1a8"
