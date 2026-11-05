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

score = w_mate × mat + w_win × gain + w_eval × éval (pions) − w_loss × perte (pions).

5. **Dialogue avec le style** : le bonus de style (voir `fun_fish.style`) s'ajoute au score,
   mais il se tait quand l'attaque a une idée nettement meilleure. L'occasion d'un coup
   (mat, gain, et ce que l'attaque espère au-delà de l'éval du coup) est comparée à la
   meilleure occasion : à `style_margin` points d'écart, son style ne compte plus qu'au tiers.
   Quand l'attaque n'a pas d'idée tranchée, le style décide ; quand elle en a une, il ne peut
   plus la renverser.

Puis tirage softmax de température `temperature`. On évalue d'abord tous les candidats sur un
seul coup adverse, puis les `deep_candidates` meilleurs sur tout l'horizon.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import chess

from fun_fish.analysis import Analyser, Line
from fun_fish.opponent import MaiaModel, OpponentModel, make_model
from fun_fish.personalities.base import (CONVERT_HELP, Candidate, Decision, Personality, Soundness, converting_note, defending, forcing,
                                         format_score,
                                         pick, regret, softmax)
from fun_fish.repertoire import REPERTOIRES, Planner
from fun_fish.style import STYLE_HELP, STYLE_WEIGHTS, Style, material, settled_material


