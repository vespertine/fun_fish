import random

import chess

from fun_fish import personalities
from fun_fish.sim import report
from fun_fish.sim.match import material, play_game


class RandomPlayer:
    name = "random"

    def __init__(self, seed=0):
        self.rng = random.Random(seed)

    def choose(self, board):
        return self.rng.choice(list(board.legal_moves))


def test_material_balance():
    board = chess.Board("4k3/8/8/8/8/8/8/R3K3 w - - 0 1")
    assert material(board, chess.WHITE) == 5 and material(board, chess.BLACK) == -5


def test_play_game_records_metrics(fake_analyser):
    agent = personalities.create("casual", rng=random.Random(0))
    record = play_game(agent, RandomPlayer(), 1200, chess.WHITE, fake_analyser, seed=7, max_plies=60)
    assert record.plies <= 60 and len(record.moves) == record.plies
    assert record.result in {"1-0", "0-1", "1/2-1/2"}
    assert record.termination
    if record.plies >= 20:
        assert "10" in record.snapshots and {"eval", "material"} <= set(record.snapshots["10"])
    assert record.pgn.startswith("[Event")


def test_report_summarizes_by_elo(fake_analyser):
    records = []
    for elo in (1100, 1500):
        for i in range(2):
            agent = personalities.create("casual", rng=random.Random(i))
            color = chess.WHITE if i % 2 == 0 else chess.BLACK
            records.append(play_game(agent, RandomPlayer(i), elo, color, fake_analyser, seed=i, max_plies=40).to_dict())
    text = report.summarize(records)
    assert " 1100 " in text and " 1500 " in text


def test_match_param_takes_opponent_elo():
    from fun_fish.sim.runner import resolve_params
    assert resolve_params({"opponent_elo": "match", "tolerance": 150}, 1300) == {"opponent_elo": 1300, "tolerance": 150}
