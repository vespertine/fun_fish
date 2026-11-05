"""Réglage paramètre par paramètre : une référence, puis chaque paramètre modifié seul.

Toutes les séries jouent les mêmes parties (mêmes graines, mêmes adversaires) : seules les
différences de réglage les séparent. Pour chaque série :

- **score** et **mats** (part des parties gagnées par mat) ;
- **rapidité** : nombre moyen de coups de la partie, une partie non gagnée par mat comptant pour
  `NO_MATE` coups (c'est le critère principal : mater vite, et ne pas rater la victoire) ;
- **allure** : sacrifices et fulgurances (échec ou prise qu'un 1800 ne jouerait presque jamais)
  par partie, et coups calmes « machine » ;
- **plan** : part des plans d'attaque poursuivis quand ils étaient encore jouables ;
- **temps** par coup.
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

import chess

NO_MATE = 100  # coups comptés pour une partie qui ne finit pas par notre mat


def flair(records: list[dict]) -> dict:
    from fun_fish.opponent import MaiaModel
    from fun_fish.personalities.base import forcing
    from fun_fish.style import material

    maia = MaiaModel(1800)
    lengths, sacs, sparks, calm, planned, kept, secs, moves = [], 0, 0, 0, 0, 0, 0.0, 0
    for r in records:
        us = chess.WHITE if r["agent_color"] == "white" else chess.BLACK
        board, mats, ours = chess.Board(), [], []
        mats.append(material(board, us))
        for uci in r["moves"]:
            m = chess.Move.from_uci(uci)
            if board.turn == us and board.ply() >= 10:
                ours.append((board.copy(stack=False), m, board.ply()))
            board.push(m)
            mats.append(material(board, us))
        lengths.append((r["plies"] + 1) // 2 if r.get("agent_mated") else NO_MATE)
        maia.prefetch([b for b, _, _ in ours])
        for b, m, ply in ours:
            p = maia.probabilities(b, None).get(m, 0.0)
            if p < 0.02:
                if forcing(b, m):
                    sparks += 1
                else:
                    calm += 1
            if ply + 4 < len(mats) and mats[ply + 2] <= mats[ply] - 2 and mats[ply + 4] <= mats[ply] - 2:
                sacs += 1
        plan = r.get("plan") or {}
        planned += plan.get("planned", 0)
        kept += plan.get("kept", 0)
        secs += r.get("agent_seconds", 0.0)
        moves += max(1, r["plies"] // 2)
    n = len(records)
    return {"games": n, "score": sum(r["score"] for r in records) / n,
            "mates": sum(bool(r.get("agent_mated")) for r in records) / n,
            "speed": statistics.mean(lengths),
            "speed_se": statistics.stdev(lengths) / math.sqrt(n) if n > 1 else 0.0,
            "sacrifices": sacs / n, "sparks": sparks / n, "calm": calm / n,
            "plan": kept / planned if planned else None, "seconds": secs / moves}


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def report(directory: Path) -> str:
    files = sorted(directory.glob("*.jsonl"))
    base = directory / "baseline.jsonl"
    rows = {f.stem: flair(load(f)) for f in files}
    ref = rows.get("baseline")
    lines = [f"{'série':<28}{'parties':>8}{'score':>7}{'mats':>6}{'rapidité':>16}{'Δ':>7}{'sacrif.':>8}"
             f"{'fulgur.':>8}{'calme':>7}{'plan':>6}{'s/coup':>8}"]
    for name in (["baseline"] if base.exists() else []) + [n for n in rows if n != "baseline"]:
        m = rows[name]
        delta = f"{m['speed'] - ref['speed']:+7.1f}" if ref and name != "baseline" else " " * 7
        plan = f"{m['plan']:6.0%}" if m["plan"] is not None else "     -"
        lines.append(f"{name:<28}{m['games']:>8}{m['score']:>7.0%}{m['mates']:>6.0%}"
                     f"{m['speed']:>9.1f} ± {m['speed_se']:<4.1f}{delta}{m['sacrifices']:>8.2f}{m['sparks']:>8.2f}"
                     f"{m['calm']:>7.2f}{plan}{m['seconds']:>8.1f}")
    lines.append(f"rapidité : coups par partie (une partie sans notre mat compte {NO_MATE}) ; ± erreur type. "
                 "Un écart n'est net que s'il dépasse environ deux erreurs types.")
    return "\n".join(lines)
