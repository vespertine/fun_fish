"""Personnalités de référence, qui servent d'étalon et de proof of concept."""

from __future__ import annotations

import chess

from fun_fish.analysis import Analyser, Line
from fun_fish.personalities.base import Candidate, Decision, Personality, format_score, pick, softmax


def _discard_note(best: Line, line: Line) -> str:
    if best.mate is not None and best.mate > 0 and (line.mate is None or line.mate <= 0):
        return "écarté (rate le mat)"
    if line.mate is not None and line.mate < 0:
        return "écarté (se fait mater)"
    return f"écarté (−{(best.score - line.score) / 100:.2f})"


class PureStockfish(Personality):
    key = "stockfish"
    name = "Stockfish"
    description = "Joue toujours le meilleur coup de Stockfish à profondeur fixe. L'étalon, pas une personnalité."
    PARAM_HELP = {"depth": "Profondeur de recherche"}

    def __init__(self, depth: int = 14, **kwargs):
        super().__init__(**kwargs)
        self.depth = depth

    def choose_move(self, board: chess.Board, analyser: Analyser) -> Decision:
        best = analyser.analyse(board, multipv=1, depth=self.depth)[0]
        return Decision(
            move=best.move,
            san=board.san(best.move),
            color=board.turn,
            explanation=f"Meilleur coup à profondeur {self.depth} ({format_score(best.score, best.mate)}).",
            candidates=[Candidate.from_line(board, best, probability=1.0)],
        )


class Casual(Personality):
    """Personnalité « dummy » : regarde peu loin et tire au sort parmi les coups corrects."""

    key = "casual"
    name = "Le copain du club"
    description = ("Regarde quelques coups, pas très loin, et en joue un des bons au hasard, "
                   "pas forcément le meilleur.")
    PARAM_HELP = {
        "depth": "Profondeur de recherche",
        "multipv": "Nombre de coups envisagés",
        "max_loss": "Perte maximale par rapport au meilleur coup (cp)",
        "temperature": "Hasard du tirage (cp)",
    }

    def __init__(self, depth: int = 6, multipv: int = 6, max_loss: int = 150,
                 temperature: float = 60.0, **kwargs):
        super().__init__(**kwargs)
        self.depth = depth
        self.multipv = multipv
        self.max_loss = max_loss  # centipions qu'il accepte de lâcher par rapport au meilleur coup
        self.temperature = temperature  # en centipions : plus c'est haut, plus le tirage est uniforme

    def choose_move(self, board: chess.Board, analyser: Analyser) -> Decision:
        lines = analyser.analyse(board, multipv=self.multipv, depth=self.depth)
        best = lines[0]
        pool = [line for line in lines if best.score - line.score <= self.max_loss]
        probabilities = softmax([line.score for line in pool], self.temperature)
        chosen = pool[pick(probabilities, self.rng)]

        proba_by_move = {line.move: p for line, p in zip(pool, probabilities)}
        candidates = []
        for line in lines:
            candidates.append(Candidate.from_line(
                board, line,
                probability=proba_by_move.get(line.move),
                note="" if line.move in proba_by_move else _discard_note(best, line),
            ))

        san = board.san(chosen.move)
        if chosen.move == best.move:
            explanation = f"Joue le coup qu'il préfère, {san}."
        else:
            loss = (best.score - chosen.score) / 100
            explanation = (f"Joue {san} plutôt que {board.san(best.move)}, qu'il trouvait meilleur de {loss:.2f} : "
                           "il aime varier les plaisirs.")
        return Decision(move=chosen.move, san=san, color=board.turn, explanation=explanation, candidates=candidates)
