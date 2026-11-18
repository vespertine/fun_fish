import zlib

import chess
import pytest

from fun_fish import fairplay


class StubEngine:
    """Le « meilleur coup » est `choice(board)`, à éval `score` ; un autre vaut `second`. Un autre coup coûte 50 cp."""

    def __init__(self, score=0, second=-10):
        self.score, self.second, self.calls, self.limits = score, second, 0, set()

    def analyse(self, board, limit, multipv=1):
        self.calls += 1
        self.limits.add((limit.depth, limit.time))
        side = board.turn
        best = choice(board)
        info = [{"score": chess.engine.PovScore(chess.engine.Cp(self.score), side), "pv": [best]}]
        other = next((m for m in board.legal_moves if m != best), None)
        if multipv > 1 and other:
            info.append({"score": chess.engine.PovScore(chess.engine.Cp(self.second), side), "pv": [other]})
        if multipv == 1:  # position après notre coup : vue par l'adversaire, qui a gagné 50 cp ; un seul dict,
            return {**info[0], "score": chess.engine.PovScore(chess.engine.Cp(-self.score + 50), side)}  # comme SF
        return info


def choice(board, shift=0):
    legal = sorted(board.legal_moves, key=lambda m: m.uci())
    return legal[(zlib.crc32(board.fen().encode()) + shift) % len(legal)]


def game(plies=60):
    """Une partie où les Blancs jouent le choix du moteur, les Noirs un autre coup."""
    board, moves = chess.Board(), []
    while len(moves) < plies and not board.is_game_over():
        move = choice(board) if board.turn == chess.WHITE else choice(board, 1)
        moves.append(board.san(move))
        board.push(move)
    return moves


def clocks(times, plies, initial=600):
    """Pendules (centièmes) après chaque demi-coup, chaque camp mettant times[i % len] secondes."""
    left, out = [initial * 100] * 2, []
    for ply in range(plies):
        left[ply % 2] -= int(times[(ply // 2) % len(times)] * 100)
        out.append(left[ply % 2])
    return out


def test_think_times_come_from_the_clocks():
    assert fairplay.think_times([6000, 6000, 5900, 5850, 5800], 1) == [None, None, 2.0, 2.5, 2.0]


def test_an_engine_copier_with_a_steady_rhythm_is_suspicious():
    moves = game()
    engine = StubEngine()
    report = fairplay.analyse(moves, chess.WHITE, engine, clocks([9, 10, 11], len(moves)), 600)
    assert report.moves == len(moves[::2]) - fairplay.OPENING  # l'ouverture n'est pas comptée
    assert report.match == report.near == 1 and report.acpl == 0 and report.cv < 0.2
    assert fairplay.suspicious(report)
    assert engine.limits == {(fairplay.DEPTH, fairplay.MOVE_TIME)}  # une analyse ne bloque jamais


def test_a_varied_rhythm_or_human_moves_are_not_suspicious():
    moves = game()
    varied = fairplay.analyse(moves, chess.WHITE, StubEngine(), clocks([1, 30, 3, 60, 2], len(moves)), 600)
    assert varied.match == 1 and varied.cv > fairplay.CV and not fairplay.suspicious(varied)
    human = fairplay.analyse(moves, chess.BLACK, StubEngine(), clocks([9, 10, 11], len(moves)), 600)
    assert human.match == human.near == 0 and human.acpl == 50 and not fairplay.suspicious(human)
    blind = fairplay.analyse(moves, chess.WHITE, StubEngine())  # sans pendule : la précision ne suffit pas
    assert blind.cv is None and not fairplay.suspicious(blind)


def test_decided_positions_and_obvious_moves_are_not_counted():
    moves = game()
    assert fairplay.analyse(moves, chess.WHITE, StubEngine(score=900, second=890)).moves == 0
    obvious = fairplay.analyse(moves, chess.WHITE, StubEngine(second=-200), clocks([10], len(moves)), 600)
    assert obvious.moves == 0 and obvious.obvious > 3 and obvious.obvious_ratio is None  # aucun coup difficile


def test_moves_in_time_trouble_do_not_count_for_the_rhythm():
    moves = game()
    report = fairplay.analyse(moves, chess.WHITE, StubEngine(), clocks([10], len(moves), initial=200), 200)
    assert report.cv is None  # sous 10 % du temps initial dès le 18e coup : trop peu de temps mesurés


@pytest.mark.parametrize("change, expected", [
    ({}, True), ({"moves": 9}, False), ({"near": 0.59}, False), ({"acpl": 26}, False), ({"cv": 0.26}, False),
    ({"cv": 0.26, "obvious_ratio": 2.0}, False), ({"cv": None}, False), ({"match": 0}, True)])
def test_precision_and_rhythm_must_agree(change, expected):
    report = fairplay.Report(**{"moves": 10, "match": 0.6, "near": 0.6, "acpl": 25, "cv": 0.25, **change})
    assert fairplay.suspicious(report) is expected
