"""Dégustation d'ouvertures : quelles lignes connues chaque personnalité choisirait-elle seule ?

On donne un répertoire de lignes nommées. Pour chaque personnalité, sans son propre
répertoire (`w_repertoire = 0`), on lui demande à chacun de ses coups de la ligne sa
distribution de choix (moyenne sur quelques tirages, l'humeur variant d'une partie à l'autre).
L'adversaire, lui, suit la ligne. On obtient :

- **adhésion** : la probabilité qu'elle joue toute la ligne si l'adversaire coopère (le produit
  de ses probabilités à chacun de ses coups) ;
- **écart** : le premier coup où elle s'en détourne le plus, et ce qu'elle préfère à la place.

Les positions communes à plusieurs lignes ne sont évaluées qu'une fois.
"""

from __future__ import annotations

import multiprocessing
import random
from collections import defaultdict

import chess

# (nom, camp de la personnalité, coups)
LINES: list[tuple[str, str, str]] = [
    # --- avec les Blancs ---
    ("Anglaise", "w", "1. c4"),
    ("Réti", "w", "1. Nf3"),
    ("Italienne, Giuoco Piano", "w", "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3"),
    ("Gambit Evans", "w", "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. b4"),
    ("Gambit Jérôme", "w", "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. Bxf7+"),
    ("Deux cavaliers, Fegatello", "w", "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Ng5"),
    ("Fried Liver", "w", "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Ng5 d5 5. exd5 Nxd5 6. Nxf7"),
    ("Espagnole", "w", "1. e4 e5 2. Nf3 Nc6 3. Bb5"),
    ("Écossaise", "w", "1. e4 e5 2. Nf3 Nc6 3. d4 exd4 4. Nxd4"),
    ("Gambit écossais", "w", "1. e4 e5 2. Nf3 Nc6 3. d4 exd4 4. Bc4"),
    ("Ponziani", "w", "1. e4 e5 2. Nf3 Nc6 3. c3"),
    ("Gambit Halloween", "w", "1. e4 e5 2. Nf3 Nc6 3. Nc3 Nf6 4. Nxe5"),
    ("Gambit du roi", "w", "1. e4 e5 2. f4"),
    ("Gambit danois", "w", "1. e4 e5 2. d4 exd4 3. c3"),
    ("Gambit viennois", "w", "1. e4 e5 2. Nc3 Nf6 3. f4"),
    ("Ouverture du fou", "w", "1. e4 e5 2. Bc4"),
    ("Sicilienne ouverte", "w", "1. e4 c5 2. Nf3 d6 3. d4"),
    ("Gambit Smith-Morra", "w", "1. e4 c5 2. d4 cxd4 3. c3"),
    ("Sicilienne Alapine", "w", "1. e4 c5 2. c3"),
    ("Sicilienne Grand Prix", "w", "1. e4 c5 2. Nc3 Nc6 3. f4"),
    ("Caro-Kann, avance", "w", "1. e4 c6 2. d4 d5 3. e5"),
    ("Caro-Kann, gambit Alien", "w", "1. e4 c6 2. d4 d5 3. Nc3 dxe4 4. Nxe4 Nf6 5. Ng5 h6 6. Nxf7"),
    ("Française, avance", "w", "1. e4 e6 2. d4 d5 3. e5"),
    ("Française, 3.Cc3", "w", "1. e4 e6 2. d4 d5 3. Nc3"),
    ("Scandinave, 3.Cc3", "w", "1. e4 d5 2. exd5 Qxd5 3. Nc3"),
    ("Pirc, attaque autrichienne", "w", "1. e4 d6 2. d4 Nf6 3. Nc3 g6 4. f4"),
    ("Gambit dame", "w", "1. d4 d5 2. c4"),
    ("Système de Londres", "w", "1. d4 d5 2. Bf4"),
    ("Gambit Blackmar-Diemer", "w", "1. d4 d5 2. e4"),
    # --- avec les Noirs ---
    ("Ouverte (1...e5)", "b", "1. e4 e5"),
    ("Sicilienne", "b", "1. e4 c5"),
    ("Française", "b", "1. e4 e6"),
    ("Caro-Kann", "b", "1. e4 c6"),
    ("Scandinave", "b", "1. e4 d5"),
    ("Pirc", "b", "1. e4 d6"),
    ("Alekhine", "b", "1. e4 Nf6"),
    ("Moderne", "b", "1. e4 g6"),
    ("Petrov", "b", "1. e4 e5 2. Nf3 Nf6"),
    ("Gambit Stafford", "b", "1. e4 e5 2. Nf3 Nf6 3. Nxe5 Nc6"),
    ("Philidor", "b", "1. e4 e5 2. Nf3 d6"),
    ("Gambit letton", "b", "1. e4 e5 2. Nf3 f5"),
    ("Gambit de l'éléphant", "b", "1. e4 e5 2. Nf3 d5"),
    ("Deux cavaliers", "b", "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6"),
    ("Giuoco (3...Fc5)", "b", "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5"),
    ("Contre-attaque Traxler", "b", "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Ng5 Bc5"),
    ("Espagnole, Morphy", "b", "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6"),
    ("Espagnole, berlinoise", "b", "1. e4 e5 2. Nf3 Nc6 3. Bb5 Nf6"),
    ("Défense Schliemann", "b", "1. e4 e5 2. Nf3 Nc6 3. Bb5 f5"),
    ("Gambit du roi accepté", "b", "1. e4 e5 2. f4 exf4"),
    ("Contre-gambit Falkbeer", "b", "1. e4 e5 2. f4 d5"),
    ("Sicilienne Najdorf", "b", "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6"),
    ("Sicilienne dragon", "b", "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 g6"),
    ("Gambit dame refusé", "b", "1. d4 d5 2. c4 e6"),
    ("Slave", "b", "1. d4 d5 2. c4 c6"),
    ("Gambit dame accepté", "b", "1. d4 d5 2. c4 dxc4"),
    ("Contre-gambit Albin", "b", "1. d4 d5 2. c4 e5"),
    ("Gambit Englund", "b", "1. d4 e5"),
    ("Hollandaise", "b", "1. d4 f5"),
    ("Gambit Budapest", "b", "1. d4 Nf6 2. c4 e5"),
    ("Gambit Benko", "b", "1. d4 Nf6 2. c4 c5 3. d5 b5"),
    ("Est-indienne", "b", "1. d4 Nf6 2. c4 g6"),
    ("Nimzo-indienne", "b", "1. d4 Nf6 2. c4 e6 3. Nc3 Bb4"),
]


