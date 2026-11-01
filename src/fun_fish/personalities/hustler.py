"""L'arnaqueur : préfère les coups qui tendent des pièges et laissent à l'adversaire un chemin étroit.

1. **Santé** : on ne garde que nos coups qui perdent au plus `tolerance` centipions par
   rapport au meilleur, si l'adversaire répond juste.
2. **Adversaire naïf** : sa probabilité de jouer une réponse est un softmax (température
   `naivety`) d'un score qui mélange son instinct (`fun_fish.naive` : prises, échecs,
   fourchettes, pièces à sauver) et l'éval de Stockfish à faible profondeur (1 à
   `shallow_depth`). `instinct` règle le mélange : 1 = pur instinct, 0 = pur Stockfish.
   Stockfish seul est un mauvais naïf : même à profondeur 1, il voit les captures en
   chaîne et ne tombe dans presque aucun piège.
   Si `opponent_elo` > 0, c'est Maia-2 qui joue ce rôle : la tentation d'un coup est la
   probabilité qu'un humain de cet Elo le joue (les réglages de l'instinct sont ignorés).
   On n'analyse alors que ses `reply_lines` meilleures réponses et celles que Maia juge
   plausibles (jusqu'à `plausible_mass` de probabilité cumulée, `max_plausible` au plus),
   au lieu de toutes : c'est 3 à 5 fois plus rapide.
3. **Simulation** : on déroule ses réponses tentantes (au plus `branching`, de tentation
   ≥ `min_temptation`) sur `sharp_plies` coups. À nos coups, on choisit parmi nos
   `our_options` meilleures réponses (à moins de `safe_margin` de la meilleure) celle qui
   piège le mieux. Un piège peut ainsi se refermer en plusieurs temps : dans Blackburne,
   Cxe5 n'est pas une erreur ; c'est Dg5 (aussi bon que d5) qui tend Cxf7, et Cxf7 qui
   coûte la partie.

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
from fun_fish.opponent import OpponentModel, make_model
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
        "opponent_elo": "Elo de l'adversaire imaginé, modélisé par Maia-2 (0 = adversaire naïf à l'instinct)",
        "reply_lines": "Avec Maia : meilleures réponses adverses toujours analysées",
        "plausible_mass": "Avec Maia : probabilité cumulée des réponses plausibles analysées (0 à 1)",
        "max_plausible": "Avec Maia : nombre maximal de réponses plausibles analysées",
        "tolerance": "Perte maximale acceptée si l'adversaire répond juste (centipions)",
        "soundness_depth": "Profondeur du filtre de santé",
        "reply_depth": "Profondeur d'analyse des réponses adverses",
        "instinct": "Part de l'instinct (prises, échecs, fourchettes) face à Stockfish dans l'adversaire naïf (0 à 1)",
        "caution": "Attention de l'adversaire naïf aux reprises et pièces en prise (0 = aveugle, 1 = lucide)",
        "shallow_depth": "Part Stockfish de l'adversaire naïf : voit de 1 à N demi-coups (0 = instinct seul)",
        "naivety": "Hasard de l'adversaire naïf (cp) : plus c'est haut, plus il joue au hasard",
        "safe_margin": "En deçà (cp), une réponse adverse est sûre",
        "error_margin": "Au-delà (cp), c'est une vraie erreur",
        "sharp_plies": "Coups adverses simulés",
        "branching": "Réponses tentantes explorées à chaque coup adverse",
        "our_options": "Nos réponses quasi optimales envisagées pour prolonger le piège (1 = toujours la meilleure)",
        "min_temptation": "Tentation minimale pour explorer une réponse (0 à 1)",
        "w_trap": "Poids du piège (gain attendu de la naïveté adverse)",
        "w_sharp": "Poids de l'étroitesse (probabilité d'erreur adverse)",
        "w_loss": "Poids de la perte acceptée",
        "temperature": "Hasard du choix final (en pions de score)",
        "max_candidates": "Coups sains examinés",
        "deep_candidates": "Dont simulés sur tous les coups",
    }

    def __init__(self, opponent_elo: int = 0, reply_lines: int = 5, plausible_mass: float = 0.9,
                 max_plausible: int = 8, tolerance: int = 100, soundness_depth: int = 12, reply_depth: int = 8,
                 instinct: float = 0.7, caution: float = 0.5, shallow_depth: int = 1, naivety: float = 60.0,
                 safe_margin: int = 60, error_margin: int = 150,
                 sharp_plies: int = 2, branching: int = 3, min_temptation: float = 0.1, our_options: int = 2,
                 w_trap: float = 1.0, w_sharp: float = 1.0, w_loss: float = 1.0, temperature: float = 0.3,
                 max_candidates: int = 15, deep_candidates: int = 6, **kwargs):
        super().__init__(**kwargs)
        self.opponent_elo = opponent_elo  # > 0 : l'adversaire est modélisé par Maia-2 à cet Elo
        self.reply_lines = reply_lines  # avec Maia : meilleures réponses toujours analysées
        self.plausible_mass = plausible_mass  # avec Maia : masse de probabilité des réponses analysées
        self.max_plausible = max_plausible
        self.tolerance = tolerance  # cp qu'il accepte de perdre si l'adversaire répond juste
        self.soundness_depth = soundness_depth  # profondeur du filtre de santé
        self.reply_depth = reply_depth  # profondeur d'analyse des réponses adverses
        self.instinct = instinct  # part de l'instinct dans l'adversaire naïf
        self.caution = caution  # attention de l'adversaire naïf aux reprises
        self.shallow_depth = shallow_depth  # part Stockfish de l'adversaire naïf : 1 à N demi-coups
        self.naivety = naivety  # cp : plus c'est haut, plus l'adversaire naïf joue au hasard
        self.safe_margin = safe_margin  # cp : en deçà, une réponse adverse est sûre
        self.error_margin = error_margin  # cp : au-delà, c'est une vraie erreur
        self.sharp_plies = sharp_plies  # coups adverses simulés
        self.branching = branching  # réponses tentantes explorées à chaque coup adverse
        self.min_temptation = min_temptation  # en deçà, une réponse n'est pas explorée
        self.our_options = our_options  # nos réponses envisagées pour prolonger le piège
        self.w_trap, self.w_sharp, self.w_loss = w_trap, w_sharp, w_loss
        self.temperature = temperature  # en pions de score
        self.max_candidates = max_candidates  # coups sains examinés
        self.deep_candidates = deep_candidates  # dont simulés sur sharp_plies coups
        self._model: OpponentModel | None = None

    # ---------- adversaire naïf ----------

    @property
    def model(self) -> OpponentModel:
        if self._model is None:
            self._model = make_model(self.opponent_elo, instinct=self.instinct, caution=self.caution,
                                     shallow_depth=self.shallow_depth, naivety=self.naivety)
        return self._model

    def temptation(self, position: chess.Board, analyser: Analyser) -> dict[chess.Move, float]:
        """Probabilité que l'adversaire naïf (qui a le trait) joue chaque coup."""
        return self.model.probabilities(position, analyser)

    def simulate(self, position: chess.Board, analyser: Analyser, plies: int) -> Node:
        """Déroule les réponses tentantes de l'adversaire (qui a le trait) sur `plies` coups."""
        tempt = self.temptation(position, analyser)
        deep = self.replies(position, analyser, tempt)
        best = deep[0]
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
            if plies > 1:
                after = position.copy()
                after.push(line.move)
                if not after.is_game_over():
                    sub = self.follow_up(after, analyser, plies - 1)
                    if sub is not None:
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

    def replies(self, position: chess.Board, analyser: Analyser, tempt: dict[chess.Move, float]) -> list[Line]:
        """Les réponses adverses à analyser : toutes, ou avec Maia les meilleures et les plausibles."""
        legal = position.legal_moves.count()
        if self.opponent_elo <= 0 or legal <= self.reply_lines + self.max_plausible:
            return analyser.analyse(position, multipv=legal, depth=self.reply_depth)
        top = analyser.analyse(position, multipv=self.reply_lines, depth=self.reply_depth)
        seen = {line.move for line in top}
        plausible, mass = [], 0.0
        for move, p in sorted(tempt.items(), key=lambda x: -x[1]):
            if mass >= self.plausible_mass or len(plausible) >= self.max_plausible:
                break
            mass += p
            if move not in seen:
                plausible.append(move)
        if not plausible:
            return top
        extra = analyser.analyse(position, multipv=len(plausible), depth=self.reply_depth, moves=plausible)
        return sorted([*top, *extra], key=lambda line: -line.score)

    def follow_up(self, position: chess.Board, analyser: Analyser, plies: int) -> Node | None:
        """À nous de jouer : parmi nos réponses quasi optimales, celle qui piège le mieux."""
        ours = analyser.analyse(position, multipv=max(1, self.our_options), depth=self.reply_depth)
        best_node = None
        for line in ours:
            if ours[0].score - line.score > self.safe_margin:
                break
            child = position.copy()
            child.push(line.move)
            if child.is_game_over():
                value = clip(line.score)
                node = Node(value=value, best_value=value, p_error=0.0)
            else:
                node = self.simulate(child, analyser, plies)
            if best_node is None or node.value > best_node.value:
                best_node = node
        return best_node

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
        root = analyser.analyse(board, multipv=min(board.legal_moves.count(), self.max_candidates),
                                depth=self.soundness_depth)
        best = root[0]
        sound = [l for l in root if best.score - l.score <= self.tolerance][: self.max_candidates]

        # Premier passage : un seul coup adverse simulé, pour tous les candidats.
        self.model.prefetch([_after(board, line.move) for line in sound])
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
                f"({', '.join(node.safe) or 'aucune'}). {self.opponent_label()} commet une vraie erreur "
                f"dans {node.p_error:.0%} des cas.")
        if node.bait:
            move, temptation, value = node.bait
            sign = 1 if board.turn == chess.WHITE else -1
            text += (f" Piège principal : {move} (tentant à {temptation:.0%}) ; s'il continue naïvement, "
                     f"la partie finit vers {format_score(int(sign * value), None)}.")
        return text

    def opponent_label(self) -> str:
        return self.model.label()

    def cost(self, board: chess.Board, chosen: CandidateEval, best: Line) -> str:
        sign = 1 if board.turn == chess.WHITE else -1
        fmt = lambda line: format_score(sign * line.score, None if line.mate is None else sign * line.mate)  # noqa: E731
        if chosen.line.move == best.move:
            return f"C'est aussi le meilleur coup selon Stockfish ({fmt(best)})."
        return (f"Prix du bluff : {chosen.loss / 100:.2f} si l'adversaire répond juste "
                f"({fmt(chosen.line)} contre {fmt(best)} pour {board.san(best.move)}).")


def _after(board: chess.Board, move: chess.Move) -> chess.Board:
    child = board.copy(stack=False)
    child.push(move)
    return child
