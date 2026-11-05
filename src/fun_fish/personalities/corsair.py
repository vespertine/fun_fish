"""Le corsaire : le romantique et l'arnaqueur fondus en un seul regard.

Le même arbre que le cogneur (nos coups sains, les réponses probables de l'adversaire selon
son modèle, sur `attack_moves` coups), avec deux ajouts venus de l'arnaqueur :

- **étroitesse** : chaque branche note si l'adversaire y commet une vraie erreur (au moins
  `error_margin` cp de moins que sa meilleure réponse) ; c'est presque gratuit, ses réponses
  sont déjà évaluées par l'arbre ;
- **coups rusés** : à notre premier coup dans l'arbre, parmi nos `our_options` réponses à moins
  de `safe_margin` de la meilleure, il prend celle qui mène au meilleur motif ; un piège peut
  ainsi se refermer en deux temps.

Quatre motifs par coup : mat (`w_mate`), gain de matériel (`w_win`), piège (`w_trap` × ce que
la naïveté adverse rapporte au-delà d'un jeu correct, en pions, borné à `trap_cap`) et
étroitesse (`w_sharp` × probabilité d'erreur). Pour ne pas devenir tiède, **le motif le plus fort
mène** : le score vaut le meilleur motif plus `lead_share` des autres, moins la perte. Un coup
qui est une vraie attaque ou un vrai piège passe devant un coup moyen sur les deux tableaux.
Le style dialogue avec ce score comme avec celui du cogneur.
"""

from __future__ import annotations

import inspect

import chess

from fun_fish.analysis import Analyser, Line
from fun_fish.personalities.attacker import CLIP, CandidateEval, Outcome
from fun_fish.personalities.romantic import Romantic


