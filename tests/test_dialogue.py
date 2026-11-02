import chess

from fun_fish.analysis import Line
from fun_fish.personalities.attacker import Attacker, CandidateEval, Outcome
from fun_fish.personalities.romantic import Romantic
from fun_fish.style import center, threats


def play(*sans):
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board


def test_style_cannot_overturn_a_clear_attack():
    a = Attacker(style_margin=1.0)
    m1, m2 = chess.Move.from_uci("e2e4"), chess.Move.from_uci("d2d4")
    attack = CandidateEval(Line(m1, 50, None, None, (m1,), 10), 0, Outcome(win=0.4, value=500))
    quiet = CandidateEval(Line(m2, 40, None, None, (m2,), 10), 10, Outcome(value=40))
    for e in (attack, quiet):
        a.scored(e, attack.line)
    scores = a.combine([attack, quiet], {m1: -1.0, m2: 2.5})
    assert scores[0] > scores[1]
    flat = a.combine([quiet, quiet], {m2: 2.5})
    assert flat[0] == quiet.score + 2.5  # sans idée d'attaque, le style parle à plein


def test_center_and_threats():
    assert center(play("e4"), chess.WHITE) > center(play("Nf3"), chess.WHITE)
    board = chess.Board("rnb1kbnr/pppp1ppp/8/4p1q1/4P3/2N5/PPPP1PPP/R1BQKBNR w KQkq - 0 1")
    after = board.copy()
    after.push_san("Nf3")  # le cavalier attaque la dame
    assert threats(after, chess.WHITE) >= 3
    assert threats(board, chess.WHITE) == 0


def test_mood_is_drawn_once_per_game():
    r = Romantic(mood=0.35)
    first = r.style(chess.Board())
    later = r.style(play("e4", "e5", "Nf3"))
    assert first == later  # même partie, même humeur
    other = r.style(chess.Board())  # nouvelle partie
    assert other != first
    assert Romantic(mood=0).style(chess.Board()).w_develop == 0.6


def test_repertoire_is_an_occasional_wish():
    import random

    from fun_fish.repertoire import REPERTOIRES, Planner
    for openings in REPERTOIRES.values():
        for color, lines in openings.items():
            for o in lines:
                assert len(o.moves) % 2 == (1 if color == chess.WHITE else 0), o.name
    always = Planner(REPERTOIRES["romantic"], random.Random(1), rate=1.0)
    assert always.next(chess.Board()) is not None
    move, name = always.next(play("e4", "c5"))
    assert move == chess.Move.from_uci("d2d4") and "Morra" in name
    assert always.next(play("e4", "c5", "d4", "e6")) is None  # sorti de toutes les lignes
    sometimes = Planner(REPERTOIRES["romantic"], random.Random(2), rate=0.2)
    wished = sum(sometimes.next(chess.Board()) is not None for _ in range(500))
    assert 60 < wished < 140


class FakeAnalyser:
    """Scores fixes par coup ; respecte multipv et la restriction `moves`."""

    def __init__(self, scores):
        self.scores = scores

    def analyse(self, board, multipv=1, depth=None, moves=None, **_):
        pool = [m for m in board.legal_moves if moves is None or m in moves]
        lines = [Line(m, self.scores.get(m.uci(), -500), None, None, (m,), depth or 0) for m in pool]
        return sorted(lines, key=lambda l: -l.score)[:multipv]


def test_vision_hides_moves_a_human_would_not_play():
    from fun_fish.personalities.base import Soundness
    board = chess.Board()
    fake = FakeAnalyser({"g1h3": 300, "e2e4": 30, "d2d4": 25, "c2c4": 10})
    s = Soundness(tolerance=50, depth=5, max_candidates=12, convert_from=0)
    assert [l.move.uci() for l in s.lines(board, fake)[0]] == ["g1h3"]  # tout voir : seul Ch3 est sain
    seen = {chess.Move.from_uci(u): p for u, p in {"e2e4": 0.6, "d2d4": 0.3, "c2c4": 0.09, "g1h3": 0.01}.items()}
    lines, _ = s.lines(board, fake, seen)
    assert [l.move.uci() for l in lines] == ["e2e4", "d2d4", "c2c4"]  # Ch3 n'est pas vu
    assert s.truth.move.uci() == "g1h3"


def test_widen_only_for_a_spark_or_an_emergency():
    from fun_fish.personalities.attacker import Attacker, CandidateEval, Outcome, Thought
    board = chess.Board()
    mv = lambda u: chess.Move.from_uci(u)  # noqa: E731
    line = lambda u, s: Line(mv(u), s, None, None, (mv(u),), 5)  # noqa: E731
    ev = lambda u, s, win: CandidateEval(line(u, s), 0, Outcome(win=win))  # noqa: E731
    a = Attacker(w_eval=0.0)
    full = Thought([ev("e2e4", 30, 0.0), ev("g1h3", 20, 0.9), ev("a2a3", 25, 0.1)], {}, line("e2e4", 30), 0.0, None)
    a.think = lambda *args: full

    calm = Thought([ev("e2e4", 30, 0.0)], {}, line("e2e4", 30), 0.0, line("e2e4", 30))
    widened, why = a.widen(board, None, calm, None, None)
    assert [e.line.move.uci() for e in widened.evals] == ["e2e4", "g1h3"]  # la fulgurance, pas le coup d'attente
    assert "plus loin" in why

    excited = Thought([ev("e2e4", 30, 0.8)], {}, line("e2e4", 30), 0.0, line("e2e4", 30))
    assert a.widen(board, None, excited, None, None)[0] is excited  # ce qu'il voit lui plaît

    burning = Thought([ev("e2e4", -300, 0.8)], {}, line("e2e4", -300), 0.0, line("d2d4", 0))
    assert a.widen(board, None, burning, None, None)[0] is full  # ça brûle : tout ce qui sauve
