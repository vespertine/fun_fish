import chess
import pytest

from fun_fish.analysis import Line
from fun_fish.reading import (
    MaterialChange,
    describe_pieces,
    eval_level,
    material_along,
    read_line,
    read_lines,
)

P, N, B, R, Q = chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN


def pv(*ucis):
    return [chess.Move.from_uci(u) for u in ucis]


def line(board, ucis, score=0, mate=None):
    moves = tuple(pv(*ucis))
    return Line(move=moves[0], score=score, mate=mate, wdl=None, pv=moves, depth=10)


# ---------- descriptions ----------

def test_describe_pieces():
    assert describe_pieces([B]) == "un fou"
    assert describe_pieces([R]) == "une tour"
    assert describe_pieces([P, B, P]) == "un fou et deux pions"
    assert describe_pieces([Q, R, P]) == "une dame, une tour et un pion"


@pytest.mark.parametrize("change, label", [
    (MaterialChange(), "matériel égal"),
    (MaterialChange(gained=(B,)), "gagne un fou"),
    (MaterialChange(lost=(P, P)), "perd deux pions"),
    (MaterialChange(gained=(R,), lost=(N,)), "gagne la qualité"),
    (MaterialChange(gained=(B,), lost=(R,)), "perd la qualité"),
    (MaterialChange(gained=(N,), lost=(B,)), "échange un fou contre un cavalier"),
    (MaterialChange(gained=(Q,), lost=(R, B)), "gagne une dame contre une tour et un fou"),
])
def test_material_labels(change, label):
    assert change.label == label


def test_eval_levels():
    assert eval_level(10, None) == 0
    assert eval_level(50, None) == 1
    assert eval_level(-120, None) == -2
    assert eval_level(450, None) == 3
    assert eval_level(0, 4) == 3
    assert eval_level(0, -2) == -3


# ---------- bilan le long de la ligne ----------

def test_recapture_is_even():
    board = chess.Board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
    change = material_along(board, pv("e4d5", "d8d5", "b1c3"))
    assert change.label == "matériel égal"
    assert change.plies == 3  # lecture complète : dernière position posée = fin de ligne


def test_wins_undefended_knight():
    board = chess.Board("4k3/8/8/3n4/8/8/8/3RK3 w - - 0 1")
    change = material_along(board, pv("d1d5", "e8e7"))
    assert change.label == "gagne un cavalier"
    assert change.value == 3


def test_wins_the_exchange():
    board = chess.Board("4k3/8/4b3/3r4/8/2N5/8/4K3 w - - 0 1")
    change = material_along(board, pv("c3d5", "e6d5", "e1e2"))
    assert change.label == "gagne la qualité"


def test_never_reads_in_the_middle_of_an_exchange():
    """La ligne se termine sur une prise : on se rabat sur la dernière position posée."""
    board = chess.Board("4k3/8/4b3/3r4/8/2N5/8/4K3 w - - 0 1")
    assert material_along(board, pv("c3d5")).label == "matériel égal"


def test_short_horizon_misses_later_gain():
    board = chess.Board("4k3/8/8/3n4/8/8/8/3RK3 w - - 0 1")
    moves = pv("e1e2", "e8e7", "d1d5", "e7e6")
    assert material_along(board, moves).label == "gagne un cavalier"
    assert material_along(board, moves, horizon=1).label == "matériel égal"
    assert material_along(board, moves, horizon=3).label == "gagne un cavalier"


def test_short_horizon_finishes_the_exchange_in_progress():
    """Horizon 1 sur une prise défendue : le joueur voit quand même la reprise."""
    board = chess.Board("4k3/8/4b3/3r4/8/2N5/8/4K3 w - - 0 1")
    change = material_along(board, pv("c3d5", "e6d5", "e1e2"), horizon=1)
    assert change.label == "gagne la qualité"


def test_illegal_pv_is_truncated():
    board = chess.Board()
    assert material_along(board, pv("e2e4", "e2e4")).label == "matériel égal"


def test_promotion_counts_as_material():
    board = chess.Board("8/P6k/8/8/8/8/8/K7 w - - 0 1")
    change = material_along(board, pv("a7a8q", "h7g7", "a1b1"))
    assert change.value == 8


def test_invalid_horizon():
    with pytest.raises(ValueError):
        material_along(chess.Board(), pv("e2e4"), horizon=0)


# ---------- lecture complète ----------

def test_reading_summary_and_sacrifice():
    board = chess.Board("4k3/8/8/3n4/8/8/8/3RK3 w - - 0 1")
    reading = read_line(board, line(board, ["d1d5", "e8e7"], score=900))
    assert reading.summary == "gagne un cavalier · décisif"

    board = chess.Board("4k3/8/4b3/3n4/8/8/8/3RK3 w - - 0 1")
    sac = read_line(board, line(board, ["d1d5", "e6d5", "e1e2"], score=60))
    assert sac.sacrifice
    assert sac.summary == "sacrifie une tour contre un cavalier · léger avantage"


def test_mates():
    board = chess.Board("6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1")
    quick = read_line(board, line(board, ["d1d8"], mate=1))
    assert quick.visible_mate and quick.summary == "mat en 1"
    slow = read_lines(board, [line(board, ["g1f1"], mate=5)])[0]
    assert not slow.visible_mate and slow.summary == "mat en 5 (compliqué)"


def test_blackburne_trap_with_real_stockfish(stockfish_analyser):
    board = chess.Board("r1b1kbnr/pppp1ppp/8/4N1q1/2BnP3/8/PPPP1PPP/RNBQK2R w KQkq - 1 5")
    lines = stockfish_analyser.analyse(board, multipv=4, depth=14)
    readings = {r.san: r for r in read_lines(board, lines)}
    assert readings["O-O"].material.value <= -3  # le cavalier e5 tombe
    assert all(r.level < 0 for r in readings.values())  # les Noirs sont mieux partout
