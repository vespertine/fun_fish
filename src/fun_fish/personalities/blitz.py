"""Le blitzeur : un horizon court et une lecture grossière de la position."""

from __future__ import annotations

import chess

from fun_fish.analysis import Analyser
from fun_fish.personalities.base import Candidate, Decision, Personality, format_score, pick, softmax
from fun_fish.reading import VISIBLE_MATE, Reading, read_line, read_lines

BLUNDER = 150  # centipions perdus par rapport au meilleur coup réel


def mate_key(reading: Reading) -> int:
    """Mat visible pour nous : très bon (et le plus court d'abord) ; mat visible contre nous : très mauvais."""
    if reading.mate is None:
        return 0
    if 0 < reading.mate <= VISIBLE_MATE:
        return 10 - reading.mate
    if -VISIBLE_MATE <= reading.mate < 0:
        return -10 - reading.mate  # se faire mater en 1 est pire qu'en 3
    return 0


class Blitz(Personality):
    key = "blitz"
    name = "Le blitzeur"
    description = ("Voit deux coups à l'avance, compte le matériel et joue à l'instinct. "
                   "Ne rate pas un mat en deux, mais passe à côté des combinaisons longues.")
    PARAM_HELP = {
        "depth": "Profondeur de sa perception",
        "multipv": "Nombre de coups qu'il envisage",
        "horizon": "Demi-coups de lecture du matériel",
        "temperature": "Hasard entre coups qu'il juge équivalents (cp)",
        "reality_depth": "Profondeur de l'analyse « réalité » (affichage seulement)",
    }

    def __init__(self, depth: int = 8, multipv: int = 8, horizon: int = 2, temperature: float = 40.0,
                 reality_depth: int = 16, **kwargs):
        super().__init__(**kwargs)
        self.depth = depth  # profondeur de la perception
        self.multipv = multipv  # nombre de coups qu'il envisage
        self.horizon = horizon  # demi-coups de lecture du matériel
        self.temperature = temperature  # hasard entre coups qu'il juge équivalents
        self.reality_depth = reality_depth  # analyse de contrôle, pour l'affichage seulement

    # ---------- perception ----------

    def perceive(self, board: chess.Board, analyser: Analyser) -> list[Reading]:
        lines = analyser.analyse(board, multipv=self.multipv, depth=self.depth)
        return read_lines(board, lines, horizon=self.horizon)

    def shortlist(self, readings: list[Reading]) -> list[Reading]:
        """Les coups qu'il juge équivalents : même mat visible, même niveau, meilleur bilan matériel."""
        top = max((mate_key(r), r.level) for r in readings)
        pool = [r for r in readings if (mate_key(r), r.level) == top]
        best_material = max(r.material.value for r in pool)
        return [r for r in pool if r.material.value == best_material]

    def choose_move(self, board: chess.Board, analyser: Analyser) -> Decision:
        readings = self.perceive(board, analyser)
        pool = self.shortlist(readings)
        probabilities = softmax([r.score for r in pool], self.temperature)
        chosen = pool[pick(probabilities, self.rng)]

        proba_by_move = {r.move: p for r, p in zip(pool, probabilities)}
        candidates = [
            Candidate(move=r.move, san=r.san, score=r.score, mate=r.mate,
                      probability=proba_by_move.get(r.move), note=r.summary)
            for r in readings
        ]
        explanation = f"Voit {chosen.san} : {chosen.summary}."
        others = [r.san for r in pool if r.move != chosen.move]
        if others:
            explanation += f" Hésitait avec {', '.join(others)}."
        return Decision(move=chosen.move, san=chosen.san, color=board.turn, explanation=explanation,
                        candidates=candidates, reality=self.reality(board, analyser, chosen))

    # ---------- réalité ----------

    def reality(self, board: chess.Board, analyser: Analyser, chosen: Reading) -> str:
        """Ce qu'une analyse profonde pense du coup choisi (pour comprendre ses erreurs)."""
        sign = 1 if board.turn == chess.WHITE else -1

        def fmt(score: int, mate: int | None) -> str:  # point de vue des Blancs, comme le reste du panneau
            return format_score(sign * score, None if mate is None else sign * mate)

        best = analyser.analyse(board, multipv=1, depth=self.reality_depth)[0]
        after = board.copy()
        after.push(chosen.move)
        if after.is_game_over():
            return "En réalité : c'était bien le coup."
        reply = analyser.analyse(after, multipv=1, depth=self.reality_depth - 1)[0]
        real_score, real_mate = -reply.score, (None if reply.mate is None else -reply.mate)
        if best.move == chosen.move or best.score - real_score < 30:
            return f"En réalité : bon choix ({fmt(real_score, real_mate)})."
        best_reading = read_line(board, best)
        verdict = "Erreur" if best.score - real_score >= BLUNDER else "Imprécision"
        return (f"{verdict} : en réalité {chosen.san} vaut {fmt(real_score, real_mate)}. "
                f"Il fallait {best_reading.san} ({fmt(best.score, best.mate)}, {best_reading.summary}).")
