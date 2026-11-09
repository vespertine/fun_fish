import random

import chess

from fun_fish import personalities
from fun_fish.theory import THEORY


def test_lines_are_legal_named_and_weighted():
    for color, lines in THEORY.items():
        assert len({o.name for o in lines}) == len(lines), "noms en double"
        assert len({o.moves for o in lines}) == len(lines), "lignes en double"
        for o in lines:
            assert o.weight > 0, o.name
            board = chess.Board()
            for move in o.moves:
                assert board.is_legal(move), o.name
                board.push(move)
            # la ligne se termine sur un de nos coups (sinon son dernier coup ne serait jamais joué)
            assert (len(o.moves) % 2 == 1) == (color == chess.WHITE), o.name


def black_reply(board: chess.Board) -> chess.Move:
    """Une réponse des Noirs qui reste dans une ligne écrite si possible."""
    played = tuple(board.move_stack)
    for o in THEORY[chess.BLACK]:
        if o.moves[: len(played)] == played and len(o.moves) > len(played):
            return o.moves[len(played)]
    return next(iter(board.legal_moves))


def test_follows_a_line_then_hands_over():
    agent = personalities.create("fun_fish_2_theorie", rng=random.Random(3), theory_rate=1, theory_moves=3)
    board, played = chess.Board(), 0
    while (found := agent.theory_move(board)) is not None:
        board.push(found[0])
        played += 1
        board.push(black_reply(board))
    assert 1 <= played <= 3  # au plus theory_moves coups, puis il joue à sa façon
    assert any(o.moves[0] == board.move_stack[0] for o in THEORY[chess.WHITE])


def test_no_theory_when_rate_is_zero():
    agent = personalities.create("fun_fish_2_theorie", rng=random.Random(0), theory_rate=0)
    assert agent.theory_move(chess.Board()) is None