CLIP = 1000  # évals bornées à ±10 pions




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
    style: float = 0.0

    @property
    def note(self) -> str:
        o = self.outcome
        text = f"mat {o.mate:.0%} · gain {max(0.0, o.win - o.mate):.0%} · éval {o.value / 100:+.1f}"
        return text + (f" · style {self.style:+.1f}" if self.style else "")


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
        "vision_elo": "Vision : n'envisage que les coups qu'un humain de cet Elo jouerait (Maia ; 0 = tous les coups sains)",
        "vision_from": "Vision : ne s'applique qu'à partir de ce demi-coup (avant, en ouverture, voir opening_vision_min)",
        "opening_vision_min": "Vision en ouverture (avant vision_from) : il voit les coups joués au moins aussi souvent (0 = tous)",
        "vision_min": "Vision : probabilité minimale (selon Maia) pour qu'un coup soit vu",
        "vision_breadth": "Vision : ses N coups les plus naturels sont toujours envisagés",
        "vision_panic": "Vision : si ce qu'il voit perd au moins autant (cp) sur le vrai meilleur coup, il voit tout (0 = jamais)",
        "impatience": "Impatience : l'envie de mater et de gagner du matériel grandit avec les coups (0 = aucune)",
        "impatience_half": "Impatience : nombre de coups pour que cette envie passe de 1 à 1 + impatience",
        "opening_temperature": "Hasard du choix dans les premiers coups (0 = comme le reste de la partie)",
        "opening_plies": "Premiers demi-coups de la partie joués avec ce hasard d'ouverture",
        "w_plan": "Plan : bonus pour poursuivre le scénario d'attaque prévu au coup précédent",
        "w_human": "Vision : goût pour les coups naturels, par unité de log-probabilité Maia (les coups forcés en sont exemptés)",
        "convert_elo": "Conversion : Elo de l'adversaire imaginé quand la conversion est complète (0 = inchangé) ; on ne compte plus sur ses gaffes",
        "w_repeat": "Conversion : pénalité pour revenir à une position déjà vue (× degré de conversion)",
        "mate_within": "Un mat forcé en N coups ou moins compte comme un mat (vérifié plus profond si l'éval est écrasante)",
        "big_gain": "Gain de matériel qui compte comme une réussite (pions)",
        "w_mate": "Poids de la probabilité de mater",
        "w_win": "Poids de la probabilité de gros gain matériel",
        "w_eval": "Poids de l'éval attendue au bout (par pion)",
        "w_loss": "Poids de la perte acceptée (par pion)",
        "temperature": "Hasard du choix final (en points de score)",
        "max_candidates": "Coups sains examinés",
        **CONVERT_HELP,
        **STYLE_HELP,
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
                 convert_from: int = 300, convert_full: int = 800, convert_depth: int = 4,
                 convert_tolerance: int = 60, w_technique: float = 1.0,
                 w_develop: float = 0.0, w_open: float = 0.0, w_king: float = 0.0, w_activity: float = 0.0,
                 w_initiative: float = 0.0, w_sacrifice: float = 0.0, w_surprise: float = 0.0,
                 w_center: float = 0.0, w_threat: float = 0.0, mood: float = 0.0,
                 w_repertoire: float = 0.0, repertoire_rate: float = 0.0,
                 style_margin: float = 1.0, style_first: int = 0, vision_elo: int = 0, vision_min: float = 0.02, vision_from: int = 0,
                 opening_vision_min: float = 0.0,
                 vision_breadth: int = 6, vision_panic: int = 150, w_human: float = 0.0, defend_from: int = 100, defend_full: int = 400, w_counterplay: float = 0.0,
                 impatience: float = 0.0, impatience_half: int = 20, w_plan: float = 0.0,
                 opening_temperature: float = 0.0, opening_plies: int = 6,
                 convert_elo: int = 0, w_repeat: float = 0.0,
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
        self.opening_temperature, self.opening_plies = opening_temperature, opening_plies
        self.max_candidates = max_candidates
        self.deep_candidates = deep_candidates
        self.instinct, self.caution, self.naivety = instinct, caution, naivety
        self.convert_from, self.convert_full = convert_from, convert_full
        self.convert_depth, self.convert_tolerance = convert_depth, convert_tolerance
        self.w_technique = w_technique
        self.w_develop, self.w_open, self.w_king, self.w_activity = w_develop, w_open, w_king, w_activity
        self.w_initiative, self.w_sacrifice, self.w_surprise = w_initiative, w_sacrifice, w_surprise
        self.w_center, self.w_threat, self.mood = w_center, w_threat, mood
        self.style_margin = style_margin
        self.vision_elo, self.vision_min, self.vision_from = vision_elo, vision_min, vision_from
        self.opening_vision_min = opening_vision_min
        self.w_human = w_human
        self.impatience, self.impatience_half, self.w_plan = impatience, impatience_half, w_plan
        self._move = 1
        self._plan: tuple[str, chess.Move] | None = None
        self.plan_stats = {"planned": 0, "kept": 0}
        self.vision_breadth, self.vision_panic = vision_breadth, vision_panic
        self.defend_from, self.defend_full, self.w_counterplay = defend_from, defend_full, w_counterplay
        self.convert_elo, self.w_repeat = convert_elo, w_repeat
        self._models: dict[int, OpponentModel] = {}
        self._elo_now = opponent_elo
        self._mate_now = mate_within
        self._vision: MaiaModel | None = None
        self.w_repertoire, self.repertoire_rate = w_repertoire, repertoire_rate
        self._planner: Planner | None = None
        self.style_first = style_first
        self._mood: dict[str, float] = {}

    @property
    def model(self) -> OpponentModel:
        """L'adversaire imaginé, à l'Elo du moment (`_elo_now`, qui monte pendant la conversion)."""
        if self._elo_now not in self._models:
            self._models[self._elo_now] = make_model(self._elo_now, instinct=self.instinct, caution=self.caution,
                                                     naivety=self.naivety)
        return self._models[self._elo_now]

    def imagined_elo(self, degree: float) -> int:
        """Plus la conversion avance, moins il compte sur les gaffes : l'adversaire imaginé monte
        de `opponent_elo` vers `convert_elo` (par paliers de 100, ceux de Maia)."""
        if self.opponent_elo <= 0 or self.convert_elo <= self.opponent_elo:
            return self.opponent_elo
        return round((self.opponent_elo + degree * (self.convert_elo - self.opponent_elo)) / 100) * 100

    def soundness(self) -> Soundness:
        return Soundness(self.tolerance, self.soundness_depth, self.max_candidates,
                         self.convert_from, self.convert_full, self.convert_depth, self.convert_tolerance,
                         self.vision_min, self.vision_breadth, self.vision_panic, self.defend_from,
                         self.defend_full)

    def seen(self, board: chess.Board, analyser: Analyser) -> dict[chess.Move, float] | None:
        """Ce qu'un humain de `vision_elo` jouerait ici (None : il voit tout)."""
        if self.vision_elo <= 0 or (board.ply() < self.vision_from and self.opening_vision_min <= 0):
            return None
        if self._vision is None:
            from fun_fish.sim import maia

            if not maia.available():
                return None
            self._vision = MaiaModel(self.vision_elo)
        return self._vision.probabilities(board, analyser)

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
        deep = 2 * self._mate_now - 1  # demi-coups d'un mat en _mate_now
        if line.mate is None and line.score >= 500 and deep > self.attack_depth:
            line = analyser.analyse(position, multipv=1, depth=deep)[0]  # écrasant : un mat plus loin ?
        if line.mate is not None and 0 < line.mate <= self._mate_now:
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
        loss = regret(best, line)
        position = board.copy()
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
        urge = self.urge()
        e.score = (urge * (self.w_mate * o.mate + self.w_win * o.win) + self.w_eval * (o.value - reference) / 100
                   - self.w_loss * e.loss / 100)
        return e

    # ---------- choix ----------

    def opportunity(self, e: CandidateEval) -> float:
        """Ce que l'attaque espère de ce coup au-delà de son éval : mat, gain, éval attendue en plus."""
        o = e.outcome
        hope = max(0.0, max(-CLIP, min(CLIP, o.value)) - max(-CLIP, min(CLIP, e.line.score))) / 100
        return self.urge() * (self.w_mate * o.mate + self.w_win * o.win) + self.w_eval * hope

    def urge(self) -> float:
        """Impatience : 1 au début, puis exponentielle (plate au départ) avec les coups joués."""
        if self.impatience <= 0:
            return 1.0
        return 1.0 + self.impatience * (2 ** (self._move / max(1, self.impatience_half)) - 1)

    @staticmethod
    def plan_key(board: chess.Board) -> str:
        return " ".join(board.fen().split()[:4])

    def remember_plan(self, board: chess.Board, chosen: CandidateEval) -> None:
        """Le scénario d'attaque du coup choisi : après sa réponse prévue, notre coup suivant."""
        self._plan = None
        best = chosen.outcome.best
        if best is None or len(best[1]) < 3:
            return
        position = board.copy(stack=False)
        try:
            position.push_san(best[1][0])
            position.push_san(best[1][1])
            self._plan = (self.plan_key(position), position.parse_san(best[1][2]))
        except ValueError:
            self._plan = None

    def combine(self, evals: list[CandidateEval], bonus: dict[chess.Move, float]) -> list[float]:
        """Score final : attaque + style, le style étant tempéré quand l'attaque voit mieux ailleurs."""
        chances = [self.opportunity(e) for e in evals]
        top = max(chances, default=0.0)
        scores = []
        for e, chance in zip(evals, chances):
            voice = math.exp(-(top - chance) / self.style_margin) if self.style_margin > 0 else 1.0
            e.style = voice * bonus.get(e.line.move, 0.0)
            scores.append(e.score + e.style)
        return scores

    def style(self, board: chess.Board) -> Style:
        """Ses goûts, teintés par l'humeur de la partie (tirée au début de chaque partie)."""
        if self.mood <= 0:
            return Style.of(self)
        if board.ply() < 2 or not self._mood:
            # l'humeur redistribue ses goûts sans les éteindre : leur poids total ne change pas
            draws = {name: math.exp(self.rng.gauss(0.0, self.mood)) for name in STYLE_WEIGHTS}
            active = [n for n in STYLE_WEIGHTS if getattr(self, n, 0)]
            mean = sum(draws[n] for n in active) / len(active) if active else 1.0
            self._mood = {n: f / mean for n, f in draws.items()}
        return Style.of(self).moody(self._mood)

    def planned(self, board: chess.Board) -> tuple[chess.Move, str] | None:
        """Le coup de la ligne connue qu'il a envie de jouer dans cette partie, s'il en a envie."""
        # le répertoire se transmet : le corsaire, le flibustier et Fun Fish jouent celui du romantique
        openings = next((REPERTOIRES[c.key] for c in type(self).__mro__ if getattr(c, "key", None) in REPERTOIRES),
                        None)
        if not (self.w_repertoire and self.repertoire_rate > 0 and openings):
            return None
        if self._planner is None:
            self._planner = Planner(openings, self.rng, self.repertoire_rate)
        return self._planner.next(board)

    def more_candidates(self, board: chess.Board, analyser: Analyser, sound: list[Line],
                        seen: dict[chess.Move, float] | None) -> list[Line]:
        """Point d'accroche : d'autres coups à examiner (aucun ici)."""
        return sound

    def more_bonus(self, board: chess.Board, sound: list[Line], best: Line, bonus: dict[chess.Move, float],
                   style: Style) -> None:
        """Point d'accroche : d'autres goûts à ajouter aux bonus (aucun ici)."""

    def naturalness(self, board: chess.Board, moves: list[chess.Move],
                    seen: dict[chess.Move, float]) -> dict[chess.Move, float]:
        """Log-probabilité Maia de chaque coup par rapport au plus naturel (0 au mieux, négatif sinon).

        Les coups forcés (échecs, prises, promotions) ne sont pas pénalisés : un attaquant les
        regarde toujours, même quand ils sont improbables. Ce qui reste peu naturel, ce sont les
        coups calmes et subtils d'une machine.
        """
        top = max(seen.values(), default=1.0)
        floor = max(self.vision_min, 1e-3)
        return {m: 0.0 if forcing(board, m) else math.log(max(seen.get(m, 0.0), floor) / top) for m in moves}

    def choose_move(self, board: chess.Board, analyser: Analyser) -> Decision:
        self._move = board.fullmove_number
        seen = self.seen(board, analyser)
        soundness = self.soundness()
        opening = board.ply() < self.vision_from
        if opening and seen:  # vision large en ouverture : tout coup un peu joué, sans plafond de nombre
            soundness.vision_min, soundness.vision_breadth = self.opening_vision_min, 0
        sound, degree = soundness.lines(board, analyser, seen, self.restrict)
        sound = self.more_candidates(board, analyser, sound, seen)
        best = sound[0]
        self._elo_now = self.imagined_elo(degree)
        self._mate_now = self.mate_within + round(degree)  # gagnant : il cherche le mat un coup plus loin
        style = self.style(board)
        trouble = defending(best.score, self.defend_from, self.defend_full) if self.w_counterplay else 0.0
        bonus = style.bonuses(board, sound, best, conversion=degree * self.w_technique, planned=self.planned(board),
                              counterplay=trouble * self.w_counterplay)
        if seen and self.w_human and not opening:
            for move, value in self.naturalness(board, [l.move for l in sound], seen).items():
                bonus[move] = bonus.get(move, 0.0) + self.w_human * value
        self.more_bonus(board, sound, best, bonus, style)
        planned = None
        if self._plan is not None and self._plan[0] == self.plan_key(board):
            planned = self._plan[1]
            if any(line.move == planned for line in sound):
                self.plan_stats["planned"] += 1
                if self.w_plan:
                    bonus[planned] = bonus.get(planned, 0.0) + self.w_plan
            else:
                planned = None
        if self.style_first > 0 and bonus:
            sound = sorted(sound, key=lambda l: -bonus[l.move])[: self.style_first]
        children = []
        for line in sound:
            child = board.copy(stack=False)
            child.push(line.move)
            children.append(child)
        self.model.prefetch(children)

        evals = [self.scored(self.evaluate(board, line, best, analyser, moves=1), best) for line in sound]
        if self.attack_moves > 1:
            first = self.combine(evals, bonus)
            evals = [e for _, e in sorted(zip(first, evals), key=lambda x: -x[0])]
            evals[: self.deep_candidates] = [
                self.scored(self.evaluate(board, e.line, best, analyser, moves=self.attack_moves), best)
                for e in evals[: self.deep_candidates]
            ]

        scores = self.combine(evals, bonus)
        if self.w_repeat and degree > 0:  # en conversion, ne pas tourner en rond
            for i, e in enumerate(evals):
                child = board.copy()
                child.push(e.line.move)
                if child.is_repetition(2):
                    scores[i] -= self.w_repeat * degree
        hot = self.opening_temperature > 0 and board.ply() < self.opening_plies and board.root() == chess.Board()
        probabilities = softmax(scores, self.opening_temperature if hot else self.temperature)
        chosen = evals[pick(probabilities, self.rng)]
        kept = planned is not None and chosen.line.move == planned
        self.plan_stats["kept"] += kept
        self.remember_plan(board, chosen)
        candidates = [
            Candidate(move=e.line.move, san=board.san(e.line.move), score=e.line.score, mate=e.line.mate,
                      probability=p, note=e.note)
            for e, p in sorted(zip(evals, probabilities), key=lambda x: -x[1])
        ]
        san = board.san(chosen.line.move)
        return Decision(move=chosen.line.move, san=san, color=board.turn,
                        explanation=converting_note(degree) + ("Il poursuit son plan. " if kept else "")
                        + ("En difficulté, il cherche du contre-jeu. " if trouble >= 0.5 else "") + self.explain(san, chosen,
                                                                                      style.reasons.get(chosen.line.move)),
                        candidates=candidates,
                        reality=self.cost(board, chosen, best, soundness.truth))

    def explain(self, san: str, e: CandidateEval, taste: str | None = None) -> str:
        o = e.outcome
        if o.mate < 0.1 and o.win < 0.1:
            if taste:
                return f"Pas d'attaque qui paie tout de suite : joue {san}, un coup à son goût ({taste})."
            return f"Pas d'attaque qui paie tout de suite : joue {san}, le coup le plus prometteur."
        parts = []
        if o.mate >= 0.05:
            parts.append(f"se fait mater dans {o.mate:.0%} des cas")
        if o.win - o.mate >= 0.05:
            parts.append(f"perd au moins {self.big_gain} pions dans {o.win - o.mate:.0%} des cas")
        maia = isinstance(self.model, MaiaModel)
        subject = f"un joueur à {self._elo_now} Elo" if maia else "l'adversaire, à courte vue,"
        text = f"Joue {san} pour attaquer : en {self.attack_moves} coups, {subject} {' et '.join(parts)}."
        if o.best is not None and o.best[1]:
            text += f" Scénario le plus probable ({o.best[0]:.0%}) : {' '.join(o.best[1])}."
        return text

    def cost(self, board: chess.Board, chosen: CandidateEval, best: Line, truth: Line | None = None) -> str:
        sign = 1 if board.turn == chess.WHITE else -1
        fmt = lambda line: format_score(sign * line.score, None if line.mate is None else sign * line.mate)  # noqa: E731
        if truth is not None and truth.move != best.move and truth.score - best.score >= 50:
            return (f"Il ne voit pas {board.san(truth.move)} ({fmt(truth)}), trop peu naturel à "
                    f"{self.vision_elo} Elo ; parmi ce qu'il voit, le meilleur est {board.san(best.move)} "
                    f"({fmt(best)}), il joue {board.san(chosen.line.move)} ({fmt(chosen.line)}).")
        if chosen.line.move == best.move:
            return f"C'est aussi le meilleur coup selon Stockfish ({fmt(best)})."
        return (f"Prix de l'attaque : {chosen.loss / 100:.2f} si l'adversaire se défend bien "
                f"({fmt(chosen.line)} contre {fmt(best)} pour {board.san(best.move)}).")
