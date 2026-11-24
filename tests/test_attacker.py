import random

import chess
import pytest

from fun_fish import personalities
from fun_fish.personalities import attacker
from fun_fish.analysis import Line
from fun_fish.personalities.attacker import RESCUE, Attacker, CandidateEval, Outcome, settled_material


def test_registered_with_params():
    a = personalities.create("attacker", tolerance=200, w_mate=5)
    assert a.tolerance == 200 and a.w_mate == 5.0


def test_settled_material_finishes_the_exchange():
    # Les Blancs prennent le cavalier e5, les Noirs reprennent : bilan nul après l'échange.
    board = chess.Board("4k3/8/3p4/4n3/8/8/8/4RK2 w - - 0 1")
    pv = tuple(chess.Move.from_uci(u) for u in ("e1e5", "d6e5"))
    assert settled_material(board, pv, chess.WHITE) == 5 - 1 - 5 + 0  # tour contre cavalier : -2


def test_plays_a_legal_move_with_fake_engine(fake_analyser):
    board = chess.Board()
    decision = Attacker(rng=random.Random(0), deep_candidates=2).choose_move(board, fake_analyser)
    assert board.is_legal(decision.move) and decision.candidates and decision.reality


def test_goes_for_scholars_mate_against_naive_opponent():
    """Après 1.e4 e5 2.Fc4 Cc6, Dh5 menace Dxf7# : sur Lichess, sous 1000 Elo, Cf6?? 9 % du temps."""
    pytest.importorskip("maia2")
    from fun_fish.analysis import Analyser
    from fun_fish.engines import find_stockfish

    with Analyser(find_stockfish(download=False), threads=1, hash_mb=16) as analyser:  # neuf : l'équilibre testé
        check_scholars_mate(analyser)  # est fin, il ne doit pas dépendre des tests précédents


def check_scholars_mate(stockfish_analyser):
    board = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 2 3")
    attacker = Attacker(rng=random.Random(0), temperature=0.05, opponent_elo=900)
    evals = {}
    root = stockfish_analyser.analyse(board, multipv=12, depth=10)
    for line in root:
        if board.san(line.move) in ("Qh5", "Nf3"):
            evals[board.san(line.move)] = attacker.scored(
                attacker.evaluate(board, line, root[0], stockfish_analyser, moves=2), root[0])
    assert evals["Qh5"].outcome.mate > 0.05 and "Qxf7#" in evals["Qh5"].outcome.best[1]
    assert evals["Nf3"].outcome.mate == 0
    # 9 % de mat ne paient pas le demi-pion que coûte Dh5... sauf pour qui tient vraiment à mater.
    assert evals["Qh5"].score < evals["Nf3"].score
    attacker.w_mate = 10
    assert attacker.scored(evals["Qh5"], root[0]).score > attacker.scored(evals["Nf3"], root[0]).score


def test_maia_model_mentions_elo(stockfish_analyser):
    pytest.importorskip("maia2")
    board = chess.Board("rn1qkbnr/ppp2p1p/3p2p1/4p3/2B1P1b1/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 0 5")
    decision = Attacker(rng=random.Random(0), opponent_elo=1100, temperature=0.05).choose_move(board, stockfish_analyser)
    assert decision.san == "Nxe5"
    assert "1100 Elo" in decision.explanation


def test_panache_also_lets_style_speak_when_material_is_all_the_attack_sees():
    """Gagnant, avec du panache, un gain de matériel ne doit plus faire taire le style (via l'occasion)."""
    from fun_fish.analysis import Line
    from fun_fish.personalities.attacker import CandidateEval, Outcome

    def candidate(uci, win):
        line = Line(move=chess.Move.from_uci(uci), score=800, mate=None, wdl=None, pv=(), depth=6)
        return CandidateEval(line, loss=0, outcome=Outcome(win=win, value=800))

    for cls in ("attacker", "romantic"):
        a = personalities.create(cls, panache=1, w_eval=0)
        a._degree, a._luxury = 1.0, 0
        greedy, stylish = candidate("e2e4", 1.0), candidate("d2d4", 0.0)
        evals = [a.scored(greedy, greedy.line), a.scored(stylish, stylish.line)]
        scores = a.combine(evals, {greedy.line.move: 0.5, stylish.line.move: 1.0})
        assert scores[1] > scores[0], cls  # le plus stylé l'emporte : le gain ne compte plus


