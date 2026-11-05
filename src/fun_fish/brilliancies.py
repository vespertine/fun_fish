"""Coups célèbres : est-ce qu'une personnalité les voit, et les joue ?

Chaque partie est donnée par ses coups (SAN) ; on vise certains de ses coups (« 10.Nxb5 »,
« 17...Be6 »). Pour chacun, on rejoue la position avec son historique, on tire plusieurs fois
le choix de la personnalité et on regarde : le coup est-il parmi ses candidats, avec quelle
probabilité, combien de fois est-il joué, et ce qu'il vaut pour Stockfish.

On peut ajouter ses propres parties dans un fichier JSON : une liste d'objets
{"name": ..., "moves": "e4 e5 Nf3 ...", "targets": ["10.Nxb5", "17...Be6"]} ; ou un PGN annoté
(par exemple une étude Lichess : https://lichess.org/api/study/<id>.pgn), dont les coups notés
! ou !! sont visés.
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
    {"name": "Tal-Hecht, Varna (1962)",
     "moves": "d4 Nf6 c4 e6 Nf3 b6 Nc3 Bb4 Bg5 Bb7 e3 h6 Bh4 Bxc3+ bxc3 d6 Nd2 e5 f3 Qe7 e4 Nbd7 Bd3 Nf8 c5 dxc5 "
              "dxe5 Qxe5 Qa4+ c6 O-O Ng6 Nc4 Qe6 e5 b5 exf6 bxa4 fxg7 Rg8 Bf5",
     "targets": ["13.c5", "17.Nc4", "19.exf6", "21.Bf5"]},
    {"name": "Botvinnik-Tal, championnat du monde (1960)",
     "moves": "c4 Nf6 Nf3 g6 g3 Bg7 Bg2 O-O d4 d6 Nc3 Nbd7 O-O e5 e4 c6 h3 Qb6 d5 cxd5 cxd5 Nc5 Ne1 Bd7 Nd3 Nxd3 "
              "Qxd3 Rfc8 Rb1 Nh5 Be3 Qb4 Qe2 Rc4 Rfc1 Rac8 Kh2 f5 exf5 Bxf5 Ra1 Nf4",
     "targets": ["21...Nf4"]},
    {"name": "Tal-Smyslov, Bled (1959)",
     "moves": "e4 c6 d3 d5 Nd2 e5 Ngf3 Nd7 d4 dxe4 Nxe4 exd4 Qxd4 Ngf6 Bg5 Be7 O-O-O O-O Nd6 Qa5 Bc4 b5 Bd2 Qa6 "
              "Nf5 Bd8 Qh4 bxc4 Qg5 Nh5 Nh6+ Kh8 Qxh5",
     "targets": ["12.Bd2", "14.Qh4"]},
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
    """Les parties fournies, un fichier JSON, ou un PGN (coups visés : ceux notés ! ou !!)."""
    if not path:
        return GAMES
    if path.endswith(".pgn"):
        return from_pgn(Path(path).read_text())
    return json.loads(Path(path).read_text())


def from_pgn(text: str, marks: tuple[int, ...] = (1, 3)) -> list[dict]:
    """Chaque partie d'un PGN, avec pour coups visés ceux que l'annotateur a notés ! (1) ou !! (3)."""
    import io

    import chess.pgn

    games, stream = [], io.StringIO(text)
    while (game := chess.pgn.read_game(stream)) is not None:
        sans, targets = [], []
        for node in game.mainline():
            board = node.parent.board()
            sans.append(node.san())
            if any(n in marks for n in node.nags):
                targets.append(f"{board.fullmove_number}{'.' if board.turn else '...'}{node.san()}")
        if targets:
            h = game.headers
            games.append({"name": f"{h.get('White', '?')}-{h.get('Black', '?')} ({h.get('Date', '?')[:4]})",
                          "moves": " ".join(sans), "targets": targets})
    return games


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
