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