def test_short_mate_is_always_played_and_prettily(stockfish_analyser):
    """Mat court : un mat en 1 est toujours joué (plus d'échecs sans fin) ; entre deux mats en 1, le goût
    pour les jolis mats préfère le cavalier à la dame."""
    from fun_fish.personalities.attacker import pretty
    board = chess.Board("6k1/8/6K1/8/8/8/8/7Q w - - 0 1")  # Qh7# ou Qa8# ; les autres échecs font durer
    for seed in range(6):
        agent = personalities.create("crazy_fish", rng=random.Random(seed))
        assert agent.choose_move(board.copy(), stockfish_analyser).san.endswith("#")
    # mat étouffé au cavalier contre mat de la dame (deux mats en 1 construits à la main)
    from fun_fish.analysis import Line
    two = chess.Board("6rk/6pp/8/6N1/8/8/8/1Q4K1 w - - 0 1")
    mate = lambda uci: Line(chess.Move.from_uci(uci), 99999, 1, None, (), 8)  # noqa: E731
    assert pretty(two, mate("g5f7")) > pretty(two, mate("b1h7"))



QXE5 = "1q4k1/p1r3p1/7p/4p3/4Q3/5P2/PP3B2/1K4R1 w - - 8 37"  # 0DwIicXu : 37.Dxe5?? Tc1+


class _Revérifie:
    """Analyseur de test : `scores` donne la vraie éval de chaque coup analysé seul ; une recherche sur
    plusieurs coups rend le meilleur d'entre eux, les coups absents de `scores` valant `others`."""

    def __init__(self, scores, others=716):
        self.scores, self.others, self.searches = scores, others, []

    def analyse(self, board, *, multipv=1, depth=None, nodes=None, time=None, moves=None):
        moves = list(moves or board.legal_moves)
        self.searches.append(moves)
        best = max(moves, key=lambda m: (self.scores.get(m, self.others), m.uci()))
        return [Line(best, self.scores.get(best, self.others), None, None, (best,), depth or 6)]


def _eval(board, san, listed):
    move = board.parse_san(san)
    return CandidateEval(Line(move, listed, None, None, (move,), 6), 0, Outcome())


def _verified(fen, evals, scores, others=716):
    agent = personalities.create("capabot_v2", rng=random.Random(0))
    board = chess.Board(fen)
    analyser = _Revérifie({board.parse_san(k): v for k, v in scores.items()}, others)
    result = agent.verified(board, analyser, evals[0], evals, [1.0 - 0.1 * i for i in range(len(evals))],
                            evals[0].line)
    return agent, board, analyser, result


def test_garde_fou_se_replie_hors_liste_quand_le_coup_refuse_est_seul_candidat():
    # 37.Dxe5?? (0DwIicXu) : seul candidat en conversion, refusé par le garde-fou mais joué faute d'autre coup
    board = chess.Board(QXE5)
    agent, board, analyser, result = _verified(QXE5, [_eval(board, "Qxe5", 795)], {"Qxe5": -417, "Be3": 716}, 0)
    assert result.rescue and board.san(result.line.move) == "Be3" and result.outcome.value == 716  # note juste
    assert agent.anomalies[-1] == {"fen": QXE5, "move": "e4e5", "listed": 795, "checked": -417, "fallback": "f2e3"}
    assert len(analyser.searches[-1]) == board.legal_moves.count() - 1  # tous les autres coups légaux


def test_garde_fou_garde_le_coup_si_le_repli_n_est_pas_meilleur():
    board = chess.Board(QXE5)
    for others in (-900, -417):  # pire, puis égal
        chosen = _eval(board, "Qxe5", 795)
        *_, result = _verified(QXE5, [chosen], {"Qxe5": -417}, others)
        assert result is chosen


