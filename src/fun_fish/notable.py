"""Les coups notables d'une partie du bot : ceux qui méritent qu'on lise leur justification.

Tirés de l'export de debug, sans analyse en plus : le coût (meilleur candidat moins coup choisi, selon
l'éval de la personnalité), et l'écart entre l'éval du coup choisi et celle de notre coup suivant (la
réponse de l'adversaire est passée entre les deux : un gain, c'est son erreur ; une perte, la nôtre, que
notre analyse n'avait pas vue). Les seuils viennent des parties du serveur (voir `fun-fish opponents
notable`).
"""
from __future__ import annotations

import re

import chess

CLIP = 1000  # cp : au-delà, la partie est jouée
STYLE_COST = 50  # cp : un coup choisi au moins aussi loin du meilleur candidat, par goût
SWING = 150  # cp : écart d'éval d'un de nos coups au suivant (erreur de l'un ou de l'autre)
SAC_DROP = 2  # pions : notre bilan matériel perdu après la réponse adverse, et encore deux demi-coups plus tard
SAC_FLOOR = -100  # cp : … sans que notre éval tombe sous ce seuil (sinon c'est une perte, pas un sacrifice)
TEXT = 200  # caractères gardés de l'explication
# Un piège notable : annoncé probable et payant, et choisi pour lui (il coûte). Mesuré le 17/11 sur 637 parties :
# ~2,4 par partie ; annoncés à 61 % contre des humains, ils ont pris dans 24 % des cas (9 % contre des bots).
TRAP = re.compile(r"s'y trompe dans (\d+)% des cas et rapporte ([+-]?[\d.]+)")
TRAP_CHANCE, TRAP_GAIN, TRAP_COST = 40, 2.0, 30  # %, pions, cp
VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


def material(fen: str, side: chess.Color) -> int:
    board = chess.Board(fen)
    return sum(VALUES[p.piece_type] * (1 if p.color == side else -1) for p in board.piece_map().values())


def clip(score: int) -> int:
    return max(-CLIP, min(CLIP, score))


def highlights(export: dict) -> list[dict]:
    """Les coups notables de notre camp : piège, sacrifice, choix de style, notre erreur, celle de
    l'adversaire, garde-fou. Un coup peut en cumuler plusieurs (une ligne par sorte). Un piège a-t-il pris ?
    Son `swing` le dit (≥ SWING : l'adversaire s'est trompé)."""
    ours = chess.WHITE if export.get("human_color") == "black" else chess.BLACK
    sign = 1 if ours == chess.WHITE else -1
    moves = export.get("moves") or []
    fens = [m.get("fen_before") for m in moves] + [export.get("fen")]  # un export partiel : pas de sacrifice
    guarded = {a.get("fen") for a in export.get("anomalies") or [] if isinstance(a, dict)}
    mine = []  # (index, coup, éval choisie, meilleure éval), de notre point de vue
    for i, m in enumerate(moves):
        candidates = (m.get("decision") or {}).get("candidates") or []
        chosen = next((c for c in candidates if c.get("chosen")), None)
        if m.get("by") != "ai" or chosen is None or chosen.get("score") is None:
            continue
        mine.append((i, m, clip(sign * chosen["score"]), max(clip(sign * c["score"]) for c in candidates
                                                             if c.get("score") is not None)))
    found = []
    for k, (i, m, chosen, best) in enumerate(mine):
        decision = m["decision"]
        text = (decision.get("explanation") or "")[:TEXT]
        after = mine[k + 1][3] if k + 1 < len(mine) and mine[k + 1][0] == i + 2 else None  # notre coup suivant
        swing = None if after is None else after - chosen
        trap = TRAP.search(text)
        chance, gain = (int(trap.group(1)), float(trap.group(2))) if trap else (None, None)
        note = {"ply": m.get("ply", i), "san": m.get("san", "?"), "cost": best - chosen, "swing": swing, "chance": chance,
                "gain": gain, "explanation": text}
        kinds = []
        if trap and chance >= TRAP_CHANCE and gain >= TRAP_GAIN and best - chosen >= TRAP_COST:
            kinds.append("piège")
        if i + 4 < len(fens) and all(fens[j] for j in (i, i + 2, i + 4)) and chosen >= SAC_FLOOR and all(
                material(fens[i], ours) - material(fens[j], ours) >= SAC_DROP for j in (i + 2, i + 4)):
            # perdu dès la réponse de l'adversaire, et pas repris ensuite (le sacrifice du coup suivant
            # n'est pas compté à celui-ci)
            kinds.append("sacrifice")
        if best - chosen >= STYLE_COST:
            kinds.append("style")
        if swing is not None and swing <= -SWING:
            kinds.append("notre erreur")
        if swing is not None and swing >= SWING:
            kinds.append("leur erreur")
        if m.get("fen_before") in guarded:
            kinds.append("garde-fou")
        found += [{**note, "kind": kind} for kind in kinds]
    return found