def parse(san: str) -> list[chess.Move]:
    board, moves = chess.Board(), []
    for token in san.split():
        if token[0].isdigit():
            continue
        moves.append(board.push_san(token))
    return moves


def nodes(lines=LINES) -> set[tuple[str, ...]]:
    """Les positions (suite de coups UCI) où la personnalité doit choisir."""
    out = set()
    for _, side, san in lines:
        moves = parse(san)
        ours = 0 if side == "w" else 1
        for i in range(ours, len(moves), 2):
            out.add(tuple(m.uci() for m in moves[:i]))
    return out


_worker: dict = {}


def _init(stockfish: str) -> None:
    from fun_fish.analysis import Analyser

    _worker["analyser"] = Analyser(stockfish, threads=1, hash_mb=64)


def _taste(job: tuple) -> tuple[str, tuple[str, ...], dict[str, float], dict[str, str]]:
    """Distribution moyenne des choix d'une personnalité dans une position.

    `key` peut être « étiquette=personnalité » ; `extra` force des réglages (la config d'une partie).
    """
    from fun_fish.personalities import PERSONALITIES, create

    label, path, draws, *rest = job
    extra = rest[0] if rest else {}
    key = label.split("=")[-1]
    names = {s["name"] for s in PERSONALITIES[key].param_specs()}
    params = {"w_repertoire": 0} if "w_repertoire" in names else {}
    params |= extra
    board = chess.Board()
    for uci in path:
        board.push_uci(uci)
    total: dict[str, float] = defaultdict(float)
    sans: dict[str, str] = {}
    for seed in range(draws):
        personality = create(key, rng=random.Random(seed), **params)
        decision = personality.choose_move(board.copy(), _worker["analyser"])
        probs = {c.move.uci(): c.probability or 0.0 for c in decision.candidates}
        if sum(probs.values()) <= 0:
            probs = {decision.move.uci(): 1.0}
        norm = sum(probs.values())
        for uci, p in probs.items():
            total[uci] += p / norm / draws
            sans[uci] = board.san(chess.Move.from_uci(uci))
    return label, path, dict(total), sans


def run(stockfish: str, keys: list[str], draws: int = 3, workers: int = 4, lines=LINES) -> dict:
    """Distributions de choix de chaque personnalité, à chaque position de choix des lignes."""
    jobs = [(k, path, draws) for k in keys for path in sorted(nodes(lines))]
    tastes: dict[str, dict] = {k: {} for k in keys}
    with multiprocessing.Pool(workers, initializer=_init, initargs=(stockfish,)) as pool:
        for key, path, probs, sans in pool.imap_unordered(_taste, jobs):
            tastes[key][" ".join(path)] = {"probs": probs, "sans": sans}
    return tastes