def test_garde_fou_rend_le_moins_mauvais_des_refuses():
    # Dxe5 refusé (−417), puis Fe3 aussi (−600) : un repli à −500 ne vaut pas Dxe5
    board = chess.Board(QXE5)
    first, second = _eval(board, "Qxe5", 795), _eval(board, "Be3", 780)
    agent, board, analyser, result = _verified(QXE5, [first, second], {"Qxe5": -417, "Be3": -600}, -500)
    assert result is first
    assert all(board.parse_san(s) not in analyser.searches[-1] for s in ("Qxe5", "Be3"))


def test_garde_fou_laisse_passer_un_coup_encore_gagnant():
    # MnwvVVL5 : finale de tour contre deux pions, 63.Th8+ noté « mat en 8 » mais revérifié à +11,62
    fen = "4k3/8/7R/5p1p/5K1P/8/8/8 w - - 6 63"
    board = chess.Board(fen)
    for checked, kept in ((1162, True), (attacker.GUARD_WON, True), (attacker.GUARD_WON - 1, False)):
        move = board.parse_san("Rh8+")
        chosen = CandidateEval(Line(move, 99992, 8, None, (move,), 6), 0, Outcome())
        agent, board, _, result = _verified(fen, [chosen, _eval(board, "Kxf5", 1400)], {"Rh8+": checked, "Kxf5": 1400})
        assert (result is chosen) == kept and bool(agent.anomalies) != kept


def test_garde_fou_ne_joue_jamais_un_quatrieme_candidat_sans_le_verifier():
    # trois refus : le repli prend le meilleur des autres coups revérifiés, pas le 4e candidat (−9) à l'aveugle
    board = chess.Board(QXE5)
    evals = [_eval(board, san, 795) for san in ("Qxe5", "Be3", "Rh1", "Rg2")]
    agent, board, analyser, result = _verified(QXE5, evals, {"Qxe5": -417, "Be3": -500, "Rh1": -600, "Rg2": -900}, -100)
    assert result.rescue and result.line.score == -100 and board.san(result.line.move) not in ("Qxe5", "Be3", "Rh1", "Rg2")
    assert len(agent.anomalies) == 3


def test_garde_fou_sans_autre_coup_legal_garde_le_coup():
    fen = "7k/8/8/8/8/8/6q1/7K w - - 0 1"  # seul coup : Rxg2
    board = chess.Board(fen)
    chosen = _eval(board, "Kxg2", 900)
    *_, result = _verified(fen, [chosen], {"Kxg2": 0})
    assert result is chosen


def test_le_repli_figure_dans_la_decision(stockfish_analyser, monkeypatch):
    board = chess.Board(QXE5)
    agent = personalities.create("capabot_v2", rng=random.Random(0))
    move = board.parse_san("Qh7+")  # hors de toute liste : un repli n'y est jamais (tous ses coups sont refusés)
    rescue = CandidateEval(Line(move, 716, None, None, (move,), 6), 0, Outcome(value=716), rescue=True)
    monkeypatch.setattr(agent, "verified", lambda *a: rescue)
    decision = agent.choose_move(board, stockfish_analyser)
    assert decision.move == move and decision.explanation.endswith(RESCUE.format("Qh7+"))
    assert "garde-fou" in decision.reality.lower()
    assert [c.probability for c in decision.candidates if c.move == move] == [1.0]
    assert sum(c.probability for c in decision.candidates) == 1.0
    assert "éval +7.2" in decision.candidates[0].note


def test_garde_fou_d_un_membre_de_conseil_se_replie_parmi_les_coups_du_conseil():
    board = chess.Board(QXE5)
    agent = personalities.create("capabot_v2", rng=random.Random(0))
    agent.restrict = [board.parse_san(s) for s in ("Qxe5", "Rd1", "Ka1")]
    analyser = _Revérifie({board.parse_san("Qxe5"): -417, board.parse_san("Be3"): 716}, 0)
    chosen = _eval(board, "Qxe5", 795)
    result = agent.verified(board, analyser, chosen, [chosen], [1.0], chosen.line)
    assert analyser.searches[-1] == agent.restrict[1:]
    assert result.rescue and board.san(result.line.move) in ("Rd1", "Ka1")
