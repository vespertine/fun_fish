"""Synthèse d'un fichier de résultats, par tranche d'Elo."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from fun_fish.sim.match import SNAPSHOT_MOVES

EVAL_CLIP = 20  # pions : un mat compterait sinon pour 100 et écraserait les moyennes

TERMINATIONS = {
    "checkmate": "mat", "stalemate": "pat", "insufficient_material": "mat. insuff.",
    "threefold_repetition": "répétition", "fivefold_repetition": "répétition",
    "fifty_moves": "50 coups", "seventyfive_moves": "75 coups", "adjudication": "arbitrage",
}


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _mean(values) -> str:
    values = list(values)
    return f"{statistics.mean(values):+.1f}" if values else "–"


def summarize(records: list[dict]) -> str:
    by_elo: dict[int, list[dict]] = defaultdict(list)
    for r in records:
        by_elo[r["opponent_elo"]].append(r)
    agents = Counter(f"{r['agent']} {r['params'] or ''}".strip() for r in records)

    lines = [f"Agent : {', '.join(agents)}", ""]
    header = (f"{'Elo':>5} {'N':>4} {'Score':>6} {'G/N/P':>11} {'Mat ✓':>6} {'Mat ✗':>6} "
              f"{'Coups mat ✓':>12} {'Long.':>6} {'s/coup':>7}  "
              + " ".join(f"{'Év.' + str(m):>7}" for m in SNAPSHOT_MOVES)
              + "  " + " ".join(f"{'Mat.' + str(m):>7}" for m in SNAPSHOT_MOVES) + "  Fins")
    lines += [header, "-" * len(header)]
    for elo in sorted(by_elo):
        rs = by_elo[elo]
        n = len(rs)
        wins = sum(r["score"] == 1 for r in rs)
        draws = sum(r["score"] == 0.5 for r in rs)
        losses = n - wins - draws
        mates_for = [r for r in rs if r["agent_mated"]]
        mates_against = sum(r["agent_got_mated"] for r in rs)
        mate_moves = [(r["plies"] + 1) // 2 for r in mates_for]
        agent_moves = sum((r["plies"] + (1 if r["agent_color"] == "white" else 0)) // 2 for r in rs) or 1
        evals = {m: [max(-EVAL_CLIP, min(EVAL_CLIP, r["snapshots"][str(m)]["eval"] / 100))
                     for r in rs if str(m) in r["snapshots"]] for m in SNAPSHOT_MOVES}
        mats = {m: [r["snapshots"][str(m)]["material"] for r in rs if str(m) in r["snapshots"]] for m in SNAPSHOT_MOVES}
        ends = Counter(TERMINATIONS.get(r["termination"], r["termination"]) for r in rs)
        lines.append(
            f"{elo:>5} {n:>4} {100 * sum(r['score'] for r in rs) / n:>5.0f}% {f'{wins}/{draws}/{losses}':>11} "
            f"{100 * len(mates_for) / n:>5.0f}% {100 * mates_against / n:>5.0f}% "
            f"{(f'{statistics.median(mate_moves):.0f} (méd.)' if mate_moves else '–'):>12} "
            f"{statistics.mean((r['plies'] + 1) // 2 for r in rs):>6.0f} "
            f"{sum(r['agent_seconds'] for r in rs) / agent_moves:>7.2f}  "
            + " ".join(f"{_mean(evals[m]):>7}" for m in SNAPSHOT_MOVES) + "  "
            + " ".join(f"{_mean(mats[m]):>7}" for m in SNAPSHOT_MOVES) + "  "
            + ", ".join(f"{k} {v}" for k, v in ends.most_common())
        )
    lines += ["", "Score, mats et évals du point de vue de l'agent. Év.N : éval Stockfish (pions, bornée à ±20) après le coup N ;",
              "Mat.N : bilan matériel (pions) ; Coups mat ✓ : durée médiane (en coups) des parties gagnées par mat ;",
              "Long. : longueur moyenne des parties (coups) ; moyennes sur les parties encore en cours au coup N."]
    return "\n".join(lines)
