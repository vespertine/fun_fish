import random

import chess
import pytest

from fun_fish import personalities
from fun_fish.personalities.hustler import Hustler


def test_params_can_be_overridden():
    h = personalities.create("hustler", tolerance=150, naivety=15.0)
    assert h.tolerance == 150 and h.naivety == 15.0
    with pytest.raises(ValueError):
        personalities.create("hustler", nope=1)


def test_plays_a_legal_move_with_fake_engine(fake_analyser):
    board = chess.Board()
    decision = Hustler(rng=random.Random(0), deep_candidates=2).choose_move(board, fake_analyser)
    assert board.is_legal(decision.move)
    assert decision.reality and decision.candidates


def test_tolerance_zero_keeps_only_best_moves(fake_analyser):
    board = chess.Board()
    fake_analyser.scores = {"e2e4": 80}
    decision = Hustler(rng=random.Random(0), tolerance=0).choose_move(board, fake_analyser)
    assert decision.san == "e4"


def test_finds_legal_trap_with_real_stockfish(stockfish_analyser):
    """Piège de Légal : 5.Cxe5 !, et 5...Fxd1 ?? se fait mater en deux."""
    board = chess.Board("rn1qkbnr/ppp2p1p/3p2p1/4p3/2B1P1b1/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 0 5")
    decision = Hustler(rng=random.Random(0)).choose_move(board, stockfish_analyser)
    assert decision.san == "Nxe5"
    assert "Bxd1" in decision.explanation
