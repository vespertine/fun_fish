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


def test_sees_blackburne_trap_with_real_stockfish(stockfish_analyser):
    """3...Cd4 : Cxe5 est tentant, et après Dg5 l'adversaire naïf joue Cxf7 et s'effondre."""
    board = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3")
    hustler = Hustler(rng=random.Random(0), tolerance=150)
    root = stockfish_analyser.analyse(board, multipv=board.legal_moves.count(), depth=12)
    nd4 = next(line for line in root if board.san(line.move) == "Nd4")
    evaluation = hustler.evaluate(board, nd4, root[0], stockfish_analyser, plies=2)
    assert evaluation.node.hustle > 1.0
    assert evaluation.node.bait[0] == "Nxe5"


def test_maia_opponent_model_finds_legal_trap(stockfish_analyser):
    pytest.importorskip("maia2")
    board = chess.Board("rn1qkbnr/ppp2p1p/3p2p1/4p3/2B1P1b1/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 0 5")
    decision = Hustler(rng=random.Random(0), opponent_elo=1100).choose_move(board, stockfish_analyser)
    assert decision.san == "Nxe5"
    assert "1100 Elo" in decision.explanation
