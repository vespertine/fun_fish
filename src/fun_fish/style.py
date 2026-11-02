"""Étage de style : des goûts de joueur pour départager des coups sains.

Chaque dimension est mesurée sur la position après le coup, presque toujours avec
python-chess seul (pas d'appel à Stockfish), du point de vue du camp qui joue :

- **develop** : pièces mineures sorties et roque (pèse surtout en ouverture) ;
- **open** : colonnes ouvertes ou semi-ouvertes pour nous, et tension (prises possibles) ;
- **king** : cases autour du roi adverse attaquées, et échecs à notre disposition ;
- **activity** : mobilité de nos pièces et cases contrôlées dans le camp adverse ;
- **initiative** : éval moins le bilan matériel. Garder une bonne éval avec moins de matériel,
  c'est jouer la compensation dynamique plutôt que le matériel garanti (Morphy) ;
- **sacrifice** : matériel donné (une fois les échanges finis) par un coup qui reste sain ;
  pèse surtout quand la position est équilibrée ;
- **technique** (conversion) : quand on mène nettement, simplifier, pousser ses pions passés,
  freiner ceux de l'adversaire et activer son roi en finale. Son poids vient du degré de
  conversion de l'étage de santé (voir `Soundness`), pas d'un réglage de style ;
- **surprise** : peu probable pour un humain fort (Maia à 2000), en ouverture seulement.
  Sortir l'adversaire de la théorie (« dark forest ») ; à manier avec prudence.

Les mesures sont centrées et réduites parmi les candidats : un poids de 1 vaut à peu près
un écart type. Le bonus total s'ajoute au score de la personnalité.
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
          "technique": "il convertit"}

STYLE_HELP = {
    "w_develop": "Style : développement, roque (ouverture)",
    "w_open": "Style : ouvrir les lignes (colonnes, tension)",
    "w_king": "Style : pression sur le roi adverse",
    "w_activity": "Style : mobilité, cases contrôlées chez l'adversaire",
    "w_initiative": "Style : initiative plutôt que matériel (éval moins matériel)",
    "w_sacrifice": "Style : goût du sacrifice sain, surtout à position égale",
    "w_surprise": "Style : coups peu théoriques en ouverture, selon Maia (dark forest)",
    "style_first": "Style d'abord : ne garde que les N coups les plus à son goût avant de calculer (0 = non)",
}


@dataclass
class Style:
    w_develop: float = 0.0
    w_open: float = 0.0
    w_king: float = 0.0
    w_activity: float = 0.0
    w_initiative: float = 0.0
    w_sacrifice: float = 0.0
    w_surprise: float = 0.0

    def __post_init__(self):
        self.reasons: dict[chess.Move, str] = {}  # ce qui plaît le plus dans chaque coup

    @classmethod
    def of(cls, obj) -> "Style":
        return cls(**{f.name: getattr(obj, f.name) for f in fields(cls)})

    def active(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))

    def bonuses(self, board: chess.Board, lines: list[Line], best: Line,
                conversion: float = 0.0) -> dict[chess.Move, float]:
        """Bonus de style de chaque coup (somme pondérée des mesures normalisées) ; `conversion`
        est le poids de la technique de conversion."""
        if not (self.active() or conversion > 0) or len(lines) < 2:
            self.reasons = {}
            return {line.move: 0.0 for line in lines}
        weights = {f.name[2:]: getattr(self, f.name) for f in fields(self)} | {"technique": conversion}
        raw = {name: [] for name in weights}
        opening = max(0.0, 1.0 - board.ply() / OPENING_PLIES)
        equal = math.exp(-abs(best.score) / 200)  # 1 à position égale, ~0 quand l'un domine
        human = _maia_probabilities(board) if self.w_surprise and opening > 0 else {}
        us = board.turn
        base = material(board, us)
        for line in lines:
            after = board.copy(stack=False)
            after.push(line.move)
            settled = settle(board, line.pv)
            delta = material(settled, us) - base
            given = max(0, -delta)
            raw["develop"].append(opening * (development(after, us) - development(board, us)))
            raw["open"].append(open_lines(after, us))
            raw["king"].append(king_pressure(after, us))
            raw["activity"].append(activity(after, us))
            raw["initiative"].append(max(-1000, min(1000, line.score)) / 100 - delta)
            raw["sacrifice"].append(equal * given)
            p = human.get(line.move)
            raw["surprise"].append(opening * -math.log(max(p, 1e-4)) if p is not None else 0.0)
            raw["technique"].append(technique(settled, us) if conversion > 0 else 0.0)
        bonus = [0.0] * len(lines)
        top = [(0.5, "")] * len(lines)  # (contribution, dimension) : au moins un demi-point pour être cité
        for name, values in raw.items():
            weight = weights[name]
            if weight:
                for i, z in enumerate(_standardise(values)):
                    bonus[i] += weight * z
                    top[i] = max(top[i], (weight * z, LABELS[name]))
        self.reasons = {line.move: reason for line, (_, reason) in zip(lines, top) if reason}
        return {line.move: b for line, b in zip(lines, bonus)}


def _standardise(values: list[float]) -> list[float]:
    mean = sum(values) / len(values)
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    return [0.0] * len(values) if sd < 1e-9 else [(v - mean) / sd for v in values]


def _maia_probabilities(board: chess.Board) -> dict[chess.Move, float]:
    from fun_fish.sim import maia

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


def development(board: chess.Board, color: chess.Color) -> float:
    minors = sum(1 for p in MINORS for sq in board.pieces(p, color) if not chess.BB_SQUARES[sq] & HOME[color])
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
    checks = sum(1 for m in view.pseudo_legal_moves if view.gives_check(m)) if not view.is_check() else 0
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
