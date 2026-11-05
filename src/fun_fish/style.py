"""Étage de style : des goûts de joueur pour départager des coups sains.

Chaque dimension est mesurée sur la position après le coup, presque toujours avec
python-chess seul (pas d'appel à Stockfish), du point de vue du camp qui joue :

- **develop** : pièces mineures sorties et roque (pèse surtout en ouverture) ;
- **open** : colonnes ouvertes ou semi-ouvertes pour nous, et tension (prises possibles) ;
- **king** : cases autour du roi adverse attaquées, et échecs à notre disposition ;
- **activity** : mobilité de nos pièces et cases contrôlées dans le camp adverse ;
- **initiative** : éval plus le matériel donné. Garder une bonne éval avec moins de matériel,
  c'est jouer la compensation dynamique plutôt que le matériel garanti (Morphy). Prendre du
  matériel gratuit n'est pas pénalisé : ce serait refuser les cadeaux ;
- **sacrifice** : matériel donné (une fois les échanges finis) par un coup qui reste sain ;
  pèse surtout quand la position est équilibrée ;
- **technique** (conversion) : quand on mène nettement, simplifier, pousser ses pions passés,
  freiner ceux de l'adversaire et activer son roi en finale. Son poids vient du degré de
  conversion de l'étage de santé (voir `Soundness`), pas d'un réglage de style ;
- **surprise** : rare chez les humains forts (livre d'ouverture à 2000, sinon Maia), en ouverture.
  Sortir l'adversaire de la théorie (« dark forest ») ; à manier avec prudence.

- **center** : pions au centre (d4, e4, d5, e5, et à moitié sur c et f) et contrôle des
  cases centrales ;
- **threat** : gain de temps, pièces adverses attaquées par moins fort qu'elles ou sans
  défense (harceler la dame avec un cavalier).

Les mesures sont centrées et réduites parmi les candidats, avec un écart type plancher propre
à chaque dimension (`FLOORS`) : une différence minuscule entre coups ne devient pas une
opinion tranchée. Un poids de 1 vaut à peu près une unité naturelle de la dimension.

Le bonus total s'ajoute au score de la personnalité, qui le tempère quand l'attaque a une
idée nettement meilleure (voir `Attacker.combine`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields

import chess

from fun_fish.analysis import Line

MINORS = (chess.KNIGHT, chess.BISHOP)
HOME = {chess.WHITE: chess.BB_RANK_1, chess.BLACK: chess.BB_RANK_8}
CASTLED_KING = {chess.WHITE: (chess.G1, chess.C1, chess.B1, chess.H1),
                chess.BLACK: (chess.G8, chess.C8, chess.B8, chess.H8)}
OPENING_PLIES = 24  # au-delà, le développement et la surprise ne comptent plus
SURPRISE_ELO = 2000
LABELS = {"develop": "il se développe", "open": "il ouvre les lignes", "king": "il vise le roi",
          "activity": "il active ses pièces", "initiative": "il joue l'initiative plutôt que le matériel",
          "sacrifice": "il sacrifie", "surprise": "il sort des sentiers battus",
          "technique": "il convertit", "counterplay": "il cherche du contre-jeu", "center": "il occupe le centre", "threat": "il gagne du temps"}
# écart type minimal de chaque mesure, dans ses unités : en deçà, pas d'opinion tranchée
FLOORS = {"develop": 0.5, "open": 1.0, "king": 1.0, "activity": 3.0, "initiative": 0.3, "sacrifice": 0.5,
          "surprise": 0.5, "technique": 1.0, "counterplay": 0.5, "center": 0.5, "threat": 0.5}
CENTER = chess.BB_D4 | chess.BB_E4 | chess.BB_D5 | chess.BB_E5
FLANK_CENTER = chess.BB_C4 | chess.BB_F4 | chess.BB_C5 | chess.BB_F5
VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}

STYLE_HELP = {
    "w_develop": "Style : développement, roque (ouverture)",
    "w_open": "Style : ouvrir les lignes (colonnes, tension)",
    "w_king": "Style : pression sur le roi adverse",
    "w_activity": "Style : mobilité, cases contrôlées chez l'adversaire",
    "w_initiative": "Style : initiative plutôt que matériel (éval moins matériel)",
    "w_sacrifice": "Style : goût du sacrifice sain, surtout à position égale",
    "w_surprise": "Style : coups peu théoriques en ouverture, selon Maia (dark forest)",
    "w_center": "Style : pions et contrôle au centre",
    "w_threat": "Style : gagner du temps en attaquant les pièces adverses",
    "w_repertoire": "Répertoire : poids du coup prévu, les parties où il en a envie",
    "repertoire_rate": "Répertoire : part des parties où il a envie de suivre une ligne connue (0 à 1)",
    "mood": "Humeur : variation aléatoire des goûts, tirée à chaque partie (0 = aucune, 0.3 = sensible)",
    "style_margin": "Dialogue attaque-style : écart d'occasion (en points de score) qui fait taire le style d'un tiers",
    "style_first": "Style d'abord : ne garde que les N coups les plus à son goût avant de calculer (0 = non)",
}


@dataclass
class Style:
    floor_scale = 1.0  # (pas un goût) 0 : sans écart type plancher, comme le premier romantique
    bishop_release = 0.5  # (pas un goût) développement compté pour un fou libéré ; 0 chez le premier romantique
    w_develop: float = 0.0
    w_open: float = 0.0
    w_king: float = 0.0
    w_activity: float = 0.0
    w_initiative: float = 0.0
    w_sacrifice: float = 0.0
    w_surprise: float = 0.0
    w_center: float = 0.0
    w_threat: float = 0.0
    w_repertoire: float = 0.0

    def __post_init__(self):
        self.reasons: dict[chess.Move, str] = {}  # ce qui plaît le plus dans chaque coup

    @classmethod
    def of(cls, obj) -> "Style":
        return cls(**{f.name: getattr(obj, f.name, f.default) for f in fields(cls)})

    def active(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))

    def bonuses(self, board: chess.Board, lines: list[Line], best: Line,
                conversion: float = 0.0, planned: tuple[chess.Move, str] | None = None,
                counterplay: float = 0.0) -> dict[chess.Move, float]:
        """Bonus de style de chaque coup (somme pondérée des mesures normalisées) ; `conversion`
        est le poids de la technique de conversion, `counterplay` celui du contre-jeu en défense."""
        if not (self.active() or conversion > 0 or counterplay > 0) or len(lines) < 2:
            self.reasons = {}
            return {line.move: 0.0 for line in lines}
        weights = {f.name[2:]: getattr(self, f.name) for f in fields(self) if f.name != "w_repertoire"}
        weights |= {"technique": conversion, "counterplay": counterplay}
        raw = {name: [] for name in weights}
        opening = max(0.0, 1.0 - board.ply() / OPENING_PLIES)
        equal = math.exp(-abs(best.score) / 200)  # 1 à position égale, ~0 quand l'un domine
        middlegame = middlegame_weight(board)  # lignes, roi, activité : des goûts de milieu de partie
        human = _maia_probabilities(board) if self.w_surprise and opening > 0 else {}
        us = board.turn
        base = material(board, us)
        for line in lines:
            after = board.copy(stack=False)
            after.push(line.move)
            settled = settle(board, line.pv)
            delta = material(settled, us) - base
            given = max(0, -delta)
            raw["develop"].append(opening * (development(after, us, self.bishop_release)
                                                     - development(board, us, self.bishop_release)))
            raw["open"].append(middlegame * open_lines(after, us))
            raw["king"].append(king_pressure(after, us))  # viser le roi reste un goût jusqu'au mat
            raw["activity"].append(middlegame * activity(after, us))
            raw["initiative"].append(max(-1000, min(1000, line.score)) / 100 + given)
            raw["sacrifice"].append(equal * given)
            p = human.get(line.move)
            raw["surprise"].append(opening * -math.log(max(p, 1e-4)) if p is not None else 0.0)
            raw["technique"].append(technique(settled, us) if conversion > 0 else 0.0)
            raw["counterplay"].append(questions(after, us) if counterplay > 0 else 0.0)
            raw["center"].append(middlegame * center(after, us))
            raw["threat"].append(threats(after, us))
        bonus = [0.0] * len(lines)
        gave = [g > 0 for g in raw["sacrifice"]]  # coups qui donnent du matériel
        top = [(0.5, "")] * len(lines)  # (contribution, dimension) : au moins un demi-point pour être cité
        for name, values in raw.items():
            weight = weights[name]
            if weight:
                for i, z in enumerate(_standardise(values, FLOORS[name] * self.floor_scale)):
                    bonus[i] += weight * z
                    if name not in ("initiative", "sacrifice") or gave[i]:  # pas pour une prise
                        top[i] = max(top[i], (weight * z, LABELS[name]))
        if planned is not None and self.w_repertoire:  # pas de normalisation : un goût franc
            for i, line in enumerate(lines):
                if line.move == planned[0]:
                    bonus[i] += self.w_repertoire
                    top[i] = (math.inf, f"envie de jouer : {planned[1]}")
        self.reasons = {line.move: reason for line, (_, reason) in zip(lines, top) if reason}
        return {line.move: b for line, b in zip(lines, bonus)}


    def moody(self, factors: dict[str, float]) -> "Style":
        """Le même style, chaque goût multiplié par son facteur d'humeur."""
        return Style(**{f.name: getattr(self, f.name) * factors.get(f.name, 1.0) for f in fields(self)})


