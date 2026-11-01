"""Une partie entre une personnalité et un adversaire simulé, avec les mesures qui nous intéressent."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Protocol

import chess
import chess.pgn

from fun_fish.analysis import Analyser
from fun_fish.personalities import Personality
from fun_fish.reading import PIECE_VALUES

SNAPSHOT_MOVES = (10, 20, 30, 40)


class Player(Protocol):
    name: str

    def choose(self, board: chess.Board) -> chess.Move: ...


def material(board: chess.Board, color: chess.Color) -> int:
    """Bilan matériel en pions du point de vue de `color`."""
    return sum(v * (len(board.pieces(p, color)) - len(board.pieces(p, not color))) for p, v in PIECE_VALUES.items())


@dataclass
class GameRecord:
    agent: str
    params: dict
    opponent: str
    opponent_elo: int
    agent_color: str
    seed: int
    result: str = "*"
    score: float = 0.5  # du point de vue de l'agent
    termination: str = ""
    plies: int = 0
    agent_mated: bool = False  # l'agent a maté
    agent_got_mated: bool = False
    snapshots: dict = field(default_factory=dict)  # coup -> {"eval": cp, "material": pions}, point de vue agent
    agent_seconds: float = 0.0
    seconds: float = 0.0
    moves: list = field(default_factory=list)  # UCI
    pgn: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def play_game(agent: Personality, opponent: Player, opponent_elo: int, agent_color: chess.Color,
              analyser: Analyser, *, seed: int = 0, params: dict | None = None, max_plies: int = 240,
              judge_depth: int = 12, adjudicate_cp: int = 400) -> GameRecord:
    record = GameRecord(agent=agent.key, params=params or {}, opponent=opponent.name, opponent_elo=opponent_elo,
                        agent_color="white" if agent_color == chess.WHITE else "black", seed=seed)
    board = chess.Board()
    start = time.time()

    def snapshot() -> None:
        lines = analyser.analyse(board, multipv=1, depth=judge_depth)
        score = lines[0].score if lines else 0
        if board.turn != agent_color:
            score = -score
        record.snapshots[str(board.fullmove_number - 1)] = {"eval": max(-10_000, min(10_000, score)),
                                                            "material": material(board, agent_color)}

    while board.outcome(claim_draw=True) is None and len(board.move_stack) < max_plies:
        if board.turn == agent_color:
            t = time.time()
            move = agent.choose_move(board.copy(), analyser).move
            record.agent_seconds += time.time() - t
        else:
            move = opponent.choose(board.copy())
        board.push(move)
        # Photo après le coup des Noirs du coup N (N = 10, 20...)
        if board.turn == chess.WHITE and board.fullmove_number - 1 in SNAPSHOT_MOVES:
            snapshot()

    outcome = board.outcome(claim_draw=True)
    if outcome is not None:
        record.result = outcome.result()
        record.termination = outcome.termination.name.lower()
        if outcome.termination == chess.Termination.CHECKMATE:
            record.agent_mated = outcome.winner == agent_color
            record.agent_got_mated = outcome.winner != agent_color
        winner = outcome.winner
    else:  # limite de longueur : on arbitre à l'éval
        lines = analyser.analyse(board, multipv=1, depth=judge_depth)
        score = lines[0].score if lines else 0
        white_score = score if board.turn == chess.WHITE else -score
        winner = chess.WHITE if white_score >= adjudicate_cp else chess.BLACK if white_score <= -adjudicate_cp else None
        record.result = {chess.WHITE: "1-0", chess.BLACK: "0-1", None: "1/2-1/2"}[winner]
        record.termination = "adjudication"
    record.score = 0.5 if winner is None else (1.0 if winner == agent_color else 0.0)
    record.plies = len(board.move_stack)
    record.moves = [m.uci() for m in board.move_stack]
    record.seconds = time.time() - start

    game = chess.pgn.Game.from_board(board)
    game.headers.update({"Event": "fun_fish sim", "Result": record.result,
                         "White": agent.key if agent_color == chess.WHITE else opponent.name,
                         "Black": opponent.name if agent_color == chess.WHITE else agent.key})
    record.pgn = str(game)
    return record
