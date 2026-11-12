"""DuBot : Fun Fish 2 attiré par les positions des joueurs d'attaque (voir `fun_fish.position_style`).

Parmi les coups sains de Fun Fish 2 (pièges, arbre d'attaque et goûts compris), chacun reçoit
`w_pos` × (logit « attaque » de la position d'arrivée − moyenne des candidats). Libre en ouverture :
ni théorie ni répertoire, c'est le goût des positions qui choisit (il a retrouvé seul le gambit écossais).

Réglage `w_pos` 4 : le trait est forcé. Mesures de novembre 2026 (40 parties par série) : 99 % contre
Maia 2000, mais 16 % contre Fun Fish 2 et 19 % contre Crazy Fish, qui ne tombent pas dans ses pièges ;
le style pur (sans pièges) ne coûte rien jusqu'à `w_pos` 2. Sans le modèle de style (onnxruntime ou
fichier absent), il joue comme Fun Fish 2 libre.
"""

from __future__ import annotations

from fun_fish import position_style
from fun_fish.personalities.funfish import FunFish2

EXTRA = {"w_pos": 4.0, "pos_from": 0}
HELP = {"w_pos": "Positions : poids du goût (+ attaque, − solide)",
        "pos_from": "Positions : à partir de ce demi-coup"}


class DuBot(FunFish2):
    key = "dubot"
    name = "DuBot"
    description = "Fun Fish 2 qui recherche les positions des joueurs d'attaque, quitte à y laisser des pions."
    OVERRIDES = {"w_repertoire": 0}

    def __init__(self, **kwargs):
        extra = {k: kwargs.pop(k, v) for k, v in EXTRA.items()}
        super().__init__(**{**self.OVERRIDES, **kwargs})
        for k, v in extra.items():
            setattr(self, k, v)

    @classmethod
    def param_specs(cls) -> list[dict]:
        specs = [{**s, "default": cls.OVERRIDES.get(s["name"], s["default"])} for s in super().param_specs()]
        return specs + [{"name": k, "default": v, "type": "int" if isinstance(v, int) else "float", "help": HELP[k]}
                        for k, v in EXTRA.items()]

    def more_bonus(self, board, sound, best, bonus, style) -> None:
        super().more_bonus(board, sound, best, bonus, style)
        if not self.w_pos or board.ply() < self.pos_from or len(sound) < 2:
            return
        children = []
        for line in sound:
            child = board.copy(stack=False)
            child.push(line.move)
            children.append(child)
        values = position_style.logits(children)
        if values is None:
            return
        mean = float(sum(values)) / len(values)
        for line, v in zip(sound, values):
            extra = self.w_pos * (float(v) - mean)
            bonus[line.move] = bonus.get(line.move, 0.0) + extra
            if extra >= 0.5:
                style.reasons[line.move] = f"une position de joueur d'attaque ({float(v) - mean:+.2f})"