def _standardise(values: list[float], floor: float = 0.0) -> list[float]:
    mean = sum(values) / len(values)
    sd = max(floor, math.sqrt(sum((v - mean) ** 2 for v in values) / len(values)))
    return [0.0] * len(values) if sd < 1e-9 else [(v - mean) / sd for v in values]


def center(board: chess.Board, color: chess.Color) -> float:
    """Pions au centre (moitié sur les colonnes c et f), plus 0,2 par attaque sur une case centrale."""
    pawns = board.pieces_mask(chess.PAWN, color)
    score = chess.popcount(pawns & CENTER) + 0.5 * chess.popcount(pawns & FLANK_CENTER)
    return score + 0.2 * sum(len(board.attackers(color, sq)) for sq in chess.SquareSet(CENTER))


def questions(board: chess.Board, color: chess.Color) -> float:
    """Ce que le coup demande à l'adversaire : un échec, des pièces menacées, son roi visé."""
    return 1.5 * board.is_check() + threats(board, color) + 0.3 * king_pressure(board, color)


def threats(board: chess.Board, color: chess.Color) -> float:
    """Pièces adverses (hors pions) attaquées par moins fort qu'elles, ou sans défense : en
    valeur, sur 3 (un cavalier menacé vaut 1, la dame 3)."""
    total = 0.0
    for sq in chess.SquareSet(board.occupied_co[not color] & ~board.pawns & ~board.kings):
        target = VALUES[board.piece_type_at(sq)]
        attackers = [VALUES[board.piece_type_at(a)] or 100 for a in board.attackers(color, sq)]
        if attackers and (min(attackers) < target or not board.attackers(not color, sq)):
            total += target / 3
    return total


