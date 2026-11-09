"""Le client UCI maison (analysis._Uci) : scores, mats, MultiPV, coups restreints, cache, historique."""

import chess

from fun_fish.analysis import MATE_SCORE, _parse


def test_parse_cp_mate_and_wdl():
    line = _parse("info depth 12 seldepth 18 multipv 2 score cp -35 wdl 100 600 300 nodes 1 pv e2e4 e7e5 g1f3")
    assert (line.score, line.mate, line.wdl, line.depth) == (-35, None, (100, 600, 300), 12)
    assert [m.uci() for m in line.pv] == ["e2e4", "e7e5", "g1f3"]
    assert _parse("info depth 5 score mate 2 pv d1h5").score == MATE_SCORE - 2
    assert _parse("info depth 5 score mate -3 pv a2a3").score == -MATE_SCORE + 3
    assert _parse("info depth 5 score cp 10 lowerbound pv e2e4").score == 10
    assert _parse("info depth 5 currmove e2e4 currmovenumber 1") is None


def test_mate_in_one_multipv_and_restriction(stockfish_analyser):
    board = chess.Board("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1")  # Ta8#
    lines = stockfish_analyser.analyse(board, multipv=3, depth=8)
    assert len(lines) == 3 and lines[0].move.uci() == "a1a8" and lines[0].mate == 1
    assert lines[0].score > lines[1].score >= lines[2].score
    only = stockfish_analyser.analyse(board, multipv=2, depth=8, moves=[chess.Move.from_uci("g1f1")])
    assert [l.move.uci() for l in only] == ["g1f1"]


def test_cache_and_history(stockfish_analyser):
    board = chess.Board()
    first = stockfish_analyser.analyse(board, multipv=2, depth=6)
    assert stockfish_analyser.analyse(board, multipv=2, depth=6) is first  # servi par le cache
    # même position, mais une répétition possible : clé différente (Stockfish compte la nulle)
    shuffled = chess.Board()
    for uci in ("g1f3", "g8f6", "f3g1", "f6g8"):
        shuffled.push_uci(uci)
    again = stockfish_analyser.analyse(shuffled, multipv=2, depth=6)
    assert again is not first and again[0].move in board.legal_moves


def test_game_over_gives_no_line(stockfish_analyser):
    mated = chess.Board("R5k1/5ppp/8/8/8/8/5PPP/6K1 b - - 1 1")
    assert stockfish_analyser.analyse(mated, multipv=1, depth=4) == []
