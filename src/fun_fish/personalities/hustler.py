"""L'arnaqueur : préfère les coups qui tendent des pièges et laissent à l'adversaire un chemin étroit.

1. **Santé** : on ne garde que nos coups qui perdent au plus `tolerance` centipions par
   rapport au meilleur, si l'adversaire répond juste.
2. **Adversaire naïf** : sa probabilité de jouer une réponse est un softmax (température
   `naivety`) de l'analyse à faible profondeur, moyenné sur les profondeurs 1 à
   `shallow_depth`. Chaque piège vise un niveau de myopie différent. Une réponse est
   tentante quand elle paraît bonne à courte vue.
3. **Simulation** : on déroule ses réponses tentantes (au plus `branching`, de tentation
   ≥ `min_temptation`) sur `sharp_plies` coups, en répondant à chaque fois par notre
   meilleur coup. Un piège peut ainsi se refermer en plusieurs temps : dans Blackburne,
   Cxe5 n'est qu'une erreur moyenne, c'est Cxf7 au coup suivant qui coûte la partie.

On en tire deux mesures :

- **piège** = ce que rapporte la naïveté de l'adversaire par rapport à un jeu correct (en pions) ;
- **étroitesse** = probabilité qu'il commette une vraie erreur (≥ `error_margin`) en route.
  Les réponses à moins de `safe_margin` de la meilleure sont dites sûres.

score = w_trap × piège + w_sharp × étroitesse − w_loss × perte (en pions), puis tirage
softmax de température `temperature`. Pour limiter le coût, on simule d'abord un seul
coup adverse pour tous les candidats, puis `sharp_plies` coups pour les `deep_candidates`
meilleurs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import chess

from fun_fish.analysis import Analyser, Line
from fun_fish.personalities.base import Candidate, Decision, Personality, format_score, pick, softmax
from fun_fish.reading import VISIBLE_MATE

CLIP = 1000  # on borne les évals à ±10 pions : au-delà, peu importe
MATE_VALUE = CLIP


def clip(score: int) -> int:
    return max(-CLIP, min(CLIP, score))


def our_value(line: Line) -> int:
    """Valeur d'une réponse adverse, de notre point de vue (la ligne est du point de vue adverse)."""
    if line.mate is not None and -VISIBLE_MATE <= line.mate < 0:
        return MATE_VALUE
    return clip(-line.score)


@dataclass
class Node:
    """Résultat de la simulation de l'adversaire naïf à partir d'une position où il a le trait."""

    value: float  # éval attendue face à lui, notre point de vue, en cp
    best_value: int  # éval s'il joue juste
    p_error: float  # probabilité qu'il commette une vraie erreur en route
    safe: list[str] = field(default_factory=list)  # ses réponses sûres (premier coup)
    bait: tuple[str, float, float] | None = None  # (coup, tentation, valeur pour nous au bout) : piège principal

    @property
    def hustle(self) -> float:
        """Ce que rapporte sa naïveté, en pions."""
        return (self.value - self.best_value) / 100


@dataclass
class CandidateEval:
    line: Line
    loss: int  # cp perdus par rapport au meilleur coup si l'adversaire répond juste
    node: Node
    score: float = 0.0

    @property
    def note(self) -> str:
        n = len(self.node.safe)
        return f"{n} sûr{'s' if n > 1 else ''} · erreur {self.node.p_error:.0%} · piège {self.node.hustle:+.1f}"