def verdicts(tastes: dict, lines=LINES) -> list[dict]:
    """Pour chaque ligne et chaque personnalité : adhésion et principal écart."""
    rows = []
    for name, side, san in lines:
        moves = parse(san)
        ours = 0 if side == "w" else 1
        row = {"line": name, "side": side, "moves": san, "by": {}}
        for key, table in tastes.items():
            adhesion, worst = 1.0, None
            for i in range(ours, len(moves), 2):
                node = table[" ".join(m.uci() for m in moves[:i])]
                p = node["probs"].get(moves[i].uci(), 0.0)
                adhesion *= p
                if worst is None or p < worst[0]:
                    top = max(node["probs"], key=node["probs"].get)
                    worst = (p, node["sans"].get(top, top), node["probs"][top], i)
            row["by"][key] = {"adhesion": adhesion, "worst": worst}
        rows.append(row)
    return rows


__all__ = ["LINES", "nodes", "run", "verdicts"]


# ---------- répertoire naturel : exploration libre ----------

def explore(stockfish: str, keys: list[str], plies: int = 10, min_weight: float = 0.03, draws: int = 3,
            workers: int = 4, opponent_elo: int = 1500, opponent_mass: float = 0.85,
            opponent_min: float = 0.05, extra: dict[str, dict] | None = None) -> dict:
    """L'arbre d'ouvertures que chaque personnalité joue d'elle-même, avec chaque couleur.

    Aux coups de la personnalité, sa propre distribution (sans répertoire) ; à ceux de
    l'adversaire, les réponses humaines probables (Maia et parties réelles, à `opponent_elo`).
    Une branche est suivie tant que sa fréquence reste au moins `min_weight`.
    """
    from fun_fish.sim.maia import shared

    maia = shared()
    trees: dict[str, dict] = {f"{k}|{side}": {} for k in keys for side in "wb"}
    frontier = [(f"{k}|{side}", (), 1.0) for k in keys for side in "wb"]
    with multiprocessing.Pool(workers, initializer=_init, initargs=(stockfish,)) as pool:
        for ply in range(plies):
            mine = [(t, path, w) for t, path, w in frontier if (ply % 2 == 0) == t.endswith("|w")]
            theirs = [(t, path, w) for t, path, w in frontier if (ply % 2 == 0) != t.endswith("|w")]
            nxt = []
            jobs = [(t.split("|")[0], path, draws, (extra or {}).get(t.split("|")[0], {})) for t, path, _ in mine]
            weights = {(t, path): w for t, path, w in mine}
            for key, path, probs, sans in pool.imap_unordered(_taste, jobs):
                tree = next(t for t in trees if t.startswith(key + "|") and (t, path) in weights)
                w = weights[(tree, path)]
                trees[tree][" ".join(path)] = {"weight": w, "mine": True,
                                               "moves": {sans[u]: p for u, p in sorted(probs.items(), key=lambda x: -x[1])}}
                for uci, p in probs.items():
                    if w * p >= min_weight:
                        nxt.append((tree, (*path, uci), w * p))
            for tree, path, w in theirs:
                board = chess.Board()
                for uci in path:
                    board.push_uci(uci)
                probs = maia.probabilities(board, opponent_elo)
                kept, mass = {}, 0.0
                for move, p in sorted(probs.items(), key=lambda x: -x[1]):
                    if mass >= opponent_mass or p < opponent_min:
                        break
                    kept[move] = p
                    mass += p
                trees[tree][" ".join(path)] = {"weight": w, "mine": False,
                                               "moves": {board.san(m): p / mass for m, p in kept.items()}}
                for move, p in kept.items():
                    if w * p / mass >= min_weight:
                        nxt.append((tree, (*path, move.uci()), w * p / mass))
            frontier = nxt
    return trees


def tree_text(tree: dict, min_weight: float = 0.03) -> str:
    """L'arbre en texte indenté : chaque coup, sa fréquence dans les parties, et qui l'a choisi."""
    out = []

    def walk(path: tuple[str, ...], board: chess.Board, depth: int) -> None:
        node = tree.get(" ".join(path))
        if node is None:
            return
        for san, p in node["moves"].items():
            w = node["weight"] * p
            if w < min_weight:
                continue
            number = f"{board.fullmove_number}." if board.turn else f"{board.fullmove_number}..."
            mark = "" if node["mine"] else "  (adv.)"
            out.append(f"{'  ' * depth}{number}{san}  {w:.0%}{mark}")
            move = board.parse_san(san)
            board.push(move)
            walk((*path, move.uci()), board, depth + 1)
            board.pop()

    walk((), chess.Board(), 0)
    return "\n".join(out)
