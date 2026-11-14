"""Construit les arbres d'ouvertures des maîtres-modèles (DuBotV2, CapaBotV2) à partir des PGN de runs/styles.

Pour chaque position des `PLIES` premiers demi-coups : les coups joués par le maître-modèle lui-même (jamais
par son adversaire), avec leur nombre de parties. On ne garde que les positions où il a joué au moins
`MIN_GAMES` fois. Sortie : src/fun_fish/data/books/<nom>.json.gz, {"players": [...], "positions": {epd: {uci: n}}}.

    python scripts/model_books.py
"""

from __future__ import annotations

import gzip
import json
from collections import defaultdict
from pathlib import Path

import chess.pgn

ROOT = Path(__file__).resolve().parents[1]
STYLES = ROOT / "runs" / "styles"
OUT = ROOT / "src" / "fun_fish" / "data" / "books"
PLIES, MIN_GAMES = 20, 3
BOOKS = {
    "attaque": ["pgn/Tal", "pgn/Dubov", "pgn/Polgar", "pgn/Alekhine", "pgn/Shirov", "pgn/Bronstein", "pgn/Jobava",
                "pgn/Morozevich", "pgn/Rapport", "pgn/Mamedyarov", "pgn/Marshall", "pgn/Nezhmetdinov", "pgn/Velimirovic"],
    "solide": ["pgn_contre/Capablanca", "pgn_contre/Karpov", "pgn_contre/Petrosian", "pgn_contre/Kramnik",
               "pgn_contre/Andersson", "pgn_test/Smyslov", "pgn_test/Carlsen"],
}


def build(files: list[str]) -> dict:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for name in files:
        player = name.split("/")[-1]
        with open(STYLES / f"{name}.pgn", encoding="utf-8", errors="replace") as f:
            while (game := chess.pgn.read_game(f)) is not None:
                white, black = game.headers.get("White", ""), game.headers.get("Black", "")
                if (player in white) == (player in black):
                    continue  # joueur introuvable (ou des deux côtés) : partie ignorée
                ours = chess.WHITE if player in white else chess.BLACK
                board = game.board()
                if board.fen() != chess.STARTING_FEN:
                    continue
                for ply, move in enumerate(game.mainline_moves()):
                    if ply >= PLIES:
                        break
                    if board.turn == ours:
                        counts[board.epd()][move.uci()] += 1
                    board.push(move)
    return {epd: dict(moves) for epd, moves in counts.items() if sum(moves.values()) >= MIN_GAMES}


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for book, files in BOOKS.items():
        positions = build(files)
        with gzip.open(OUT / f"{book}.json.gz", "wt", encoding="utf-8") as f:
            json.dump({"players": [n.split("/")[-1] for n in files], "plies": PLIES, "min_games": MIN_GAMES,
                       "positions": positions}, f, separators=(",", ":"))
        print(book, len(positions), "positions")
