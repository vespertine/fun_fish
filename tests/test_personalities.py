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


def test_regret_prefers_faster_mates():
    from fun_fish.analysis import Line
    from fun_fish.personalities.base import regret

    def line(score, mate=None):
        return Line(move=chess.Move.null(), score=score, mate=mate, wdl=None, pv=(), depth=1)

    assert regret(line(99997, 3), line(99994, 6)) == 300
    assert regret(line(300), line(250)) == 50
    assert regret(line(99997, 3), line(1500)) > 90000


def test_repetition_changes_the_analysis(stockfish_analyser):
    """Après deux allers-retours de cavaliers, Stockfish doit recevoir l'historique : la même
    position (EPD) ne partage plus son entrée de cache avec celle sans historique."""
    from fun_fish.analysis import _seen_before

    board = chess.Board()
    assert _seen_before(board) == ()
    for san in ("Nf3", "Nf6", "Ng1", "Ng8"):
        board.push_san(san)
    assert chess.Board().epd() in _seen_before(board)
    fresh = chess.Board()
    assert stockfish_analyser.analyse(board, depth=6) is not stockfish_analyser.analyse(fresh, depth=6)


def test_soundness_tightens_when_winning(fake_analyser):
    from fun_fish.personalities.base import Soundness

    board = chess.Board()
    stage = Soundness(tolerance=300, depth=4, max_candidates=12, convert_above=500, convert_tolerance=30)
    lines, converting = stage.lines(board, fake_analyser)  # meilleur coup à 0 : rien à convertir
    assert not converting and len(lines) == 8  # 0, -40, ..., -280
    fake_analyser.scores = {"e2e4": 700, "d2d4": 680, "g1f3": 600}
    lines, converting = stage.lines(board, fake_analyser)
    assert converting and [l.move.uci() for l in lines] == ["e2e4", "d2d4"]
    lines, converting = Soundness(300, 4, 12, convert_above=0).lines(board, fake_analyser)
    assert not converting and len(lines) == 3  # tolérance normale : tout ce qui est au-dessus de +4
