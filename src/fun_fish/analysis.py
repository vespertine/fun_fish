"""Accès à Stockfish : analyses MultiPV mises en cache."""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import chess

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


class _Uci:
    """Client UCI minimal : n'envoie que ce dont on a besoin et ne décode que la dernière ligne de
    chaque variante. python-chess décodait (et validait coup par coup) toutes les lignes « info »
    intermédiaires : c'était plus long que la recherche elle-même à faible profondeur. Les commandes
    envoyées sont celles de python-chess (MultiPV quand il change, ucinewgame une fois), donc
    Stockfish calcule exactement la même chose."""

    def __init__(self, path: str, options: dict[str, object]):
        self.proc = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.name, known = "?", set()
        self.send("uci")
        for line in self.lines("uciok"):
            if line.startswith("id name "):
                self.name = line[8:].strip()
            elif line.startswith("option name "):
                known.add(line[12:].split(" type ")[0])
        for name, value in options.items():
            if name in known:
                self.send(f"setoption name {name} value {str(value).lower() if isinstance(value, bool) else value}")
        self.send("ucinewgame")
        self.send("isready")
        for _ in self.lines("readyok"):
            pass
        self.multipv = None

    def send(self, line: str) -> None:
        self.proc.stdin.write(line + "\n")

    def lines(self, until: str):
        for line in self.proc.stdout:
            if line.startswith(until):
                return
            yield line
        raise EOFError("Stockfish s'est arrêté")

    def analyse(self, board: chess.Board, multipv: int, depth: int | None, nodes: int | None, time: float | None,
                root_moves: list[chess.Move] | None) -> list[Line]:
        if multipv != self.multipv:
            self.send(f"setoption name MultiPV value {multipv}")
            self.multipv = multipv
        fen = board.root().fen(en_passant="fen")  # comme python-chess
        position = "startpos" if fen == chess.STARTING_FEN else f"fen {fen}"
        if board.move_stack:
            position += " moves " + " ".join(m.uci() for m in board.move_stack)
        go = ["go"]
        if depth is not None:
            go += ["depth", str(max(1, int(depth)))]
        if nodes is not None:
            go += ["nodes", str(max(1, int(nodes)))]
        if time is not None:
            go += ["movetime", str(max(1, round(time * 1000)))]
        if root_moves is not None:
            go += ["searchmoves", *([m.uci() for m in root_moves] or ["0000"])]
        self.send("position " + position)
        self.send(" ".join(go))
        last: dict[int, str] = {}
        for line in self.lines("bestmove"):
            if line.startswith("info ") and " pv " in line:
                k = line.find(" multipv ")
                last[int(line[k + 9:].split(None, 1)[0]) if k >= 0 else 1] = line
        return [line for _, raw in sorted(last.items()) if (line := _parse(raw)) is not None]

    def quit(self) -> None:
        try:
            self.send("quit")
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()


def _parse(raw: str) -> Line | None:
    """Une ligne « info ... score ... pv ... » de Stockfish (point de vue du camp au trait)."""
    tokens = raw.split()
    depth, score, mate, wdl, pv = 0, None, None, None, ()
    i = 1
    while i < len(tokens):
        t = tokens[i]
        if t == "depth":
            depth = int(tokens[i + 1])
            i += 2
        elif t == "score":
            kind, value = tokens[i + 1], int(tokens[i + 2])
            if kind == "cp":
                score, mate = value, None
            else:  # comme chess.engine.Mate.score(mate_score=MATE_SCORE)
                mate = value
                score = MATE_SCORE - value if value > 0 else -MATE_SCORE - value
            i += 3
        elif t == "wdl":
            wdl = (int(tokens[i + 1]), int(tokens[i + 2]), int(tokens[i + 3]))
            i += 4
        elif t == "pv":
            pv = tuple(chess.Move.from_uci(m) for m in tokens[i + 1:])
            break
        else:
            i += 1
    if not pv or score is None:
        return None
    return Line(move=pv[0], score=score, mate=mate, wdl=wdl, pv=pv, depth=depth)


class Analyser:
    """Enveloppe autour d'un processus Stockfish. Thread-safe, avec cache par position."""

    def __init__(self, engine_path: str | os.PathLike, *, threads: int | None = None, hash_mb: int = 128):
        self._engine = _Uci(str(Path(engine_path)), {
            "Threads": threads or max(1, min(4, (os.cpu_count() or 2) // 2)),
            "Hash": hash_mb,
            "UCI_ShowWDL": True,
        })
        self.engine_name: str = self._engine.name
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
            lines = self._engine.analyse(board, multipv, depth, nodes, time, root_moves)
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
