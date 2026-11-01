"""Séries de parties en parallèle, résultats en JSONL (une partie par ligne)."""

from __future__ import annotations

import json
import multiprocessing
import os
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import chess

_worker: dict = {}


@dataclass(frozen=True)
class Task:
    personality: str
    params: dict
    elo: int
    seed: int
    agent_white: bool


def _init_worker(stockfish: str, maia_kind: str) -> None:
    from fun_fish.analysis import Analyser
    from fun_fish.sim.maia import Maia

    _worker["analyser"] = Analyser(stockfish, threads=1, hash_mb=64)
    _worker["maia"] = Maia(kind=maia_kind, threads=1)


def _run(task: Task) -> dict:
    from fun_fish import personalities
    from fun_fish.sim.maia import MaiaPlayer
    from fun_fish.sim.match import play_game

    rng = random.Random(task.seed)
    agent = personalities.create(task.personality, rng=random.Random(rng.random()), **task.params)
    opponent = MaiaPlayer(_worker["maia"], task.elo, rng=random.Random(rng.random()))
    record = play_game(agent, opponent, task.elo, chess.WHITE if task.agent_white else chess.BLACK,
                       _worker["analyser"], seed=task.seed, params=task.params)
    return record.to_dict()


def _run_safe(task: Task) -> dict:
    try:
        return _run(task)
    except Exception as exc:  # une partie qui plante ne doit pas tuer la série
        return {"error": repr(exc), "task": asdict(task)}


def run(personality: str, params: dict, elos: list[int], games: int, out: Path, *, stockfish: str,
        workers: int | None = None, seed: int = 0, maia_kind: str = "rapid") -> None:
    """`games` parties par tranche d'Elo, l'agent alternant les couleurs. Reprend un fichier existant."""
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            done.add((r["opponent_elo"], r["seed"]))
    tasks = [Task(personality, params, elo, seed * 1_000_000 + elo * 1000 + i, i % 2 == 0)
             for elo in elos for i in range(games)]
    tasks = [t for t in tasks if (t.elo, t.seed) not in done]
    if not tasks:
        print("Rien à faire : toutes les parties sont déjà dans le fichier.")
        return
    workers = min(len(tasks), workers or max(1, (os.cpu_count() or 2) - 2))
    print(f"{len(tasks)} parties à jouer ({len(done)} déjà faites), {workers} processus → {out}", flush=True)
    start = time.time()
    pool = multiprocessing.Pool(workers, initializer=_init_worker, initargs=(stockfish, maia_kind))
    try:
        with open(out, "a") as f:
            for n, record in enumerate(pool.imap_unordered(_run_safe, tasks), 1):
                if "error" in record:
                    print(f"  partie en erreur : {record['error']}", file=sys.stderr, flush=True)
                    continue
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                eta = (time.time() - start) / n * (len(tasks) - n)
                print(f"  [{n}/{len(tasks)}] {record['opponent']:<10} {record['agent_color']:<5} "
                      f"{record['result']:<7} {record['termination']:<20} {record['plies']:>3} demi-coups "
                      f"(reste ~{eta / 60:.0f} min)", flush=True)
    finally:
        # Chaque processus garde son Stockfish ouvert, ce qui l'empêcherait de s'arrêter seul.
        pool.terminate()
        pool.join()
