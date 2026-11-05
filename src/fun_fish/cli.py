"""Point d'entrée `fun-fish`."""

from __future__ import annotations

import argparse
import random
import socket
import sys
import threading
import webbrowser
from collections import Counter
from pathlib import Path

import chess

from fun_fish import __version__, personalities
from fun_fish.analysis import Analyser
from fun_fish.engines import StockfishNotFound, find_stockfish
from fun_fish.personalities.base import format_score


def _free_port(host: str, preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return sock.getsockname()[1]
    raise RuntimeError("Aucun port disponible.")


def cmd_play(args: argparse.Namespace) -> None:
    import uvicorn

    from fun_fish.server import create_app

    port = _free_port(args.host, args.port)
    url = f"http://{args.host}:{port}/"

    def on_startup() -> None:
        print(f"fun_fish est prêt sur {url} (Ctrl+C pour quitter)", flush=True)
        if not args.no_browser:
            threading.Timer(0.8, webbrowser.open, args=(url,)).start()

    with Analyser(find_stockfish(args.stockfish), threads=args.threads) as analyser:
        print(f"Moteur : {analyser.engine_name}", flush=True)
        uvicorn.run(create_app(analyser, on_startup=on_startup), host=args.host, port=port, log_level="warning")


def parse_params(pairs: list[str]) -> dict:
    """["tolerance=150", "w_sharp=3.5"] -> {"tolerance": 150, "w_sharp": 3.5}"""
    params = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep:
            raise SystemExit(f"Réglage mal formé : {pair!r} (attendu : nom=valeur)")
        for cast in (int, float):
            try:
                params[key] = cast(raw)
                break
            except ValueError:
                continue
        else:
            params[key] = raw
    return params


def cmd_choose(args: argparse.Namespace) -> None:
    """Montre ce que choisirait une personnalité dans une position donnée."""
    board = chess.Board(args.fen)
    rng = random.Random(args.seed)
    try:
        personality = personalities.create(args.personality, rng=rng, **parse_params(args.set))
    except ValueError as exc:
        raise SystemExit(f"Erreur : {exc}") from None
    with Analyser(find_stockfish(args.stockfish), threads=args.threads) as analyser:
        decision = personality.choose_move(board, analyser)
        print_decision(decision)
        if args.repeat > 1:
            counts = Counter(personality.choose_move(board, analyser).san for _ in range(args.repeat))
            print(f"\nSur {args.repeat} tirages : " + ", ".join(f"{san} ×{n}" for san, n in counts.most_common()))


def cmd_brilliancies(args: argparse.Namespace) -> None:
    """Coups célèbres : les voit-il, les joue-t-il ?"""
    from fun_fish import brilliancies

    try:
        params = parse_params(args.set)
        personalities.create(args.personality, **params)
    except ValueError as exc:
        raise SystemExit(f"Erreur : {exc}") from None
    factory = lambda rng: personalities.create(args.personality, rng=rng, **params)  # noqa: E731
    with Analyser(find_stockfish(args.stockfish), threads=args.threads) as analyser:
        rows = brilliancies.run(factory, analyser, brilliancies.load(args.file), args.repeat)
    seen = played = 0
    for r in rows:
        loss = "  mat" if r["loss"] is None else f"{r['loss'] / 100:+5.1f}"
        print(f"{r['target']:<10} {'vu' if r['candidate'] else '--':<3} {r['probability']:4.0%}  joué {r['played']}/{r['draws']}"
              f"  perte {loss} (meilleur {r['best']:<6})  {r['game']}  · ses choix : {r['picks']}")
        seen += r["candidate"]
        played += r["played"] > 0
    print(f"\n{seen}/{len(rows)} coups vus, {played}/{len(rows)} joués au moins une fois")


def print_decision(decision) -> None:
    print(decision.explanation)
    if decision.reality:
        print(decision.reality)
    for cand in decision.to_dict()["candidates"]:
        proba = "  -  " if cand["probability"] is None else f"{cand['probability']:5.0%}"
        score = f"#{cand['mate']}" if cand["mate"] is not None else f"{cand['score'] / 100:+.2f}"
        mark = "→" if cand["chosen"] else " "
        print(f" {mark} {cand['san']:<8} {score:>7}  {proba}  {cand['note']}")


def cmd_replay(args: argparse.Namespace) -> None:
    """Rejoue la réflexion de l'IA à un coup d'un export de debug (bouton « Export debug »)."""
    import json
    import time

    data = json.loads(Path(args.file).read_text())
    ai_moves = [m for m in data["moves"] if m["by"] == "ai"]
    if args.list or not ai_moves:
        for m in ai_moves:
            board = chess.Board(m["fen_before"])
            print(f"  ply {m['ply']:>3}  {board.fullmove_number}{'.' if board.turn else '...'} {m['san']:<7} "
                  f"{m['personality']:<9} {m['seconds']:>5.1f}s  {m['params']}")
        return
    entry = next((m for m in ai_moves if m["ply"] == args.ply), None) if args.ply is not None else ai_moves[-1]
    if entry is None:
        raise SystemExit(f"Pas de coup de l'IA au demi-coup {args.ply} (--list pour la liste).")
    board = chess.Board(data["start_fen"])
    for m in data["moves"][: entry["ply"]]:  # avec l'historique : les répétitions comptent
        board.push_uci(m["uci"])
    params = {**entry["params"], **parse_params(args.set)}
    try:
        personality = personalities.create(entry["personality"], rng=random.Random(args.seed), **params)
    except ValueError as exc:
        raise SystemExit(f"Erreur : {exc}") from None
    print(f"Demi-coup {entry['ply']} : {board.fen()}")
    print(f"À l'époque : {entry['san']} ({entry['seconds']} s), {entry['personality']} {entry['params']}\n")
    with Analyser(find_stockfish(args.stockfish), threads=args.threads) as analyser:
        start = time.monotonic()
        decision = personality.choose_move(board, analyser)
        print(f"Maintenant ({time.monotonic() - start:.1f} s) :")
        print_decision(decision)


def cmd_read(args: argparse.Namespace) -> None:
    """Affiche la lecture humaine des meilleures lignes, approfondie et court-termiste."""
    from fun_fish.reading import read_lines

    board = chess.Board(args.fen)
    with Analyser(find_stockfish(args.stockfish), threads=args.threads) as analyser:
        lines = analyser.analyse(board, multipv=args.multipv, depth=args.depth)
    deep = read_lines(board, lines)
    short = read_lines(board, lines, horizon=args.horizon)
    side = "Blancs" if board.turn == chess.WHITE else "Noirs"
    print(f"Trait aux {side}, profondeur {args.depth}. Lecture complète | à {args.horizon} demi-coups\n")
    for d, s, line in zip(deep, short, lines):
        pv = board.variation_san(list(line.pv[:args.show_pv]))
        print(f"  {d.san:<7} {format_score(d.score, d.mate):>6}  {d.summary:<44} | {s.summary}")
        print(f"  {'':<7} {'':>6}  {pv}")


def parse_elos(spec: str) -> list[int]:
    """« 1100-1900:200 » -> [1100, 1300, 1500, 1700, 1900] ; « 1000,1500 » -> [1000, 1500]."""
    elos: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            span, _, step = part.partition(":")
            lo, hi = (int(x) for x in span.split("-"))
            elos += list(range(lo, hi + 1, int(step or 100)))
        else:
            elos.append(int(part))
    return elos


def cmd_sim(args: argparse.Namespace) -> None:
    from pathlib import Path

    from fun_fish.sim import report, runner

    params = parse_params(args.set)
    try:
        personalities.create(args.personality, **runner.resolve_params(params, 1500))  # valide les réglages avant de lancer les processus
    except ValueError as exc:
        raise SystemExit(f"Erreur : {exc}") from None
    out = Path(args.out)
    runner.run(args.personality, params, parse_elos(args.elos), args.games, out,
               stockfish=str(find_stockfish(args.stockfish)), workers=args.workers, seed=args.seed)
    print()
    print(report.summarize(report.load(out)))


def cmd_sim_report(args: argparse.Namespace) -> None:
    from pathlib import Path

    from fun_fish.sim import report

    for path in args.files:
        print(report.summarize(report.load(Path(path))))
        print()


def cmd_maia_export(args: argparse.Namespace) -> None:
    from fun_fish.sim import maia

    if not maia.torch_ready():
        raise SystemExit("L'export demande PyTorch et maia2 : make sim-install (voir README).")
    model, moves = maia.export_onnx()
    print(f"Modèle ONNX : {model} ({model.stat().st_size // 1_000_000} Mo)\nCoups : {moves}")


def cmd_book_build(args: argparse.Namespace) -> None:
    from fun_fish import book

    book.build(args.month, args.games, Path(args.out), workers=args.workers)


def cmd_stockfish(args: argparse.Namespace) -> None:
    print(find_stockfish(args.stockfish))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fun-fish", description="Jouer contre des personnalités d'échecs.")
    parser.add_argument("--version", action="version", version=f"fun_fish {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--stockfish", help="chemin du binaire Stockfish (sinon détection ou téléchargement)")
    common.add_argument("--threads", type=int, help="threads alloués à Stockfish")
    sub = parser.add_subparsers(dest="command")

    play = sub.add_parser("play", parents=[common], help="ouvrir l'échiquier (commande par défaut)")
    play.add_argument("--host", default="127.0.0.1")
    play.add_argument("--port", type=int, default=8765)
    play.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur")
    play.set_defaults(func=cmd_play)

    choose = sub.add_parser("choose", parents=[common], help="afficher le choix d'une personnalité dans une position")
    choose.add_argument("fen", nargs="?", default=chess.STARTING_FEN)
    choose.add_argument("-p", "--personality", default=personalities.DEFAULT_PERSONALITY,
                        choices=list(personalities.PERSONALITIES))
    choose.add_argument("--seed", type=int)
    choose.add_argument("-s", "--set", action="append", default=[], metavar="PARAM=VALEUR",
                        help="surcharge un réglage de la personnalité (répétable)")
    choose.add_argument("-n", "--repeat", type=int, default=1, help="nombre de tirages pour voir la distribution")
    choose.set_defaults(func=cmd_choose)

    bril = sub.add_parser("brilliancies", parents=[common], help="coups célèbres : les voit-il, les joue-t-il ?")
    bril.add_argument("-p", "--personality", default="romantic", choices=list(personalities.PERSONALITIES))
    bril.add_argument("-s", "--set", action="append", default=[], metavar="PARAM=VALEUR")
    bril.add_argument("-n", "--repeat", type=int, default=4, help="tirages par position")
    bril.add_argument("--file", help="parties à tester (JSON : name, moves, targets), à la place des parties fournies")
    bril.set_defaults(func=cmd_brilliancies)

    read = sub.add_parser("read", parents=[common], help="lecture humaine des meilleures lignes d'une position")
    read.add_argument("fen", nargs="?", default=chess.STARTING_FEN)
    read.add_argument("-d", "--depth", type=int, default=16)
    read.add_argument("-m", "--multipv", type=int, default=6)
    read.add_argument("--horizon", type=int, default=2, help="demi-coups de la lecture court-termiste")
    read.add_argument("--show-pv", type=int, default=8, help="demi-coups de variation affichés")
    read.set_defaults(func=cmd_read)

    sim = sub.add_parser("sim", parents=[common], help="faire jouer une personnalité contre Maia par tranche d'Elo")
    sim.add_argument("-p", "--personality", default=personalities.DEFAULT_PERSONALITY,
                     choices=list(personalities.PERSONALITIES))
    sim.add_argument("-s", "--set", action="append", default=[], metavar="PARAM=VALEUR")
    sim.add_argument("--elos", default="1100-1900:200", help="tranches : « 1100-1900:200 » ou « 1000,1500,2000 »")
    sim.add_argument("-g", "--games", type=int, default=10, help="parties par tranche (couleurs alternées)")
    sim.add_argument("-w", "--workers", type=int, help="processus en parallèle (défaut : cœurs - 2)")
    sim.add_argument("-o", "--out", required=True, help="fichier JSONL de résultats (repris s'il existe)")
    sim.add_argument("--seed", type=int, default=0)
    sim.set_defaults(func=cmd_sim)

    sim_report = sub.add_parser("sim-report", help="synthèse de fichiers de résultats")
    sim_report.add_argument("files", nargs="+")
    sim_report.set_defaults(func=cmd_sim_report)

    replay = sub.add_parser("replay", parents=[common], help="rejouer la réflexion de l'IA depuis un export de debug")
    replay.add_argument("file", help="fichier JSON du bouton « Export debug »")
    replay.add_argument("--ply", type=int, help="demi-coup à rejouer (par défaut : le dernier coup de l'IA)")
    replay.add_argument("--list", action="store_true", help="lister les coups de l'IA avec leurs réglages")
    replay.add_argument("-s", "--set", action="append", default=[], metavar="PARAM=VALEUR", help="surcharger un réglage")
    replay.add_argument("--seed", type=int, help="graine du tirage au sort")
    replay.set_defaults(func=cmd_replay)
    export = sub.add_parser("maia-export", help="exporter Maia-2 en ONNX (pour s'en servir sans PyTorch)")
    export.set_defaults(func=cmd_maia_export)
    book_build = sub.add_parser("book-build", help="reconstruire le livre d'ouverture humain (base Lichess, extra sim)")
    book_build.add_argument("--month", default="2025-09", help="mois de la base Lichess (AAAA-MM)")
    book_build.add_argument("--games", type=int, default=2_000_000, help="parties lues")
    book_build.add_argument("--workers", type=int, default=8)
    book_build.add_argument("-o", "--out", default=str(Path(__file__).parent / "data" / "book.json.gz"))
    book_build.set_defaults(func=cmd_book_build)
    stockfish = sub.add_parser("stockfish", parents=[common], help="afficher (et télécharger si besoin) Stockfish")
    stockfish.set_defaults(func=cmd_stockfish)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version"):
        argv = ["play", *argv]
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except StockfishNotFound as exc:
        sys.exit(f"Erreur : {exc}")
    except KeyboardInterrupt:
        pass
