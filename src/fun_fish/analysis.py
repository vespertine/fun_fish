"""Accès à Stockfish : analyses MultiPV mises en cache."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.engine

MATE_SCORE = 100_000
_CACHE_LIMIT = 50_000


@dataclass(frozen=True)
class Line:
    """Une ligne proposée par le moteur, du point de vue du camp au trait."""

    move: chess.Move
    score: int  # centipions ; un mat vaut ±(MATE_SCORE - distance)
    mate: int | None  # coups jusqu'au mat (>0 : on mate, <0 : on est maté)
    wdl: tuple[int, int, int] | None  # gain / nulle / perte, pour mille
    pv: tuple[chess.Move, ...]
    depth: int


class Analyser:
    """Enveloppe autour d'un processus Stockfish. Thread-safe, avec cache par position."""

    def __init__(self, engine_path: str | os.PathLike, *, threads: int | None = None, hash_mb: int = 128):
        self._engine = chess.engine.SimpleEngine.popen_uci(str(Path(engine_path)))
        wanted = {
            "Threads": threads or max(1, min(4, (os.cpu_count() or 2) // 2)),
            "Hash": hash_mb,
            "UCI_ShowWDL": True,
        }
        self._engine.configure({k: v for k, v in wanted.items() if k in self._engine.options})
        self.engine_name: str = self._engine.id.get("name", "?")
        self._lock = threading.Lock()
        self._cache: dict[tuple, list[Line]] = {}

    def analyse(self, board: chess.Board, *, multipv: int = 1, depth: int | None = None,
                nodes: int | None = None, time: float | None = None,
                moves: Iterable[chess.Move] | None = None) -> list[Line]:
        """Renvoie jusqu'à `multipv` lignes, triées de la meilleure à la pire pour le camp au trait.

        `moves` restreint la recherche à ces coups (searchmoves de l'UCI).

        Le cache est indexé par position (EPD) et par les positions déjà vues depuis le dernier
        coup irréversible : Stockfish reçoit l'historique et compte les répétitions comme nulles,
        donc une même position n'a pas la même valeur selon ce qui s'est joué avant.
        """
        if board.is_game_over():
            return []
        if depth is None and nodes is None and time is None:
            depth = 12
        root_moves = sorted(moves, key=lambda m: m.uci()) if moves is not None else None
        key = (board.epd(), _seen_before(board), multipv, depth, nodes, time,
               tuple(root_moves) if root_moves is not None else None)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            infos = self._engine.analyse(
                board,
                chess.engine.Limit(depth=depth, nodes=nodes, time=time),
                multipv=multipv,
                info=chess.engine.INFO_ALL,
                root_moves=root_moves,
            )
            lines = [line for info in infos if (line := _to_line(info)) is not None]
            lines.sort(key=lambda line: line.score, reverse=True)
            if len(self._cache) >= _CACHE_LIMIT:
                self._cache.clear()
            self._cache[key] = lines
            return lines

    def close(self) -> None:
        with self._lock:
            self._engine.quit()

    def __enter__(self) -> "Analyser":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _seen_before(board: chess.Board) -> tuple[str, ...]:
    """Positions déjà vues (depuis le dernier coup irréversible) que la suite pourrait répéter."""
    plies = min(board.halfmove_clock, len(board.move_stack))
    if plies < 4:  # pas de répétition possible en moins de 4 demi-coups
        return ()
    b = board.copy()
    seen = set()
    for _ in range(plies):
        b.pop()
        seen.add(b.epd())
    return tuple(sorted(seen))


def _to_line(info: chess.engine.InfoDict) -> Line | None:
    pv = info.get("pv")
    if not pv or "score" not in info:
        return None
    pov = info["score"].relative
    wdl_info = info.get("wdl")
    wdl = None
    if wdl_info is not None:
        rel = wdl_info.relative
        wdl = (rel.wins, rel.draws, rel.losses)
    return Line(
        move=pv[0],
        score=pov.score(mate_score=MATE_SCORE),
        mate=pov.mate(),
        wdl=wdl,
        pv=tuple(pv),
        depth=info.get("depth", 0),
    )
