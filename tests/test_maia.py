import chess
import pytest

from fun_fish.sim import maia


def test_elo_categories_match_maia2():
    assert [maia.elo_category(e) for e in (800, 1099, 1100, 1250, 1999, 2000, 2500)] == [0, 0, 1, 2, 9, 10, 10]


def test_encoding_ignores_unplayable_en_passant():
    pytest.importorskip("numpy")
    board = chess.Board()
    board.push_san("e4")  # case en passant e3, mais aucune prise possible
    planes = maia.encode(board)
    assert planes.shape == (18, 8, 8) and planes[17].sum() == 0
    assert planes[12].sum() == 0  # trait aux Noirs


@pytest.mark.skipif(not (maia.onnx_ready(download=False) and maia.torch_ready()), reason="ONNX et PyTorch requis")
def test_onnx_and_torch_backends_agree():
    board = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 2 3")
    board.push_san("Qh5")
    a = maia.Maia(backend="onnx").probabilities(board, 1100)
    b = maia.Maia(backend="torch").probabilities(board, 1100)
    assert a.keys() == b.keys()
    assert max(abs(a[m] - b[m]) for m in a) < 1e-4
