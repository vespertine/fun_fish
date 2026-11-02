"""Livre d'ouverture humain : ce que jouent vraiment les joueurs de chaque niveau.

Maia-2 n'a pas été entraîné sur les 10 premiers demi-coups des parties (`first_n_moves: 10`
dans sa configuration) : en ouverture, ses probabilités ne valent rien (1.Cc3 Cf6 2.Cb1 à
83 %). Ce livre prend le relais : pour chaque position des `PLIES` premiers demi-coups, le
nombre de fois où chaque coup a été joué sur Lichess, par tranche d'Elo du joueur au trait.

Source : la base publique des parties notées de Lichess (https://database.lichess.org,
CC0), parties blitz, rapides et lentes. `fun-fish book-build` le reconstruit.
"""

from __future__ import annotations

import gzip
import json
import re
import threading
from collections import Counter, defaultdict
from pathlib import Path

import chess

PLIES = 10  # demi-coups couverts
BUCKETS = (0, 1000, 1200, 1400, 1600, 1800, 2000, 2200)  # bornes basses des tranches d'Elo
MIN_GAMES = 20  # en deçà, une position n'est pas assez jouée à ce niveau
MIN_MOVE = 2  # en deçà, un coup est oublié (bruit, gaffes de souris)
DEFAULT_PATH = Path(__file__).parent / "data" / "book.json.gz"
SOURCE = "https://database.lichess.org/standard/lichess_db_standard_rated_{month}.pgn.zst"


def bucket(elo: int) -> int:
    """Indice de la tranche d'Elo."""
    return max(i for i, low in enumerate(BUCKETS) if elo >= low)


def key(board: chess.Board) -> str:
    return board.epd()  # pièces, trait, roques, prise en passant (si jouable) : les transpositions se rejoignent


class Book:
    def __init__(self, data: dict[str, dict[str, dict[str, int]]]):
        self.data = data  # epd -> tranche -> coup uci -> nombre de parties

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "Book":
        with gzip.open(path, "rt") as f:
            return cls(json.load(f)["positions"])

    def probabilities(self, board: chess.Board, elo: int) -> dict[chess.Move, float] | None:
        """Fréquence de chaque coup chez les joueurs de ce niveau, ou None si la position est
        hors du livre. Si la tranche manque de parties, on y ajoute les tranches voisines."""
        if board.ply() > PLIES:
            return None
        entry = self.data.get(key(board))
        if not entry:
            return None
        home = bucket(elo)
        counts: Counter[str] = Counter()
        for distance in range(len(BUCKETS)):
            for b in {home - distance, home + distance}:
                counts.update(entry.get(str(b), {}))
            if sum(counts.values()) >= MIN_GAMES:
                break
        total = sum(counts.values())
        if total < MIN_GAMES:
            return None
        legal = {m.uci(): m for m in board.legal_moves}
        result = {legal[u]: n / total for u, n in counts.items() if u in legal}
        return result or None


_shared: Book | None = None
_lock = threading.Lock()


def shared() -> Book | None:
    """Le livre fourni avec fun_fish (None s'il manque)."""
    global _shared
    with _lock:
        if _shared is None and DEFAULT_PATH.exists():
            _shared = Book.load()
        return _shared


# ---------- construction ----------

_TAG = re.compile(r'^\[(\w+) "(.*)"\]$')
_NOISE = re.compile(r"\{[^}]*\}|\d+\.(\.\.)?|[?!]+")


def _speed_ok(time_control: str) -> bool:
    """Blitz, rapide ou lente (pas de bullet, pas de partie par correspondance)."""
    if "+" not in time_control:
        return False
    base, inc = time_control.split("+")
    return int(base) + 40 * int(inc) >= 180


def _games(lines):
    """(Elo des Blancs, Elo des Noirs, coups SAN) pour chaque partie retenue."""
    tags: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = _TAG.match(line)
        if m:
            tags[m.group(1)] = m.group(2)
            continue
        try:
            if _speed_ok(tags.get("TimeControl", "-")):  # la base ne contient que des parties notées
                sans = _NOISE.sub(" ", line).split()[:PLIES]
                yield int(tags["WhiteElo"]), int(tags["BlackElo"]), sans
        except (KeyError, ValueError):
            pass
        tags = {}


def _count(batch: list[tuple[int, int, list[str]]]) -> dict:
    counts: dict[str, dict[int, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for white, black, sans in batch:
        board = chess.Board()
        for san in sans:
            if san in ("1-0", "0-1", "1/2-1/2", "*"):
                break
            try:
                move = board.parse_san(san)
            except ValueError:
                break
            elo = white if board.turn == chess.WHITE else black
            counts[key(board)][bucket(elo)][move.uci()] += 1
            board.push(move)
    return {k: {b: dict(c) for b, c in v.items()} for k, v in counts.items()}


def build(month: str, games: int, out: Path, workers: int = 8, log=print) -> None:
    """Lit `games` parties de la base Lichess du mois (AAAA-MM), en flux, et écrit le livre."""
    import io
    import urllib.request
    from multiprocessing import Pool

    import pyzstd  # dépendance de maia2 (extra `sim`)

    url = SOURCE.format(month=month)
    request = urllib.request.Request(url, headers={"User-Agent": "fun_fish book-build"})
    total: dict[str, dict[int, Counter]] = defaultdict(lambda: defaultdict(Counter))
    seen = 0

    def batches(text):
        nonlocal seen
        batch = []
        for game in _games(text):
            batch.append(game)
            seen += 1
            if len(batch) >= 5000 or seen >= games:
                yield batch
                batch = []
                if seen >= games:
                    return
        if batch:
            yield batch

    with urllib.request.urlopen(request) as response, Pool(workers) as pool:
        text = io.TextIOWrapper(pyzstd.ZstdFile(response), encoding="utf-8", errors="replace")
        for i, part in enumerate(pool.imap_unordered(_count, batches(text))):
            for k, by_bucket in part.items():
                for b, moves in by_bucket.items():
                    total[k][b].update(moves)
            if i % 40 == 39:
                log(f"  {(i + 1) * 5000} parties comptées, {len(total)} positions")
    positions = {}
    for k, by_bucket in total.items():
        kept = {}
        for b, moves in by_bucket.items():
            moves = {u: n for u, n in moves.items() if n >= MIN_MOVE}
            if sum(moves.values()) >= MIN_GAMES // 2:  # les tranches voisines peuvent compléter
                kept[str(b)] = moves
        if kept:
            positions[k] = kept
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = {"source": url, "games": seen, "plies": PLIES, "buckets": BUCKETS, "min_move": MIN_MOVE}
    with gzip.open(out, "wt", compresslevel=9) as f:
        json.dump({"meta": meta, "positions": positions}, f, separators=(",", ":"))
    log(f"{len(positions)} positions, {out.stat().st_size / 1e6:.1f} Mo → {out}")