class Hustler(Personality):
    key = "hustler"
    name = "L'arnaqueur"
    description = ("Cherche les coups où les réponses naturelles perdent et où un seul chemin étroit tient. "
                   "Accepte de lâcher un peu si on le contre bien.")
    PARAM_HELP = {
        "tolerance": "Perte maximale acceptée si l'adversaire répond juste (centipions)",
        "soundness_depth": "Profondeur du filtre de santé",
        "reply_depth": "Profondeur d'analyse des réponses adverses",
        "shallow_depth": "L'adversaire naïf voit de 1 à N demi-coups",
        "naivety": "Hasard de l'adversaire naïf (cp) : plus c'est haut, plus il joue au hasard",
        "safe_margin": "En deçà (cp), une réponse adverse est sûre",
        "error_margin": "Au-delà (cp), c'est une vraie erreur",
        "sharp_plies": "Coups adverses simulés",
        "branching": "Réponses tentantes explorées à chaque coup adverse",
        "min_temptation": "Tentation minimale pour explorer une réponse (0 à 1)",
        "w_trap": "Poids du piège (gain attendu de la naïveté adverse)",
        "w_sharp": "Poids de l'étroitesse (probabilité d'erreur adverse)",
        "w_loss": "Poids de la perte acceptée",
        "temperature": "Hasard du choix final (en pions de score)",
        "max_candidates": "Coups sains examinés",
        "deep_candidates": "Dont simulés sur tous les coups",
    }

    def __init__(self, tolerance: int = 100, soundness_depth: int = 12, reply_depth: int = 8,
                 shallow_depth: int = 2, naivety: float = 20.0, safe_margin: int = 60, error_margin: int = 150,
                 sharp_plies: int = 2, branching: int = 3, min_temptation: float = 0.1,
                 w_trap: float = 1.0, w_sharp: float = 1.0, w_loss: float = 1.0, temperature: float = 0.3,
                 max_candidates: int = 15, deep_candidates: int = 6, **kwargs):
        super().__init__(**kwargs)
        self.tolerance = tolerance  # cp qu'il accepte de perdre si l'adversaire répond juste
        self.soundness_depth = soundness_depth  # profondeur du filtre de santé
        self.reply_depth = reply_depth  # profondeur d'analyse des réponses adverses
        self.shallow_depth = shallow_depth  # l'adversaire naïf voit de 1 à N demi-coups
        self.naivety = naivety  # cp : plus c'est haut, plus l'adversaire naïf joue au hasard
        self.safe_margin = safe_margin  # cp : en deçà, une réponse adverse est sûre
        self.error_margin = error_margin  # cp : au-delà, c'est une vraie erreur
        self.sharp_plies = sharp_plies  # coups adverses simulés
        self.branching = branching  # réponses tentantes explorées à chaque coup adverse
        self.min_temptation = min_temptation  # en deçà, une réponse n'est pas explorée
        self.w_trap, self.w_sharp, self.w_loss = w_trap, w_sharp, w_loss
        self.temperature = temperature  # en pions de score
        self.max_candidates = max_candidates  # coups sains examinés
        self.deep_candidates = deep_candidates  # dont simulés sur sharp_plies coups

    # ---------- adversaire naïf ----------

    def temptation(self, position: chess.Board, analyser: Analyser) -> dict[chess.Move, float]:
        n = position.legal_moves.count()
        result: dict[chess.Move, float] = {}
        for depth in range(1, self.shallow_depth + 1):
            shallow = analyser.analyse(position, multipv=n, depth=depth)
            for line, p in zip(shallow, softmax([l.score for l in shallow], self.naivety)):
                result[line.move] = result.get(line.move, 0.0) + p / self.shallow_depth
        return result

    def simulate(self, position: chess.Board, analyser: Analyser, plies: int) -> Node:
        """Déroule les réponses tentantes de l'adversaire (qui a le trait) sur `plies` coups."""
        deep = analyser.analyse(position, multipv=position.legal_moves.count(), depth=self.reply_depth)
        best = deep[0]
        tempt = self.temptation(position, analyser)
        node = Node(value=0.0, best_value=our_value(best), p_error=0.0,
                    safe=[position.san(l.move) for l in deep if best.score - l.score <= self.safe_margin])

        branches = sorted(deep, key=lambda l: -tempt.get(l.move, 0.0))[: self.branching]
        branches = [l for l in branches if tempt.get(l.move, 0.0) >= self.min_temptation]
        explored = 0.0
        bait_gain = 0.0
        for line in branches:
            t = tempt[line.move]
            explored += t
            error = best.score - line.score >= self.error_margin
            value, p_error = our_value(line), 1.0 if error else 0.0
            if plies > 1 and len(line.pv) >= 2:
                child = position.copy()
                child.push(line.move)
                if child.is_legal(line.pv[1]):
                    child.push(line.pv[1])  # notre meilleure réponse
                    if not child.is_game_over():
                        sub = self.simulate(child, analyser, plies - 1)
                        value = sub.value
                        p_error = 1.0 if error else sub.p_error
            node.value += t * value
            node.p_error += t * p_error
            gain = t * (value - node.best_value)
            if gain > bait_gain:
                bait_gain = gain
                node.bait = (position.san(line.move), t, value)
        node.value += (1.0 - explored) * node.best_value  # le reste du temps, il joue juste
        return node

    # ---------- choix ----------

    def evaluate(self, board: chess.Board, line: Line, best: Line, analyser: Analyser, plies: int) -> CandidateEval:
        loss = max(0, best.score - line.score)
        position = board.copy()
        position.push(line.move)
        if position.is_checkmate():
            return CandidateEval(line, loss, Node(value=MATE_VALUE, best_value=MATE_VALUE, p_error=1.0))
        if position.is_game_over():
            return CandidateEval(line, loss, Node(value=0.0, best_value=0, p_error=0.0))
        return CandidateEval(line, loss, self.simulate(position, analyser, plies))

    def scored(self, e: CandidateEval) -> CandidateEval:
        e.score = self.w_trap * e.node.hustle + self.w_sharp * e.node.p_error - self.w_loss * e.loss / 100
        return e

    def choose_move(self, board: chess.Board, analyser: Analyser) -> Decision:
        root = analyser.analyse(board, multipv=board.legal_moves.count(), depth=self.soundness_depth)
        best = root[0]
        sound = [l for l in root if best.score - l.score <= self.tolerance][: self.max_candidates]

        # Premier passage : un seul coup adverse simulé, pour tous les candidats.
        evals = [self.scored(self.evaluate(board, line, best, analyser, plies=1)) for line in sound]
        # Second passage : simulation complète pour les plus prometteurs.
        if self.sharp_plies > 1:
            evals.sort(key=lambda e: -e.score)
            evals[: self.deep_candidates] = [
                self.scored(self.evaluate(board, e.line, best, analyser, plies=self.sharp_plies))
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
                        explanation=self.explain(board, san, chosen),
                        candidates=candidates, reality=self.cost(board, chosen, best))

    def explain(self, board: chess.Board, san: str, e: CandidateEval) -> str:
        node = e.node
        if node.hustle < 0.3 and node.p_error < 0.3:
            if e.loss > 0:
                return f"Pas de piège en vue : joue {san}, un coup sain tiré au sort."
            return f"Rien à arnaquer ici : joue {san}."
        n = len(node.safe)
        text = (f"Joue {san} : l'adversaire n'a que {n} réponse{'s' if n > 1 else ''} sûre{'s' if n > 1 else ''} "
                f"({', '.join(node.safe) or 'aucune'}). À courte vue, il commet une vraie erreur "
                f"dans {node.p_error:.0%} des cas.")
        if node.bait:
            move, temptation, value = node.bait
            sign = 1 if board.turn == chess.WHITE else -1
            text += (f" Piège principal : {move} (tentant à {temptation:.0%}) ; s'il continue naïvement, "
                     f"la partie finit vers {format_score(int(sign * value), None)}.")
        return text

    def cost(self, board: chess.Board, chosen: CandidateEval, best: Line) -> str:
        sign = 1 if board.turn == chess.WHITE else -1
        fmt = lambda line: format_score(sign * line.score, None if line.mate is None else sign * line.mate)  # noqa: E731
        if chosen.line.move == best.move:
            return f"C'est aussi le meilleur coup selon Stockfish ({fmt(best)})."
        return (f"Prix du bluff : {chosen.loss / 100:.2f} si l'adversaire répond juste "
                f"({fmt(chosen.line)} contre {fmt(best)} pour {board.san(best.move)}).")