def _maia_probabilities(board: chess.Board) -> dict[chess.Move, float]:
    """Ce que jouerait un humain fort : le livre d'ouverture, sinon Maia."""
    from fun_fish import book
    from fun_fish.sim import maia

    opening = book.shared()
    found = opening.probabilities(board, SURPRISE_ELO) if opening is not None else None
    if found is not None:
        return found
    if not maia.available():
        return {}
    return maia.shared().probabilities(board, SURPRISE_ELO)


# ---------- mesures ----------

def material(board: chess.Board, color: chess.Color) -> int:
    from fun_fish.reading import PIECE_VALUES

    return sum(v * (len(board.pieces(p, color)) - len(board.pieces(p, not color))) for p, v in PIECE_VALUES.items())


def settle(board: chess.Board, pv: tuple[chess.Move, ...], plies: int = 6) -> chess.Board:
    """La position une fois les prises en cours terminées le long de la variation."""
    b = board.copy(stack=False)
    for i, move in enumerate(pv[:plies]):
        if not b.is_legal(move) or (i >= 1 and not (b.is_capture(move) or move.promotion)):
            break
        b.push(move)
    return b


def settled_material(board: chess.Board, pv: tuple[chess.Move, ...], color: chess.Color, plies: int = 6) -> int:
    """Bilan matériel une fois les prises en cours terminées le long de la variation."""
    return material(settle(board, pv, plies), color)


def middlegame_weight(board: chess.Board) -> float:
    """1 avec 12 pièces (hors pions et rois) ou plus, 0 avec 4 ou moins."""
    pieces = chess.popcount(board.occupied & ~board.pawns & ~board.kings)
    return max(0.0, min(1.0, (pieces - 4) / 8))


def development(board: chess.Board, color: chess.Color, release: float = 0.5) -> float:
    """Pièces mineures sorties, un demi-point par fou libéré (1.e4, 1.d4...) et le roque."""
    minors = sum(1 for p in MINORS for sq in board.pieces(p, color) if not chess.BB_SQUARES[sq] & HOME[color])
    minors += release * sum(1 for sq in board.pieces(chess.BISHOP, color)
                        if chess.BB_SQUARES[sq] & HOME[color] and board.attacks_mask(sq) & ~board.occupied_co[color])
    king = board.king(color)
    castled = king in CASTLED_KING[color] and not board.has_castling_rights(color)
    return minors + 1.5 * castled


