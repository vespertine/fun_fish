import random

import chess
import pytest

from fun_fish import personalities
from fun_fish.personalities.attacker import Attacker, settled_material


def test_registered_with_params():
    a = personalities.create("attacker", tolerance=200, w_mate=5)
    assert a.tolerance == 200 and a.w_mate == 5.0


def test_settled_material_finishes_the_exchange():
    # Les Blancs prennent le cavalier e5, les Noirs reprennent : bilan nul après l'échange.
    board = chess.Board("4k3/8/3p4/4n3/8/8/8/4RK2 w - - 0 1")
    pv = tuple(chess.Move.from_uci(u) for u in ("e1e5", "d6e5"))
    assert settled_material(board, pv, chess.WHITE) == 5 - 1 - 5 + 0  # tour contre cavalier : -2


def test_plays_a_legal_move_with_fake_engine(fake_analyser):
    board = chess.Board()
    decision = Attacker(rng=random.Random(0), deep_candidates=2).choose_move(board, fake_analyser)
    assert board.is_legal(decision.move) and decision.candidates and decision.reality


def test_goes_for_scholars_mate_against_naive_opponent(stockfish_analyser):
    """Après 1.e4 e5 2.Fc4 Cc6, Dh5 menace Dxf7# : un joueur à 1100 Elo répond Cf6?? 15 % du temps."""
    pytest.importorskip("maia2")
    board = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 2 3")
    attacker = Attacker(rng=random.Random(0), temperature=0.05, opponent_elo=1100)
    evals = {}
    root = stockfish_analyser.analyse(board, multipv=12, depth=10)
    for line in root:
        if board.san(line.move) in ("Qh5", "Nf3"):
            evals[board.san(line.move)] = attacker.scored(
                attacker.evaluate(board, line, root[0], stockfish_analyser, moves=2), root[0])
    assert evals["Qh5"].outcome.mate > 0.05
    assert evals["Qh5"].score > evals["Nf3"].score


def test_maia_model_mentions_elo(stockfish_analyser):
    pytest.importorskip("maia2")
    board = chess.Board("rn1qkbnr/ppp2p1p/3p2p1/4p3/2B1P1b1/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 0 5")
    decision = Attacker(rng=random.Random(0), opponent_elo=1100, temperature=0.05).choose_move(board, stockfish_analyser)
    assert decision.san == "Nxe5"
    assert "1100 Elo" in decision.explanation
