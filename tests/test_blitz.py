import random

import chess

from fun_fish.personalities.blitz import Blitz


def blitz(**kwargs):
    return Blitz(rng=random.Random(0), **kwargs)


def test_prefers_visible_mate_over_material(fake_analyser):
    board = chess.Board("4k3/8/8/3n4/8/8/8/3RK3 w - - 0 1")
    fake_analyser.scores = {"d1d5": 900}
    fake_analyser.mates = {"e1e2": 2}
    assert blitz().choose_move(board, fake_analyser).move.uci() == "e1e2"


def test_ignores_complicated_mate_when_a_piece_is_hanging(fake_analyser):
    """Mat en 6 et gain d'une pièce sont tous deux « décisifs » : il prend la pièce."""
    board = chess.Board("4k3/8/8/3n4/8/8/8/3RK3 w - - 0 1")
    fake_analyser.scores = {"d1d5": 900}
    fake_analyser.mates = {"e1e2": 6}
    fake_analyser.pvs = {"d1d5": ["d1d5", "e8e7"]}
    assert blitz().choose_move(board, fake_analyser).move.uci() == "d1d5"


def test_never_leaves_its_perceived_best_level(fake_analyser):
    board = chess.Board()
    fake_analyser.scores = {"e2e4": 50, "d2d4": 45, "g1f3": 40}
    for seed in range(20):
        decision = Blitz(rng=random.Random(seed)).choose_move(board, fake_analyser)
        assert decision.move.uci() in {"e2e4", "d2d4", "g1f3"}  # léger avantage ; le reste est « égalité »


def test_coarse_reading_makes_close_moves_equivalent(fake_analyser):
    board = chess.Board()
    fake_analyser.scores = {"e2e4": 50, "d2d4": 45}
    played = {Blitz(rng=random.Random(s), temperature=1000).choose_move(board, fake_analyser).san for s in range(30)}
    assert {"e4", "d4"} <= played


def test_decision_carries_reading_and_reality(fake_analyser):
    decision = blitz().choose_move(chess.Board(), fake_analyser)
    assert decision.explanation.startswith("Voit ")
    assert decision.reality and decision.to_dict()["reality"] == decision.reality
    assert all(c.note for c in decision.candidates)


def test_real_stockfish_plays_quick_mate(stockfish_analyser):
    board = chess.Board("6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1")
    decision = blitz(reality_depth=10).choose_move(board, stockfish_analyser)
    assert decision.san == "Rd8#"