def open_lines(board: chess.Board, color: chess.Color) -> float:
    files = 0.0
    for f in range(8):
        mask = chess.BB_FILES[f]
        ours = board.pieces_mask(chess.PAWN, color) & mask
        theirs = board.pieces_mask(chess.PAWN, not color) & mask
        files += 1.0 if not ours and not theirs else 0.5 if not ours else 0.0
    tension = sum(1 for sq in board.pieces(chess.PAWN, color) if board.attacks_mask(sq) & board.occupied_co[not color])
    tension += sum(1 for sq in board.pieces(chess.PAWN, not color) if board.attacks_mask(sq) & board.occupied_co[color])
    return files + tension


def king_pressure(board: chess.Board, color: chess.Color) -> float:
    king = board.king(not color)
    if king is None:
        return 0.0
    zone = chess.SquareSet(board.attacks_mask(king) | chess.BB_SQUARES[king])
    hits = sum(len(board.attackers(color, sq)) for sq in zone)
    view = board.copy(stack=False)
    view.turn = color
    view.ep_square = None
    # le roi adverse déjà en échec : chaque coup « donnerait échec » ; seule l'attaque présente compte (dans hits)
    already = board.is_attacked_by(color, king)
    checks = sum(1 for m in view.pseudo_legal_moves if view.gives_check(m)) if not (view.is_check() or already) else 0
    return hits + 0.5 * checks


def _advance(square: chess.Square, color: chess.Color) -> int:
    """Rangées parcourues par un pion (1 sur sa case de départ, 6 à un pas de la promotion)."""
    rank = chess.square_rank(square)
    return rank if color == chess.WHITE else 7 - rank


def passed_pawns(board: chess.Board, color: chess.Color) -> list[chess.Square]:
    passed = []
    for sq in board.pieces(chess.PAWN, color):
        f, r = chess.square_file(sq), chess.square_rank(sq)
        ahead = range(r + 1, 8) if color == chess.WHITE else range(0, r)
        blockers = [chess.square(ff, rr) for ff in (f - 1, f, f + 1) if 0 <= ff < 8 for rr in ahead]
        if not any(board.piece_at(b) == chess.Piece(chess.PAWN, not color) for b in blockers):
            passed.append(sq)
    return passed


def technique(board: chess.Board, color: chess.Color) -> float:
    """Technique de conversion, quand on mène : moins de pièces sur l'échiquier, nos pions passés
    loin, ceux de l'adversaire freinés, notre roi actif en finale (près de leurs pions passés)."""
    pieces = chess.popcount(board.occupied & ~board.pawns & ~board.kings)
    ours = passed_pawns(board, color)
    theirs = passed_pawns(board, not color)
    push = sum((_advance(sq, color) - 1) ** 2 for sq in ours) / 4
    danger = 0.0
    for sq in theirs:
        threat = (_advance(sq, not color) - 1) ** 2 / 4
        front = sq + (-8 if color == chess.WHITE else 8)  # la case devant leur pion (il descend vers nous)
        if 0 <= front < 64 and (board.piece_at(front) or board.is_attacked_by(color, front)):
            threat /= 2  # bloqué ou surveillé
        danger += threat
    king_play = 0.0
    king = board.king(color)
    if pieces <= 6 and king is not None:
        targets = theirs or [chess.square(3, 3), chess.square(4, 4)]
        king_play = 7 - min(chess.square_distance(king, t) for t in targets)
    return -pieces + push - 1.5 * danger + 0.5 * king_play


def activity(board: chess.Board, color: chess.Color) -> float:
    view = board.copy(stack=False)
    view.turn = color
    mobility = sum(1 for m in view.pseudo_legal_moves if view.piece_type_at(m.from_square) != chess.PAWN)
    enemy_half = chess.BB_RANK_5 | chess.BB_RANK_6 | chess.BB_RANK_7 | chess.BB_RANK_8 if color == chess.WHITE \
        else chess.BB_RANK_1 | chess.BB_RANK_2 | chess.BB_RANK_3 | chess.BB_RANK_4
    controlled = 0
    for sq in board.pieces(chess.KNIGHT, color) | board.pieces(chess.BISHOP, color) | \
            board.pieces(chess.ROOK, color) | board.pieces(chess.QUEEN, color):
        controlled += chess.popcount(board.attacks_mask(sq) & enemy_half)
    return mobility + 0.5 * controlled


STYLE_WEIGHTS = [f.name for f in fields(Style)]