class SlyOutcome(Outcome):
    """Bilan d'un sous-arbre, avec en plus la probabilité d'une vraie erreur adverse."""

    def __init__(self, *args, error: float = 0.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.error = error

    def add(self, other: Outcome, weight: float, prefix: list[str]) -> None:
        super().add(other, weight, prefix)
        self.error += weight * getattr(other, "error", 0.0)


class Corsair(Romantic):
    key = "corsair"
    name = "Le corsaire"
    description = ("Le romantique avec l'œil de l'arnaqueur : à chaque coup, il pèse l'attaque et le piège "
                   "ensemble, et suit le meilleur des deux.")
    OWN_HELP = {
        "w_trap": "Fusion : poids du piège (ce que la naïveté adverse rapporte, par pion)",
        "trap_cap": "Fusion : piège compté au plus (pions)",
        "w_sharp": "Fusion : poids de l'étroitesse (probabilité d'une vraie erreur adverse)",
        "error_margin": "Fusion : perte adverse (cp) qui compte comme une vraie erreur",
        "safe_margin": "Fusion : nos coups rusés restent à moins de tant (cp) du meilleur",
        "our_options": "Fusion : nos réponses envisagées pour refermer un piège (1 = le meilleur coup seul)",
        "lead_share": "Fusion : part des motifs secondaires (0 = seul le meilleur motif compte, 1 = somme)",
    }

    def __init__(self, w_trap: float = 0.5, trap_cap: float = 3.0, w_sharp: float = 1.0, error_margin: int = 150,
                 safe_margin: int = 60, our_options: int = 2, lead_share: float = 0.3, **kwargs):
        super().__init__(**kwargs)
        self.w_trap, self.trap_cap, self.w_sharp = w_trap, trap_cap, w_sharp
        self.error_margin, self.safe_margin, self.our_options = error_margin, safe_margin, our_options
        self.lead_share = lead_share

    @classmethod
    def param_specs(cls) -> list[dict]:
        specs = Romantic.param_specs()
        for name, param in inspect.signature(cls.__init__).parameters.items():
            if name in cls.OWN_HELP:
                specs.append({"name": name, "default": param.default,
                              "type": "int" if isinstance(param.default, int) else "float",
                              "help": cls.OWN_HELP[name]})
        return specs

    # ---------- arbre ----------

    def their_turn(self, position: chess.Board, analyser: Analyser, moves_left: int, us: chess.Color,
                   base: int) -> Outcome:
        """Comme le cogneur, en notant les vraies erreurs de l'adversaire."""
        probs = self.model.probabilities(position, analyser)
        best = analyser.analyse(position, multipv=1, depth=self.attack_depth)[0]
        correct = -best.score  # notre éval s'il répond juste
        followed, mass = [], 0.0
        for move, p in sorted(probs.items(), key=lambda x: -x[1]):
            if len(followed) >= self.branching or mass >= self.plausible_mass or p < self.min_probability:
                break
            followed.append(move)
            mass += p
        weights = {m: probs[m] for m in followed}
        weights[best.move] = weights.get(best.move, 0.0) + (1.0 - sum(weights.values()))

        outcome = SlyOutcome()
        for move, weight in weights.items():
            if weight <= 0:
                continue
            san = position.san(move)
            child = position.copy(stack=False)
            child.push(move)
            if child.is_checkmate():
                sub = SlyOutcome(value=-CLIP)
            elif child.is_game_over():
                sub = SlyOutcome(value=0.0, error=float(correct >= self.error_margin))
            else:
                # même analyse que celle de our_turn : servie par le cache
                ours = analyser.analyse(child, multipv=1, depth=self.attack_depth)[0]
                erred = move != best.move and ours.score - correct >= self.error_margin
                sub = self.our_turn(child, analyser, moves_left, us, base)
                if erred:
                    sub = self._erred(sub)
            outcome.add(sub, weight, [san])
        return outcome

    @staticmethod
    def _erred(sub: Outcome) -> SlyOutcome:
        out = SlyOutcome(mate=sub.mate, win=sub.win, value=sub.value, best=sub.best, error=1.0)
        return out

    def our_turn(self, position: chess.Board, analyser: Analyser, moves_left: int, us: chess.Color,
                 base: int) -> Outcome:
        """À notre premier coup de l'arbre, parmi nos coups quasi optimaux, le plus rusé."""
        if self.our_options <= 1 or moves_left != self._horizon or moves_left <= 1:
            return super().our_turn(position, analyser, moves_left, us, base)
        lines = analyser.analyse(position, multipv=self.our_options, depth=self.attack_depth)
        chosen, chosen_motive = None, -1.0
        for line in lines:
            if lines[0].score - line.score > self.safe_margin:
                break
            sub = self._after(position, line, analyser, moves_left, us, base)
            motive = self.lead(self.motives(sub, line.score))
            if chosen is None or motive > chosen_motive:
                chosen, chosen_motive = sub, motive
        return chosen

    def _after(self, position: chess.Board, line: Line, analyser: Analyser, moves_left: int, us: chess.Color,
               base: int) -> Outcome:
        """Notre coup `line`, puis le reste de l'arbre (comme our_turn du cogneur, pour un coup imposé)."""
        if line.mate is not None and 0 < line.mate <= self._mate_now:
            return SlyOutcome(mate=1.0, win=1.0, value=CLIP,
                              best=(1.0, [position.san(line.move), f"(mat en {line.mate})"]))
        child = position.copy(stack=False)
        child.push(line.move)
        if child.is_game_over():
            return SlyOutcome(value=CLIP if child.is_checkmate() else 0.0, mate=float(child.is_checkmate()),
                              win=float(child.is_checkmate()))
        sub = self.their_turn(child, analyser, moves_left - 1, us, base)
        if sub.best is not None:
            sub.best = (sub.best[0], [position.san(line.move), *sub.best[1]])
        return sub

    def evaluate(self, board: chess.Board, line: Line, best: Line, analyser: Analyser, moves: int) -> CandidateEval:
        self._horizon = moves
        return super().evaluate(board, line, best, analyser, moves)

    # ---------- motifs ----------

    def motives(self, o: Outcome, own: int) -> dict[str, float]:
        trap = max(0.0, max(-CLIP, min(CLIP, o.value)) - max(-CLIP, min(CLIP, own))) / 100
        urge = self.urge()
        return {"mat": urge * self.w_mate * o.mate, "gain": urge * self.w_win * max(0.0, o.win - o.mate),
                "piège": self.w_trap * min(trap, self.trap_cap), "étroitesse": self.w_sharp * getattr(o, "error", 0.0)}

    def lead(self, motives: dict[str, float]) -> float:
        top = max(motives.values(), default=0.0)
        return top + self.lead_share * (sum(motives.values()) - top)

    def scored(self, e: CandidateEval, best: Line) -> CandidateEval:
        e.score = self.lead(self.motives(e.outcome, e.line.score)) - self.w_loss * e.loss / 100
        return e

    def opportunity(self, e: CandidateEval) -> float:
        return self.lead(self.motives(e.outcome, e.line.score))

    def explain(self, san: str, e: CandidateEval, taste: str | None = None) -> str:
        m = self.motives(e.outcome, e.line.score)
        main = max(m, key=m.get)
        o = e.outcome
        if main in ("piège", "étroitesse") and m[main] >= 0.3:
            error = getattr(o, "error", 0.0)
            text = f"Joue {san} pour tendre un piège : un joueur à {self._elo_now} Elo s'y trompe dans {error:.0%} des cas"
            text += f" et rapporte {min(self.trap_cap, (o.value - e.line.score) / 100):+.1f} en moyenne."
            if o.mate >= 0.05 or o.win >= 0.1:
                text += f" Et l'attaque suit : mat {o.mate:.0%}, gain {max(0.0, o.win - o.mate):.0%}."
            return text
        return super().explain(san, e, taste)
