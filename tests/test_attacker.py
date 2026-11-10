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


def test_goes_for_scholars_mate_against_naive_opponent():
    """Après 1.e4 e5 2.Fc4 Cc6, Dh5 menace Dxf7# : sur Lichess, sous 1000 Elo, Cf6?? 9 % du temps."""
    pytest.importorskip("maia2")
    from fun_fish.analysis import Analyser
    from fun_fish.engines import find_stockfish

    with Analyser(find_stockfish(download=False), threads=1, hash_mb=16) as analyser:  # neuf : l'équilibre testé
        check_scholars_mate(analyser)  # est fin, il ne doit pas dépendre des tests précédents


def check_scholars_mate(stockfish_analyser):
    board = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 2 3")
    attacker = Attacker(rng=random.Random(0), temperature=0.05, opponent_elo=900)
    evals = {}
    root = stockfish_analyser.analyse(board, multipv=12, depth=10)
    for line in root:
        if board.san(line.move) in ("Qh5", "Nf3"):
            evals[board.san(line.move)] = attacker.scored(
                attacker.evaluate(board, line, root[0], stockfish_analyser, moves=2), root[0])
    assert evals["Qh5"].outcome.mate > 0.05 and "Qxf7#" in evals["Qh5"].outcome.best[1]
    assert evals["Nf3"].outcome.mate == 0
    # 9 % de mat ne paient pas le demi-pion que coûte Dh5... sauf pour qui tient vraiment à mater.
    assert evals["Qh5"].score < evals["Nf3"].score
    attacker.w_mate = 10
    assert attacker.scored(evals["Qh5"], root[0]).score > attacker.scored(evals["Nf3"], root[0]).score


def test_maia_model_mentions_elo(stockfish_analyser):
    pytest.importorskip("maia2")
    board = chess.Board("rn1qkbnr/ppp2p1p/3p2p1/4p3/2B1P1b1/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 0 5")
    decision = Attacker(rng=random.Random(0), opponent_elo=1100, temperature=0.05).choose_move(board, stockfish_analyser)
    assert decision.san == "Nxe5"
    assert "1100 Elo" in decision.explanation


def test_panache_also_lets_style_speak_when_material_is_all_the_attack_sees():
    """Gagnant, avec du panache, un gain de matériel ne doit plus faire taire le style (via l'occasion)."""
    from fun_fish.analysis import Line
    from fun_fish.personalities.attacker import CandidateEval, Outcome

    def candidate(uci, win):
        line = Line(move=chess.Move.from_uci(uci), score=800, mate=None, wdl=None, pv=(), depth=6)
        return CandidateEval(line, loss=0, outcome=Outcome(win=win, value=800))

    for cls in ("attacker", "romantic"):
        a = personalities.create(cls, panache=1, w_eval=0)
        a._degree, a._luxury = 1.0, 0
        greedy, stylish = candidate("e2e4", 1.0), candidate("d2d4", 0.0)
        evals = [a.scored(greedy, greedy.line), a.scored(stylish, stylish.line)]
        scores = a.combine(evals, {greedy.line.move: 0.5, stylish.line.move: 1.0})
        assert scores[1] > scores[0], cls  # le plus stylé l'emporte : le gain ne compte plus
