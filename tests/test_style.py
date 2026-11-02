import random

import chess

from fun_fish import personalities
from fun_fish.analysis import Line
from fun_fish.style import Style, activity, development, king_pressure, open_lines


def _line(board: chess.Board, uci: str, score: int = 0) -> Line:
    move = chess.Move.from_uci(uci)
    return Line(move=move, score=score, mate=None, wdl=None, pv=(move,), depth=1)


def test_measures():
    board = chess.Board()
    developed = chess.Board("r1bqkbnr/pppppppp/2n5/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 2 2")
    assert development(developed, chess.WHITE) == development(board, chess.WHITE) + 1
    castled = chess.Board("rnbqkbnr/pppppppp/8/8/8/5NP1/PPPPPPBP/RNBQ1RK1 b kq - 0 4")
    assert development(castled, chess.WHITE) == 2 + 1.5
    tension = chess.Board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
    assert open_lines(tension, chess.WHITE) > open_lines(board, chess.WHITE)
    assert activity(tension, chess.WHITE) > activity(board, chess.WHITE)
    aimed = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 0 3")
    assert king_pressure(aimed, chess.WHITE) > king_pressure(board, chess.WHITE)


def test_neutral_style_gives_no_bonus():
    board = chess.Board()
    lines = [_line(board, "e2e4"), _line(board, "a2a3")]
    assert set(Style().bonuses(board, lines, lines[0]).values()) == {0.0}


def test_development_preferred_in_opening():
    board = chess.Board()
    lines = [_line(board, "h2h3", 20), _line(board, "g1f3", 20), _line(board, "a2a3", 20)]
    style = Style(w_develop=1.0)
    bonus = style.bonuses(board, lines, lines[0])
    assert max(bonus, key=bonus.get).uci() == "g1f3"
    assert style.reasons[chess.Move.from_uci("g1f3")] == "il se développe"


def test_sacrifice_and_initiative_reward_material_given():
    # Les Blancs peuvent donner le fou en f7 (Fxf7+ Rxf7) : même éval que le coup calme.
    board = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")
    sac = chess.Move.from_uci("c4f7")
    lines = [Line(sac, 0, None, None, (sac, chess.Move.from_uci("e8f7")), 1), _line(board, "d2d3", 0)]
    bonus = Style(w_sacrifice=1.0).bonuses(board, lines, lines[1])
    assert bonus[sac] > 0
    bonus = Style(w_initiative=1.0).bonuses(board, lines, lines[1])
    assert bonus[sac] > 0


def test_romantic_is_a_styled_attacker(fake_analyser):
    romantic = personalities.create("romantic", rng=random.Random(0), deep_candidates=2, w_open=0)
    assert romantic.w_develop > 0 and romantic.w_open == 0
    decision = romantic.choose_move(chess.Board(), fake_analyser)
    assert decision.move in chess.Board().legal_moves


def test_style_first_keeps_only_favourites(fake_analyser):
    fake_analyser.step = 0  # tous les coups se valent
    attacker = personalities.create("attacker", rng=random.Random(0), w_develop=1, style_first=4, attack_moves=1)
    decision = attacker.choose_move(chess.Board(), fake_analyser)
    assert len(decision.candidates) == 4
    assert all(chess.Board().piece_type_at(c.move.from_square) == chess.KNIGHT for c in decision.candidates)


def test_technique_pushes_passed_pawns_and_stops_theirs():
    from fun_fish.style import passed_pawns, technique

    board = chess.Board("6k1/8/P7/8/8/8/6p1/R5K1 w - - 0 1")
    assert passed_pawns(board, chess.WHITE) == [chess.A6]
    pushed = chess.Board("6k1/P7/8/8/8/8/6p1/R5K1 b - - 0 1")
    assert technique(pushed, chess.WHITE) > technique(board, chess.WHITE)
    stopped = chess.Board("6k1/8/P7/8/8/8/6p1/4K1R1 b - - 0 1")  # la tour bloque g1
    assert technique(stopped, chess.WHITE) > technique(chess.Board("6k1/8/P7/8/8/8/6p1/R3K3 b - - 0 1"), chess.WHITE)


def test_conversion_weight_alone_activates_technique():
    board = chess.Board("6k1/8/P7/8/8/8/6p1/R5K1 w - - 0 1")
    lines = [_line(board, "a6a7", 600), _line(board, "a1b1", 600)]
    style = Style()
    assert set(style.bonuses(board, lines, lines[0]).values()) == {0.0}
    bonus = style.bonuses(board, lines, lines[0], conversion=1.0)
    assert bonus[chess.Move.from_uci("a6a7")] > bonus[chess.Move.from_uci("a1b1")]
