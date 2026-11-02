"""Une partie humain contre personnalité."""

from __future__ import annotations

import datetime
import time

import chess
import chess.pgn

from fun_fish import rules
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
        self.settings: dict[int, dict] = {}  # index du coup -> personnalité, réglages et durée de réflexion

    @property
    def over(self) -> bool:
        return rules.outcome(self.board) is not None

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
        start = time.monotonic()
        decision = self.personality.choose_move(self.board.copy(), analyser)
        if decision.move not in self.board.legal_moves:
            raise RuntimeError(f"{self.personality.key} a proposé un coup illégal : {decision.move}")
        self.board.push(decision.move)
        ply = len(self.board.move_stack) - 1
        self.decisions[ply] = decision
        self.settings[ply] = {"personality": self.personality.key, "params": self.personality.params(),
                              "seconds": round(time.monotonic() - start, 2)}
        return decision

    def retry(self) -> None:
        """Annule le dernier coup de l'IA pour qu'elle le rejoue (par exemple avec d'autres réglages)."""
        if not self.board.move_stack or self.board.turn != self.human_color:
            raise GameError("Le dernier coup n'est pas celui de l'IA.")
        self.board.pop()
        self.decisions.pop(len(self.board.move_stack), None)
        self.settings.pop(len(self.board.move_stack), None)

    def undo(self) -> None:
        """Revient au dernier moment où c'était à l'humain de jouer, en annulant au moins un coup."""
        if not self.board.move_stack:
            return
        self.board.pop()
        while self.board.move_stack and self.board.turn != self.human_color:
            self.board.pop()
        self.decisions = {i: d for i, d in self.decisions.items() if i < len(self.board.move_stack)}
        self.settings = {i: c for i, c in self.settings.items() if i < len(self.board.move_stack)}

    def state(self) -> dict:
        board = self.board
        outcome = rules.outcome(board)

        dests: dict[str, list[str]] = {}
        if outcome is None and board.turn == self.human_color:
            for move in board.legal_moves:
                targets = dests.setdefault(chess.square_name(move.from_square), [])
                to = chess.square_name(move.to_square)
                if to not in targets:
                    targets.append(to)

        replay = chess.Board()
        moves_san = []
        history = []  # un élément par demi-coup : de quoi revoir la position et le raisonnement de l'IA
        for ply, move in enumerate(board.move_stack):
            by_human = replay.turn == self.human_color
            san = replay.san(move)
            replay.push(move)
            moves_san.append(san)
            past = self.decisions.get(ply)
            history.append({
                "san": san,
                "uci": move.uci(),
                "last_move": [chess.square_name(move.from_square), chess.square_name(move.to_square)],
                "fen": replay.fen(),
                "check": replay.is_check(),
                "by": "human" if by_human else "ai",
                "decision": past.to_dict() if past else None,
            })

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
            "start_fen": chess.STARTING_FEN,
            "history": history,
            "engine_to_move": self.engine_to_move,
            "can_retry": bool(board.move_stack) and board.turn == self.human_color,
            "decision": decision.to_dict() if decision else None,
            "pgn": self.pgn(),
        }

    def debug_export(self, engine: str = "?") -> dict:
        """Tout ce qu'il faut pour rejouer et comprendre la partie : `fun-fish replay FICHIER`."""
        replay = chess.Board()
        moves = []
        for ply, move in enumerate(self.board.move_stack):
            entry = {"ply": ply, "san": replay.san(move), "uci": move.uci(), "fen_before": replay.fen(),
                     "by": "human" if replay.turn == self.human_color else "ai"}
            if ply in self.settings:
                entry.update(self.settings[ply])
                entry["decision"] = self.decisions[ply].to_dict()
            moves.append(entry)
            replay.push(move)
        return {
            "format": "fun_fish-debug/1",
            "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "engine": engine,
            "human_color": _color_name(self.human_color),
            "start_fen": chess.STARTING_FEN,
            "fen": self.board.fen(),
            "personality": {"key": self.personality.key, "params": self.personality.params()},
            "pgn": self.pgn(),
            "moves": moves,
        }

    def pgn(self) -> str:
        game = chess.pgn.Game.from_board(self.board)
        ai = f"fun_fish : {self.personality.name}"
        game.headers["Event"] = "fun_fish"
        game.headers["White"] = "Humain" if self.human_color == chess.WHITE else ai
        game.headers["Black"] = ai if self.human_color == chess.WHITE else "Humain"
        outcome = rules.outcome(self.board)
        game.headers["Result"] = outcome.result() if outcome else "*"
        return str(game)
