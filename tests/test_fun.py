import chess

from fun_fish.fun import fun_features, fun_scores
from fun_fish.personalities import create
from fun_fish.personalities.buccaneer import Buccaneer


def move(board, san):
    return board.parse_san(san)


def test_sacrifice_and_assault():
    # Fxf7+ dans l'italienne : un fou contre un pion, avec échec
    board = chess.Board("r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")
    f = fun_features(board, move(board, "Bxf7+"))
    assert f["sacrifice"] > 0.5 and f["assault"] >= 1.0
    quiet = fun_features(board, move(board, "d3"))
    assert quiet["sacrifice"] == 0 and quiet["assault"] == 0


def test_disdain_and_unanswered():
    # Les Noirs viennent de prendre en e4 (Cxe4) : ne pas reprendre, c'est l'ignorer
    board = chess.Board()
    for san in ("e4", "e5", "Nc3", "Nf6", "Bc4", "Nxe4"):
        board.push_san(san)
    away = fun_features(board, move(board, "Qh5"))
    assert away["unanswered"] == 1.0
    back = fun_features(board, move(board, "Nxe4"))
    assert back["unanswered"] == 0.0


def test_hanging_piece():
    board = chess.Board("4k3/8/8/3p4/8/8/8/2B1K3 w - - 0 1")
    f = fun_features(board, move(board, "Bf4"))  # le fou n'est pas attaqué
    assert f["hanging"] == 0 and f["sacrifice"] == 0
    g = fun_features(board, move(board, "Bb2"))
    assert g["hanging"] == 0


def test_unexpected_only_with_flair():
    board = chess.Board("r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")
    seen = {move(board, "Bxf7+"): 0.001, move(board, "a3"): 0.001}
    scores = fun_scores(board, {"sacrifice": 1, "assault": 1, "unexpected": 1}, seen)
    assert scores[move(board, "Bxf7+")][0] > 2 and scores[move(board, "a3")][0] == 0


def test_buccaneer_profile():
    names = {s["name"]: s["default"] for s in Buccaneer.param_specs()}
    assert names["fun_budget"] == 100 and names["w_trap"] == 0.5 and names["w_develop"] == 0.6
    b = create("buccaneer", fun_budget=150)
    assert b.fun_budget == 150 and b.params()["w_assault"] == 1.0
