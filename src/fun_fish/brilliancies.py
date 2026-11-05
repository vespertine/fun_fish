"""Coups célèbres : est-ce qu'une personnalité les voit, et les joue ?

Chaque partie est donnée par ses coups (SAN) ; on vise certains de ses coups (« 10.Nxb5 »,
« 17...Be6 »). Pour chacun, on rejoue la position avec son historique, on tire plusieurs fois
le choix de la personnalité et on regarde : le coup est-il parmi ses candidats, avec quelle
probabilité, combien de fois est-il joué, et ce qu'il vaut pour Stockfish.

On peut ajouter ses propres parties dans un fichier JSON : une liste d'objets
{"name": ..., "moves": "e4 e5 Nf3 ...", "targets": ["10.Nxb5", "17...Be6"]}.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import chess

GAMES = [
    {"name": "Morphy, partie de l'Opéra (1858)",
     "moves": "e4 e5 Nf3 d6 d4 Bg4 dxe5 Bxf3 Qxf3 dxe5 Bc4 Nf6 Qb3 Qe7 Nc3 c6 Bg5 b5 Nxb5 cxb5 Bxb5+ Nbd7 "
              "O-O-O Rd8 Rxd7 Rxd7 Rd1 Qe6 Bxd7+ Nxd7 Qb8+ Nxb8 Rd8#",
     "targets": ["10.Nxb5", "13.Rxd7", "16.Qb8+"]},
    {"name": "Anderssen, l'Immortelle (1851)",
     "moves": "e4 e5 f4 exf4 Bc4 Qh4+ Kf1 b5 Bxb5 Nf6 Nf3 Qh6 d3 Nh5 Nh4 Qg5 Nf5 c6 g4 Nf6 Rg1 cxb5 h4 Qg6 "
              "h5 Qg5 Qf3 Ng8 Bxf4 Qf6 Nc3 Bc5 Nd5 Qxb2 Bd6 Bxg1 e5 Qxa1+ Ke2 Na6 Nxg7+ Kd8 Qf6+ Nxf6 Be7#",
     "targets": ["17.Nd5", "18.Bd6", "22.Qf6+"]},
    {"name": "Anderssen, l'Éternelle (1852)",
     "moves": "e4 e5 Nf3 Nc6 Bc4 Bc5 b4 Bxb4 c3 Ba5 d4 exd4 O-O d3 Qb3 Qf6 e5 Qg6 Re1 Nge7 Ba3 b5 Qxb5 Rb8 "
              "Qa4 Bb6 Nbd2 Bb7 Ne4 Qf5 Bxd3 Qh5 Nf6+ gxf6 exf6 Rg8 Rad1 Qxf3 Rxe7+ Nxe7 Qxd7+ Kxd7 Bf5+ Ke8 "
              "Bd7+ Kf8 Bxe7#",
     "targets": ["17.Nf6+", "19.Rad1", "20.Rxe7+", "21.Qxd7+"]},
    {"name": "Byrne-Fischer, la partie du siècle (1956)",
     "moves": "Nf3 Nf6 c4 g6 Nc3 Bg7 d4 O-O Bf4 d5 Qb3 dxc4 Qxc4 c6 e4 Nbd7 Rd1 Nb6 Qc5 Bg4 Bg5 Na4 Qa3 Nxc3 "
              "bxc3 Nxe4 Bxe7 Qb6 Bc4 Nxc3 Bc5 Rfe8+ Kf1 Be6",
     "targets": ["11...Na4", "13...Nxe4", "17...Be6"]},
    {"name": "Giri-Carlsen, sacrifice de dame (2021)",
     "moves": "e4 c5 Nf3 Nc6 Bb5 e6 O-O Nge7 d4 cxd4 Nxd4 Qb6 Be3 Nxd4 a4 Nxb5 Bxb6 axb6 Nd2 Nd6 e5 Ndf5 Ne4 "
              "Nc6 g4 Nh4 f4 h5 h3 hxg4 hxg4 d5 exd6 Ra5",
     "targets": ["8...Nxb5", "16...d5", "17...Ra5"]},
    {"name": "Réti-Tartakower (1910)",
     "moves": "e4 c6 d4 d5 Nc3 dxe4 Nxe4 Nf6 Qd3 e5 dxe5 Qa5+ Bd2 Qxe5 O-O-O Nxe4 Qd8+ Kxd8 Bg5+ Kc7 Bd8#",
     "targets": ["9.Qd8+"]},
]


@dataclass
class Probe:
    game: str
    target: str
    board: chess.Board
    move: chess.Move


def probes(games: list[dict]) -> list[Probe]:
    out = []
    for game in games:
        sans = game["moves"].split()
        for target in game["targets"]:
            number, san = target.split(".", 1)
            black = san.startswith("..")
            san = san.lstrip(".")
            ply = 2 * (int(number) - 1) + (1 if black else 0)
            board = chess.Board()
            for s in sans[:ply]:
                board.push_san(s)
            move = board.parse_san(san)
            if sans[ply] != san:
                raise ValueError(f"{game['name']} : {target} ne correspond pas au coup joué ({sans[ply]})")
            out.append(Probe(game["name"], target, board, move))
    return out


def load(path: str | None) -> list[dict]:
    return json.loads(Path(path).read_text()) if path else GAMES


def run(personality_factory, analyser, games: list[dict], draws: int, seed: int = 0, depth: int = 18) -> list[dict]:
    """Pour chaque coup visé : joué combien de fois, candidat ou non, et sa valeur pour Stockfish."""
    rows = []
    for i, probe in enumerate(probes(games)):
        personality = personality_factory(random.Random(seed + i))
        picks, candidate, probability = Counter(), False, 0.0
        for _ in range(draws):
            decision = personality.choose_move(probe.board.copy(), analyser)
            picks[decision.san] += 1
            for c in decision.candidates:
                if c.move == probe.move:
                    candidate = True
                    probability = max(probability, c.probability or 0.0)
        best = analyser.analyse(probe.board, multipv=1, depth=depth)[0]
        mine = analyser.analyse(probe.board, multipv=1, depth=depth, moves=[probe.move])[0]
        rows.append({"game": probe.game, "target": probe.target, "played": picks[probe.board.san(probe.move)],
                     "draws": draws, "candidate": candidate, "probability": probability,
                     "loss": best.score - mine.score if best.mate is None and mine.mate is None else None,
                     "best": probe.board.san(best.move), "picks": dict(picks.most_common(3))})
    return rows
