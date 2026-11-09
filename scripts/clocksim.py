"""Simule des parties à la pendule (contre Stockfish instantané) pour vérifier la gestion du temps.

    python scripts/clocksim.py 180 0 [parties]     # 3+0
"""
import collections, random, sys, time, chess
from fun_fish import personalities
from fun_fish.analysis import Analyser
from fun_fish.engines import find_stockfish
from fun_fish.game import Game
from fun_fish.pace import OVERHEAD, Pace

base, inc = float(sys.argv[1]), float(sys.argv[2])
games = int(sys.argv[3]) if len(sys.argv) > 3 else 2
with Analyser(find_stockfish(None), threads=1, hash_mb=64) as an:
    for seed in range(games):
        agent = personalities.create("fun_fish_2_theorie", rng=random.Random(seed))
        color = chess.WHITE if seed % 2 == 0 else chess.BLACK
        g, pace, clock, efforts, low = Game(agent, human_color=not color), Pace(), base, collections.Counter(), base
        while not g.over and len(g.board.move_stack) < 160 and clock > 0:
            if g.board.turn == color:
                e = pace.effort(clock, inc, g.board.fullmove_number)
                agent.effort = e
                t = time.monotonic(); g.play_engine(an); dt = time.monotonic() - t
                pace.observe(dt, e)
                clock += inc - dt - OVERHEAD
                efforts[e] += 1; low = min(low, clock)
            else:
                g.board.push(an.analyse(g.board, multipv=1, depth=4)[0].move)
        print(f"{base:.0f}+{inc:.0f} partie {seed}: {len(g.board.move_stack)} demi-coups, {g.board.result(claim_draw=True)}, "
              f"pendule finale {clock:.0f} s (min {low:.0f}), efforts {dict(sorted(efforts.items()))}, réflexion complète estimée {pace.full:.1f} s", flush=True)
