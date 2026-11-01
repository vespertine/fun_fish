"""Le cogneur : cherche à mater ou à gagner beaucoup de matériel, vite, contre un adversaire modélisé.

1. **Santé** : on ne garde que nos coups qui perdent au plus `tolerance` centipions si
   l'adversaire répond juste (au plus `max_candidates`).
2. **Adversaire modélisé** : Maia-2 à `opponent_elo`, ou à défaut l'adversaire naïf à
   l'instinct (voir `fun_fish.opponent`). À chacun de ses coups, on suit ses réponses
   probables (au plus `branching`, jusqu'à `plausible_mass` de probabilité) ; le reste du
   temps, on suppose qu'il joue le meilleur coup selon Stockfish.
3. **Nous** : on joue le meilleur coup selon Stockfish à `attack_depth`, sur `attack_moves`
   coups. On s'arrête dès qu'un mat en `mate_within` coups ou moins est sur l'échiquier.
4. **Mesures**, sur l'arbre ainsi pondéré :
   - **mat** = probabilité de mater (ou d'avoir un mat forcé court) dans l'horizon ;
   - **gain** = probabilité de gagner au moins `big_gain` pions de matériel ;
   - **éval** = éval attendue au bout, par rapport à un jeu correct de l'adversaire.

score = w_mate × mat + w_win × gain + w_eval × éval (pions) − w_loss × perte (pions), puis
tirage softmax de température `temperature`. On évalue d'abord tous les candidats sur un
seul coup adverse, puis les `deep_candidates` meilleurs sur tout l'horizon.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess

from fun_fish.analysis import Analyser, Line
from fun_fish.opponent import MaiaModel, OpponentModel, make_model
from fun_fish.personalities.base import Candidate, Decision, Personality, format_score, pick, softmax
from fun_fish.reading import PIECE_VALUES

CLIP = 1000  # évals bornées à ±10 pions
SETTLE_PLIES = 6  # on finit les échanges en cours au plus sur ce nombre de demi-coups


def material(board: chess.Board, color: chess.Color) -> int:
    return sum(v * (len(board.pieces(p, color)) - len(board.pieces(p, not color))) for p, v in PIECE_VALUES.items())


def settled_material(board: chess.Board, pv: tuple[chess.Move, ...], color: chess.Color) -> int:
    """Bilan matériel une fois les prises en cours terminées le long de la variation."""
    b = board.copy(stack=False)
    for i, move in enumerate(pv[:SETTLE_PLIES]):
        if not b.is_legal(move) or (i >= 1 and not (b.is_capture(move) or move.promotion)):
            break
        b.push(move)
    return material(b, color)


@dataclass
class Outcome:
    """Bilan d'un sous-arbre, pondéré par les probabilités de l'adversaire. Point de vue : nous."""

    mate: float = 0.0  # probabilité de mater
    win: float = 0.0  # probabilité de gagner au moins big_gain pions
    value: float = 0.0  # éval attendue au bout (cp)
    best: tuple[float, list[str]] | None = None  # (probabilité, coups) : la réussite la plus probable

    def add(self, other: Outcome, weight: float, prefix: list[str]) -> None:
        self.mate += weight * other.mate
        self.win += weight * other.win
        self.value += weight * other.value
        if other.best is not None:
            p = weight * other.best[0]
            if self.best is None or p > self.best[0]:
                self.best = (p, prefix + other.best[1])


@dataclass
class CandidateEval:
    line: Line
    loss: int
    outcome: Outcome
    score: float = 0.0

    @property
    def note(self) -> str:
        o = self.outcome
        return f"mat {o.mate:.0%} · gain {max(0.0, o.win - o.mate):.0%} · éval {o.value / 100:+.1f}"


class Attacker(Personality):
    key = "attacker"
    name = "Le cogneur"
    description = ("Joue pour mater ou rafler du matériel vite, en pariant sur les réponses probables "
                   "de son adversaire. Brutal, mais réfutable si on se défend bien.")
    PARAM_HELP = {
        "opponent_elo": "Elo de l'adversaire imaginé, modélisé par Maia-2 (0 = adversaire naïf à l'instinct)",
        "tolerance": "Perte maximale acceptée si l'adversaire répond juste (centipions)",
        "soundness_depth": "Profondeur du filtre de santé",
        "attack_depth": "Profondeur de Stockfish pour nos coups et les réponses justes de l'adversaire",
        "attack_moves": "Coups adverses simulés (horizon de l'attaque)",
        "branching": "Réponses adverses suivies à chaque coup",
        "plausible_mass": "Probabilité cumulée des réponses suivies (0 à 1)",
        "min_probability": "Probabilité minimale pour suivre une réponse (0 à 1)",
        "mate_within": "Un mat forcé en N coups ou moins compte comme un mat",
        "big_gain": "Gain de matériel qui compte comme une réussite (pions)",
        "w_mate": "Poids de la probabilité de mater",
        "w_win": "Poids de la probabilité de gros gain matériel",
        "w_eval": "Poids de l'éval attendue au bout (par pion)",
        "w_loss": "Poids de la perte acceptée (par pion)",
        "temperature": "Hasard du choix final (en points de score)",
        "max_candidates": "Coups sains examinés",
        "deep_candidates": "Dont simulés sur tout l'horizon",
        "instinct": "Adversaire naïf : part de l'instinct face à Stockfish (0 à 1)",
        "caution": "Adversaire naïf : attention aux reprises (0 = aveugle, 1 = lucide)",
        "naivety": "Adversaire naïf : hasard (cp)",
    }

    def __init__(self, opponent_elo: int = 0, tolerance: int = 150, soundness_depth: int = 10,
                 attack_depth: int = 8, attack_moves: int = 3, branching: int = 3, plausible_mass: float = 0.85,
                 min_probability: float = 0.08, mate_within: int = 3, big_gain: int = 3,
                 w_mate: float = 3.0, w_win: float = 1.5, w_eval: float = 0.3, w_loss: float = 1.0,
                 temperature: float = 0.3, max_candidates: int = 12, deep_candidates: int = 5,
                 instinct: float = 0.7, caution: float = 0.5, naivety: float = 60.0, **kwargs):
        super().__init__(**kwargs)
        self.opponent_elo = opponent_elo
        self.tolerance = tolerance
        self.soundness_depth = soundness_depth
        self.attack_depth = attack_depth
        self.attack_moves = attack_moves
        self.branching = branching
        self.plausible_mass = plausible_mass
        self.min_probability = min_probability
        self.mate_within = mate_within
        self.big_gain = big_gain
        self.w_mate, self.w_win, self.w_eval, self.w_loss = w_mate, w_win, w_eval, w_loss
        self.temperature = temperature
        self.max_candidates = max_candidates
        self.deep_candidates = deep_candidates
        self.instinct, self.caution, self.naivety = instinct, caution, naivety
        self._model: OpponentModel | None = None

    @property
    def model(self) -> OpponentModel:
        if self._model is None:
            self._model = make_model(self.opponent_elo, instinct=self.instinct, caution=self.caution,
                                     naivety=self.naivety)
        return self._model

    # ---------- simulation ----------

    def their_turn(self, position: chess.Board, analyser: Analyser, moves_left: int, us: chess.Color,
                   base: int) -> Outcome:
        """L'adversaire a le trait : on suit ses réponses probables, le reste du temps il joue juste."""
        probs = self.model.probabilities(position, analyser)
        best = analyser.analyse(position, multipv=1, depth=self.attack_depth)[0].move
        followed, mass = [], 0.0
        for move, p in sorted(probs.items(), key=lambda x: -x[1]):
            if len(followed) >= self.branching or mass >= self.plausible_mass or p < self.min_probability:
                break
            followed.append(move)
            mass += p
        weights = {m: probs[m] for m in followed}
        weights[best] = weights.get(best, 0.0) + (1.0 - sum(weights.values()))

        outcome = Outcome()
        for move, weight in weights.items():
            if weight <= 0:
                continue
            san = position.san(move)
            child = position.copy(stack=False)
            child.push(move)
            if child.is_checkmate():
                sub = Outcome(value=-CLIP)
            elif child.is_game_over():
                sub = Outcome(value=0.0)
            else:
                sub = self.our_turn(child, analyser, moves_left, us, base)
            outcome.add(sub, weight, [san])
        return outcome

    def our_turn(self, position: chess.Board, analyser: Analyser, moves_left: int, us: chess.Color,
                 base: int) -> Outcome:
        """À nous : le meilleur coup de Stockfish, jusqu'à l'horizon ou à un mat en vue."""
        line = analyser.analyse(position, multipv=1, depth=self.attack_depth)[0]
        if line.mate is not None and 0 < line.mate <= self.mate_within:
            return Outcome(mate=1.0, win=1.0, value=CLIP, best=(1.0, [position.san(line.move), f"(mat en {line.mate})"]))
        gain = settled_material(position, line.pv, us) - base
        if moves_left <= 1:
            won = gain >= self.big_gain
            return Outcome(win=1.0 if won else 0.0, value=max(-CLIP, min(CLIP, line.score)),
                           best=(1.0, [position.san(line.move), f"(+{gain} pions)"]) if won else None)
        child = position.copy(stack=False)
        child.push(line.move)
        if child.is_game_over():
            return Outcome(value=0.0)
        sub = self.their_turn(child, analyser, moves_left - 1, us, base)
        if sub.best is not None:
            sub.best = (sub.best[0], [position.san(line.move), *sub.best[1]])
        return sub

    def evaluate(self, board: chess.Board, line: Line, best: Line, analyser: Analyser, moves: int) -> CandidateEval:
        loss = max(0, best.score - line.score)
        position = board.copy(stack=False)
        position.push(line.move)
        if position.is_checkmate():
            return CandidateEval(line, loss, Outcome(mate=1.0, win=1.0, value=CLIP, best=(1.0, [])))
        if position.is_game_over():
            return CandidateEval(line, loss, Outcome())
        base = material(board, board.turn)
        outcome = self.their_turn(position, analyser, moves, board.turn, base)
        if outcome.best is not None:
            outcome.best = (outcome.best[0], [board.san(line.move), *outcome.best[1]])
        return CandidateEval(line, loss, outcome)

    def scored(self, e: CandidateEval, best: Line) -> CandidateEval:
        o = e.outcome
        reference = max(-CLIP, min(CLIP, best.score))
        e.score = (self.w_mate * o.mate + self.w_win * o.win + self.w_eval * (o.value - reference) / 100
                   - self.w_loss * e.loss / 100)
        return e

    # ---------- choix ----------

    def choose_move(self, board: chess.Board, analyser: Analyser) -> Decision:
        root = analyser.analyse(board, multipv=min(board.legal_moves.count(), self.max_candidates),
                                depth=self.soundness_depth)
        best = root[0]
        sound = [l for l in root if best.score - l.score <= self.tolerance]
        children = []
        for line in sound:
            child = board.copy(stack=False)
            child.push(line.move)
            children.append(child)
        self.model.prefetch(children)

        evals = [self.scored(self.evaluate(board, line, best, analyser, moves=1), best) for line in sound]
        if self.attack_moves > 1:
            evals.sort(key=lambda e: -e.score)
            evals[: self.deep_candidates] = [
                self.scored(self.evaluate(board, e.line, best, analyser, moves=self.attack_moves), best)
                for e in evals[: self.deep_candidates]
            ]

        probabilities = softmax([e.score for e in evals], self.temperature)
        chosen = evals[pick(probabilities, self.rng)]
        candidates = [
            Candidate(move=e.line.move, san=board.san(e.line.move), score=e.line.score, mate=e.line.mate,
                      probability=p, note=e.note)
            for e, p in sorted(zip(evals, probabilities), key=lambda x: -x[1])
        ]
        san = board.san(chosen.line.move)
        return Decision(move=chosen.line.move, san=san, color=board.turn,
                        explanation=self.explain(san, chosen), candidates=candidates,
                        reality=self.cost(board, chosen, best))

    def explain(self, san: str, e: CandidateEval) -> str:
        o = e.outcome
        if o.mate < 0.1 and o.win < 0.1:
            return f"Pas d'attaque qui paie tout de suite : joue {san}, le coup le plus prometteur."
        parts = []
        if o.mate >= 0.05:
            parts.append(f"se fait mater dans {o.mate:.0%} des cas")
        if o.win - o.mate >= 0.05:
            parts.append(f"perd au moins {self.big_gain} pions dans {o.win - o.mate:.0%} des cas")
        maia = isinstance(self.model, MaiaModel)
        subject = f"un joueur à {self.opponent_elo} Elo" if maia else "l'adversaire, à courte vue,"
        text = f"Joue {san} pour attaquer : en {self.attack_moves} coups, {subject} {' et '.join(parts)}."
        if o.best is not None and o.best[1]:
            text += f" Scénario le plus probable ({o.best[0]:.0%}) : {' '.join(o.best[1])}."
        return text

    def cost(self, board: chess.Board, chosen: CandidateEval, best: Line) -> str:
        sign = 1 if board.turn == chess.WHITE else -1
        fmt = lambda line: format_score(sign * line.score, None if line.mate is None else sign * line.mate)  # noqa: E731
        if chosen.line.move == best.move:
            return f"C'est aussi le meilleur coup selon Stockfish ({fmt(best)})."
        return (f"Prix de l'attaque : {chosen.loss / 100:.2f} si l'adversaire se défend bien "
                f"({fmt(chosen.line)} contre {fmt(best)} pour {board.san(best.move)}).")
