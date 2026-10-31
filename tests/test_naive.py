import chess

from fun_fish.naive import Instinct


def top(board):
    appeals = Instinct().appeals(board)
    return board.san(max(appeals, key=appeals.get))


def test_free_queen_is_irresistible():
    """Piège de Légal : après 5.Cxe5, la dame d1 « gratuite » attire l'œil."""
    board = chess.Board("rn1qkbnr/ppp2p1p/3p2p1/4N3/2B1P1b1/2N5/PPPP1PPP/R1BQK2R b KQkq - 0 5")
    assert top(board) == "Bxd1"


def test_fork_looks_great():
    """Blackburne : après 4.Cxe5 Dg5, Cxf7 fourchette dame et tour."""
    board = chess.Board("r1b1kbnr/pppp1ppp/8/4N1q1/2BnP3/8/PPPP1PPP/RNBQK2R w KQkq - 1 5")
    assert top(board) == "Nxf7"


def test_mate_in_one_and_hanging_piece():
    board = chess.Board("6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1")
    inst = Instinct()
    assert inst.appeal(board, chess.Move.from_uci("d1d8")) == inst.mate
    board = chess.Board("4k3/8/8/8/8/2p5/8/2R1K3 w - - 0 1")
    # Tc1-b2 ? impossible ; Tc1-d1 est sûr, Tc1xc3 prend un pion non défendu
    assert inst.appeal(board, chess.Move.from_uci("c1c3")) == 1
