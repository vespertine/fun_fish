from __future__ import annotations

import chess
import pytest

from fun_fish.analysis import Analyser, Line
from fun_fish.engines import StockfishNotFound, find_stockfish


class FakeAnalyser:
    """Analyseur déterministe : les coups légaux, dans l'ordre, avec des scores décroissants."""

    engine_name = "FakeFish"

    def __init__(self, scores: dict[str, int] | None = None, step: int = 40):
        self.scores = scores or {}
        self.mates: dict[str, int] = {}  # coup uci -> mat en N
        self.pvs: dict[str, list[str]] = {}  # coup uci -> variation complète
        self.step = step
        self.calls = 0

    def analyse(self, board: chess.Board, *, multipv: int = 1, **_limits) -> list[Line]:
        self.calls += 1
        lines = []
        for i, move in enumerate(board.legal_moves):
            uci = move.uci()
            mate = self.mates.get(uci)
            score = 100_000 - mate if mate else self.scores.get(uci, -i * self.step)
            pv = tuple(chess.Move.from_uci(u) for u in self.pvs.get(uci, [uci]))
            lines.append(Line(move=move, score=score, mate=mate, wdl=None, pv=pv, depth=1))
        lines.sort(key=lambda line: line.score, reverse=True)
        return lines[:multipv]


@pytest.fixture
def fake_analyser() -> FakeAnalyser:
    return FakeAnalyser()


@pytest.fixture(scope="session")
def stockfish_analyser():
    try:
        path = find_stockfish(download=False)
    except StockfishNotFound:
        pytest.skip("Stockfish absent (lancer `fun-fish stockfish` pour le télécharger)")
    with Analyser(path, threads=1, hash_mb=16) as analyser:
        yield analyser
