"""Banc de performance et de non-régression du choix de coup.

Rejoue des positions fixes (parties du bot sur Lichess + parties de simulation), partie par partie
comme en jeu réel (même agent, même Stockfish, caches chauds d'un coup à l'autre), avec une graine
fixe par position. Pour chaque position : coup tiré, probabilités des candidats, temps, nombre
d'appels Stockfish / Maia. Deux runs se comparent ensuite (mêmes coups ? mêmes probabilités ?).

    python scripts/perfbench.py positions                 # (re)construit runs/perf/positions.json
    python scripts/perfbench.py run LABEL [-s k=v ...]    # → runs/perf/LABEL.jsonl
    python scripts/perfbench.py compare A B
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import random
import statistics
import sys
import time
import zlib
from pathlib import Path

import chess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runs" / "perf"
POSITIONS = OUT / "positions.json"


def build_positions(max_games_sim: int = 6) -> None:
    games = []
    for path in sorted(Path("~/.local/share/fun_fish/bot_games").expanduser().glob("*.json.gz")):
        with gzip.open(path, "rt") as f:
            d = json.load(f)
        moves = [m["uci"] if isinstance(m, dict) else m for m in d["moves"]]
        bot = chess.WHITE if d["human_color"] == "black" else chess.BLACK
        games.append({"id": path.name.split(".")[0], "moves": moves, "bot": bot})
    for name in ("fun_fish_2__maia.jsonl", "fun_fish_2__sf-1.jsonl"):
        with open(ROOT / "runs" / "night" / name) as f:
            rows = [json.loads(l) for l in f][: max_games_sim // 2]
        for i, r in enumerate(rows):
            games.append({"id": f"{name.split('.')[0]}-{i}", "moves": r["moves"],
                          "bot": chess.WHITE if r["agent_color"] == "white" else chess.BLACK})
    out = []
    for g in games:
        plies = [p for p in range(12, min(len(g["moves"]), 100)) if (p % 2 == 0) == (g["bot"] == chess.WHITE)]
        out.append({"id": g["id"], "moves": g["moves"], "plies": plies})
    OUT.mkdir(parents=True, exist_ok=True)
    POSITIONS.write_text(json.dumps(out))
    print(len(out), "parties,", sum(len(g["plies"]) for g in out), "positions")


class Counters:
    def __init__(self):
        from fun_fish.analysis import Analyser
        from fun_fish.sim import maia

        self.c = collections.Counter()
        orig_analyse, orig_compute = Analyser.analyse, maia.Maia._compute
        counters = self.c

        def analyse(self, board, **kw):
            counters["sf"] += 1
            n = len(self._cache)
            t = time.perf_counter()
            r = orig_analyse(self, board, **kw)
            counters["sf_s"] += time.perf_counter() - t
            counters["sf_new"] += len(self._cache) != n
            return r

        def compute(self, boards, elo, opponent_elo):
            counters["maia_calls"] += 1
            counters["maia_boards"] += len(boards)
            t = time.perf_counter()
            r = orig_compute(self, boards, elo, opponent_elo)
            counters["maia_s"] += time.perf_counter() - t
            return r

        Analyser.analyse, maia.Maia._compute = analyse, compute


def play_game(job) -> list[dict]:
    g, params, personality, limit, hash_mb = job
    from fun_fish import personalities
    from fun_fish.analysis import Analyser
    from fun_fish.engines import find_stockfish

    counters = Counters().c
    agent = personalities.create(personality, rng=random.Random(0), **params)
    rows = []
    with Analyser(find_stockfish(None), threads=1, hash_mb=hash_mb) as analyser:
        for ply in g["plies"][:limit]:
            board = chess.Board()
            for uci in g["moves"][:ply]:
                board.push_uci(uci)
            agent.rng = random.Random(zlib.crc32(f"{g['id']}:{ply}".encode()))
            counters.clear()
            t = time.perf_counter()
            decision = agent.choose_move(board, analyser)
            dt = time.perf_counter() - t
            rows.append({"game": g["id"], "ply": ply, "move": decision.move.uci(), "seconds": dt,
                         "candidates": {c.move.uci(): c.probability for c in decision.candidates},
                         **{k: round(v, 4) for k, v in counters.items()}})
    return rows


def run(label: str, params: dict, personality: str, limit: int | None, hash_mb: int, workers: int) -> None:
    import multiprocessing

    games = [g for g in json.loads(POSITIONS.read_text()) if g["plies"]]
    jobs = [(g, params, personality, limit, hash_mb) for g in games]
    t = time.perf_counter()
    with multiprocessing.get_context("spawn").Pool(workers, maxtasksperchild=1) as pool:  # une partie = un processus neuf (Maia froid)
        results = pool.map(play_game, jobs, chunksize=1)
    rows = [r for game in results for r in game]
    with open(OUT / f"{label}.jsonl", "w") as out:
        for r in rows:
            out.write(json.dumps(r) + "\n")
    print(f"{label}: {len(rows)} positions, {statistics.mean(r['seconds'] for r in rows):.2f} s/coup "
          f"({time.perf_counter() - t:.0f} s au total)")


TRUTH = OUT / "truth.json"
TRUTH_DEPTH = 14


def truth_game(g) -> dict:
    from fun_fish.analysis import Analyser
    from fun_fish.engines import find_stockfish

    out = {}
    with Analyser(find_stockfish(None), threads=1, hash_mb=128) as analyser:
        for ply in g["plies"]:
            board = chess.Board()
            for uci in g["moves"][:ply]:
                board.push_uci(uci)
            lines = analyser.analyse(board, multipv=board.legal_moves.count(), depth=TRUTH_DEPTH)
            out[f"{g['id']}:{ply}"] = {l.move.uci(): l.score for l in lines}
    return out


def build_truth(workers: int) -> None:
    """Tous les coups légaux de chaque position, notés par Stockfish à TRUTH_DEPTH (une fois pour toutes)."""
    import multiprocessing

    games = [g for g in json.loads(POSITIONS.read_text()) if g["plies"]]
    with multiprocessing.get_context("spawn").Pool(workers) as pool:
        table = {k: v for part in pool.map(truth_game, games, chunksize=1) for k, v in part.items()}
    TRUTH.write_text(json.dumps(table))
    print(len(table), "positions notées à profondeur", TRUTH_DEPTH)


def load(label: str) -> dict:
    return {(r["game"], r["ply"]): r for r in map(json.loads, open(OUT / f"{label}.jsonl"))}


def quality(rows: list[dict]) -> tuple[float, float, float]:
    """Perte attendue (cp, bornée à 1000 par coup), perte du coup tiré, part de coups tirés perdant ≥ 200 cp."""
    table = json.loads(TRUTH.read_text())
    expected, drawn, blunders = [], [], 0
    for r in rows:
        scores = table[f"{r['game']}:{r['ply']}"]
        best = max(scores.values())
        loss = lambda m: min(1000, best - scores.get(m, best - 1000))
        expected.append(sum(p * loss(m) for m, p in r["candidates"].items()))
        drawn.append(loss(r["move"]))
        blunders += loss(r["move"]) >= 200
    return statistics.mean(expected), statistics.mean(drawn), blunders / len(rows)


def table(base: str, labels: list[str]) -> None:
    B = load(base)
    print(f"{'réglage':22} {'s/coup':>6} {'SF calc':>7} {'Maia':>5} {'perte att.':>10} {'perte tirée':>11} {'≥200cp':>6} "
          f"{'même coup':>9} {'écart':>6}")
    for label in [base, *labels]:
        R = load(label)
        keys = [k for k in B if k in R]
        rows = [R[k] for k in keys]
        mean = lambda f: statistics.mean(r.get(f, 0) for r in rows)
        same = sum(B[k]["move"] == R[k]["move"] for k in keys) / len(keys)
        tv = statistics.mean(0.5 * sum(abs(B[k]["candidates"].get(m, 0) - R[k]["candidates"].get(m, 0))
                                       for m in set(B[k]["candidates"]) | set(R[k]["candidates"])) for k in keys)
        exp, drawn, blunder = quality(rows)
        print(f"{label:22} {mean('seconds'):6.2f} {mean('sf_new'):7.0f} {mean('maia_boards'):5.0f} {exp:10.0f} {drawn:11.0f} "
              f"{blunder:6.1%} {same:9.1%} {tv:6.3f}")


def golden() -> None:
    """Régénère tests/data/golden.json : 12 positions réparties sur les parties, effort complet et réduit."""
    sys.path.insert(0, str(ROOT / "tests"))
    from fun_fish.engines import find_stockfish
    from regression import GOLDEN, decide

    games = [g for g in json.loads(POSITIONS.read_text()) if len(g["plies"]) >= 6]
    cases = []
    for i, g in enumerate(games[:6]):
        for j, effort in ((len(g["plies"]) // 3, 0), (2 * len(g["plies"]) // 3, 4 if i % 2 else 0)):
            ply = g["plies"][j]
            cases.append({"game": g["id"], "ply": ply, "moves": g["moves"][:ply], "effort": effort,
                          "seed": zlib.crc32(f"{g['id']}:{ply}".encode())})
    stockfish = str(find_stockfish(None))
    for case in cases:
        case.update(decide(case, stockfish))
        print(case["game"], case["ply"], "effort", case["effort"], "→", case["move"], flush=True)
    GOLDEN.write_text(json.dumps(cases, indent=1))


def compare(a: str, b: str) -> None:
    def load(label):
        return {(r["game"], r["ply"]): r for r in map(json.loads, open(OUT / f"{label}.jsonl"))}

    A, B = load(a), load(b)
    keys = [k for k in A if k in B]
    same = sum(A[k]["move"] == B[k]["move"] for k in keys)
    argmax = sum(max(A[k]["candidates"], key=A[k]["candidates"].get) == max(B[k]["candidates"], key=B[k]["candidates"].get)
                 for k in keys)
    tv = [0.5 * sum(abs(A[k]["candidates"].get(m, 0) - B[k]["candidates"].get(m, 0))
                    for m in set(A[k]["candidates"]) | set(B[k]["candidates"])) for k in keys]
    print(f"{len(keys)} positions : même coup tiré {same / len(keys):.1%}, même favori {argmax / len(keys):.1%}, "
          f"écart de distribution moyen {statistics.mean(tv):.3f} (max {max(tv):.2f})")
    for label, R in ((a, A), (b, B)):
        rows = [R[k] for k in keys]
        s = [r["seconds"] for r in rows]
        mean = lambda f: statistics.mean(r.get(f, 0) for r in rows)
        print(f"  {label:20} {statistics.mean(s):.2f} s/coup (médiane {statistics.median(s):.2f}, max {max(s):.1f}) ; "
              f"Stockfish {mean('sf'):.0f} appels dont {mean('sf_new'):.0f} calculés, {mean('sf_s'):.2f} s ; "
              f"Maia {mean('maia_boards'):.0f} positions en {mean('maia_calls'):.0f} lots, {mean('maia_s'):.2f} s")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("positions")
    r = sub.add_parser("run")
    r.add_argument("label")
    r.add_argument("-p", "--personality", default="fun_fish_2")
    r.add_argument("-s", "--set", action="append", default=[])
    r.add_argument("--limit", type=int, help="positions par partie au plus")
    r.add_argument("--hash", type=int, default=64)
    r.add_argument("-w", "--workers", type=int, default=7)
    t = sub.add_parser("truth")
    t.add_argument("-w", "--workers", type=int, default=14)
    tb = sub.add_parser("table")
    tb.add_argument("base")
    tb.add_argument("labels", nargs="*")
    sub.add_parser("golden")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    args = ap.parse_args()
    if args.cmd == "positions":
        build_positions()
    elif args.cmd == "run":
        run(args.label, dict(kv.split("=", 1) for kv in args.set), args.personality, args.limit, args.hash, args.workers)
    elif args.cmd == "truth":
        build_truth(args.workers)
    elif args.cmd == "golden":
        golden()
    elif args.cmd == "table":
        table(args.base, args.labels)
    else:
        compare(args.a, args.b)


if __name__ == "__main__":
    sys.exit(main())
