"""Une partie humain contre personnalité."""

from __future__ import annotations

import chess
import chess.pgn

from fun_fish.analysis import Analyser
from fun_fish.personalities import Decision, Personality


class GameError(ValueError):
    pass


_TERMINATIONS = {
    chess.Termination.CHECKMATE: "Échec et mat",
    chess.Termination.STALEMATE: "Pat",
    chess.Termination.INSUFFICIENT_MATERIAL: "Matériel insuffisant",
    chess.Termination.SEVENTYFIVE_MOVES: "Règle des 75 coups",
    chess.Termination.FIVEFOLD_REPETITION: "Quintuple répétition",
    chess.Termination.FIFTY_MOVES: "Règle des 50 coups",
    chess.Termination.THREEFOLD_REPETITION: "Triple répétition",
}


def _color_name(color: chess.Color) -> str:
    return "white" if color == chess.WHITE else "black"


class Game:
    def __init__(self, personality: Personality, human_color: chess.Color = chess.WHITE):
        self.board = chess.Board()
        self.personality = personality
        self.human_color = human_color
        self.decisions: dict[int, Decision] = {}  # index du coup dans move_stack -> décision de l'IA

    @property
    def over(self) -> bool:
        return self.board.outcome(claim_draw=True) is not None

    @property
    def engine_to_move(self) -> bool:
        return not self.over and self.board.turn != self.human_color

    def play_human(self, uci: str) -> None:
        if self.over:
            raise GameError("La partie est terminée.")
        if self.board.turn != self.human_color:
            raise GameError("Ce n'est pas à vous de jouer.")
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            raise GameError(f"Coup mal formé : {uci!r}") from None
        if move not in self.board.legal_moves:
            raise GameError(f"Coup illégal : {uci}")
        self.board.push(move)

    def play_engine(self, analyser: Analyser) -> Decision:
        if not self.engine_to_move:
            raise GameError("Ce n'est pas à l'IA de jouer.")
        decision = self.personality.choose_move(self.board.copy(), analyser)
        if decision.move not in self.board.legal_moves:
            raise RuntimeError(f"{self.personality.key} a proposé un coup illégal : {decision.move}")
        self.board.push(decision.move)
        self.decisions[len(self.board.move_stack) - 1] = decision
        return decision

    def retry(self) -> None:
        """Annule le dernier coup de l'IA pour qu'elle le rejoue (par exemple avec d'autres réglages)."""
        if not self.board.move_stack or self.board.turn != self.human_color:
            raise GameError("Le dernier coup n'est pas celui de l'IA.")
        self.board.pop()
        self.decisions.pop(len(self.board.move_stack), None)

    def undo(self) -> None:
        """Revient au dernier moment où c'était à l'humain de jouer, en annulant au moins un coup."""
        if not self.board.move_stack:
            return
        self.board.pop()
        while self.board.move_stack and self.board.turn != self.human_color:
            self.board.pop()
        self.decisions = {i: d for i, d in self.decisions.items() if i < len(self.board.move_stack)}

    def state(self) -> dict:
        board = self.board
        outcome = board.outcome(claim_draw=True)

        dests: dict[str, list[str]] = {}
        if outcome is None and board.turn == self.human_color:
            for move in board.legal_moves:
                targets = dests.setdefault(chess.square_name(move.from_square), [])
                to = chess.square_name(move.to_square)
                if to not in targets:
                    targets.append(to)

        replay = chess.Board()
        moves_san = []
        for move in board.move_stack:
            moves_san.append(replay.san(move))
            replay.push(move)

        last = board.move_stack[-1] if board.move_stack else None
        decision = self.decisions.get(len(board.move_stack) - 1)

        if outcome is not None:
            reason = _TERMINATIONS.get(outcome.termination, "Partie terminée")
            winner = {chess.WHITE: "les Blancs gagnent", chess.BLACK: "les Noirs gagnent", None: "nulle"}[outcome.winner]
            status = f"{reason} : {winner}."
        else:
            side = "Blancs" if board.turn == chess.WHITE else "Noirs"
            status = f"Aux {side} de jouer" + (" (échec !)" if board.is_check() else "") + "."

        return {
            "fen": board.fen(),
            "turn": _color_name(board.turn),
            "human_color": _color_name(self.human_color),
            "personality": {"key": self.personality.key, "name": self.personality.name,
                            "params": self.personality.params()},
            "dests": dests,
            "last_move": [chess.square_name(last.from_square), chess.square_name(last.to_square)] if last else None,
            "check": board.is_check(),
            "game_over": outcome is not None,
            "result": outcome.result() if outcome else "*",
            "status": status,
            "moves": moves_san,
            "engine_to_move": self.engine_to_move,
            "can_retry": bool(board.move_stack) and board.turn == self.human_color,
            "decision": decision.to_dict() if decision else None,
            "pgn": self.pgn(),
        }

    def pgn(self) -> str:
        game = chess.pgn.Game.from_board(self.board)
        ai = f"fun_fish : {self.personality.name}"
        game.headers["Event"] = "fun_fish"
        game.headers["White"] = "Humain" if self.human_color == chess.WHITE else ai
        game.headers["Black"] = ai if self.human_color == chess.WHITE else "Humain"
        outcome = self.board.outcome(claim_draw=True)
        game.headers["Result"] = outcome.result() if outcome else "*"
        return str(game)
