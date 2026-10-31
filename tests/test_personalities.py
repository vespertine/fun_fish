import random

import chess
import pytest

from fun_fish import personalities
from fun_fish.personalities.base import softmax
from fun_fish.personalities.reference import Casual, PureStockfish


def test_softmax_sums_to_one_and_prefers_best():
    probs = softmax([100, 50, 0], temperature=50)
    assert sum(probs) == pytest.approx(1)
    assert probs[0] > probs[1] > probs[2]


def test_softmax_zero_temperature_is_argmax():
    assert softmax([10, 30, 20], temperature=0) == [0.0, 1.0, 0.0]


def test_registry_lists_reference_personalities():
    keys = {p["key"] for p in personalities.catalogue()}
    assert {"casual", "stockfish"} <= keys
    with pytest.raises(ValueError):
        personalities.create("inconnu")


def test_pure_stockfish_plays_best_line(fake_analyser):
    board = chess.Board()
    fake_analyser.scores = {"g1f3": 500}
    decision = PureStockfish().choose_move(board, fake_analyser)
    assert decision.move == chess.Move.from_uci("g1f3")
    assert decision.san == "Nf3"


def test_casual_never_plays_discarded_moves(fake_analyser):
    board = chess.Board()
    casual = Casual(max_loss=50, multipv=10, rng=random.Random(0))
    for _ in range(50):
        decision = casual.choose_move(board, fake_analyser)
        chosen = next(c for c in decision.candidates if c.move == decision.move)
        assert chosen.probability is not None
        best = decision.candidates[0].score
        assert best - chosen.score <= 50


def test_casual_varies_its_moves(fake_analyser):
    board = chess.Board()
    casual = Casual(max_loss=200, temperature=200, rng=random.Random(1))
    played = {casual.choose_move(board, fake_analyser).move for _ in range(40)}
    assert len(played) > 1


def test_decision_scores_are_from_white_point_of_view(fake_analyser):
    board = chess.Board()
    board.push_san("e4")
    fake_analyser.scores = {"e7e5": 30}
    decision = PureStockfish().choose_move(board, fake_analyser)
    assert decision.to_dict()["candidates"][0]["score"] == -30


def test_personality_does_not_mutate_board(fake_analyser):
    board = chess.Board()
    fen = board.fen()
    Casual(rng=random.Random(0)).choose_move(board, fake_analyser)
    assert board.fen() == fen
