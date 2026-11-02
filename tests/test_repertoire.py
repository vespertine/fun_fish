import random

import chess

from fun_fish.analysis import Line
from fun_fish.personalities.attacker import Attacker, CandidateEval, Outcome
from fun_fish.repertoire import REPERTOIRES, Planner


def play(*sans):
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board


def test_lines_are_legal_and_end_on_our_move():
    for openings in REPERTOIRES.values():
        for color, lines in openings.items():
            for o in lines:
                assert o.moves and len(o.moves) % 2 == (1 if color == chess.WHITE else 0), o.name


def test_planner_follows_a_line_and_resamples_on_deviation():
    planner = Planner(REPERTOIRES["romantic"], random.Random(1))
    move, name = planner.next(play("e4", "c5"))
    assert move == chess.Move.from_uci("d2d4") and "Morra" in name
    assert planner.next(play("e4", "c5", "d4", "cxd4"))[0] == chess.Move.from_uci("c2c3")
    assert planner.next(play("e4", "c5", "d4", "e6")) is None  # sorti de toutes les lignes
    assert planner.next(chess.Board("8/8/8/8/8/8/k7/K7 w - - 0 1")) is None


def test_planner_varies_between_games():
    planner = Planner(REPERTOIRES["romantic"], random.Random(3))
    seen = set()
    for _ in range(40):
        planner.next(chess.Board())  # nouvelle partie
        seen.add(planner.next(play("e4", "e5"))[1])
    assert len(seen) >= 4


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
