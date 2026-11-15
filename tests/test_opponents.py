import gzip
import json
import random

import pytest

from fun_fish import opponents
from fun_fish.opponents import GameRecord, History

DAY = 86400


def rec(game_id, result, ended_at, opponent="alice", account="dubot", **kw):
    base = dict(title=None, bot=False, rating=1500, speed="blitz", rated=False, color="white", status="resign",
                plies=40)
    return GameRecord(game_id, account, opponent, ended_at=ended_at, result=result, **{**base, **kw})


def test_history_counts_one_account_and_ignores_aborted_games():
    for i, result in enumerate(["win", "win", "loss", "draw", "aborted"]):
        opponents.record(rec(f"g{i}", result, 1000 + i))
    opponents.record(rec("autre", "loss", 5000, account="capabot"))
    opponents.record(rec("g0", "win", 1000))  # importé deux fois : compté une fois
    assert opponents.history("DuBot", "Alice") == History(4, 2, 1, 1, 1003)
    assert opponents.history("dubot", "bob") == History()


@pytest.mark.parametrize("past, gap, kind", [
    (History(), 0, None),
    (History(1, 1, 0, 0, 0), 600, "revanche"),
    (History(3, 1, 0, 2, 0), DAY, "retour"),
    (History(12, 6, 1, 5, 0), DAY, "habitué"),
    (History(12, 6, 1, 5, 0), 20 * DAY, "retrouvailles"),
    (History(1, 0, 0, 1, 0), 20 * DAY, "retrouvailles"),
])
def test_the_welcome_depends_on_the_absence_and_the_number_of_games(past, gap, kind):
    assert opponents.welcome_kind(past, gap) == kind


def test_the_welcome_recalls_the_score_from_the_player_side():
    fr, en = opponents.welcome(History(4, 1, 1, 2, 0), now=DAY, rng=random.Random(0))
    assert fr.endswith("Le compteur : toi 2, moi 1, 1 nulle.")
    assert en.endswith("Score so far: you 2, me 1, 1 draw.")
    fr, en = opponents.welcome(History(5, 2, 3, 0, 0), now=DAY, rng=random.Random(0))
    assert fr.endswith("toi 0, moi 2, 3 nulles.") and en.endswith("you 0, me 2, 3 draws.")
    assert opponents.welcome(History(), now=DAY) is None


def test_every_welcome_fits_in_a_lichess_chat_line():
    worst = History(999, 999, 999, 999, 0)
    for kind, texts in opponents.WELCOMES.items():
        for i in range(len(texts)):
            rng = type("R", (), {"choice": staticmethod(lambda xs, i=i: xs[i])})()
            now = {"revanche": 1, "retour": DAY, "habitué": DAY, "retrouvailles": 30 * DAY}[kind]
            assert all(len(t) <= 140 for t in opponents.welcome(worst, now=now, rng=rng))


def _move(ply, by, scores=(), chosen=None):
    """Un coup d'export : `scores` du point de vue des Blancs, le premier est choisi sauf mention."""
    cands = [{"score": s, "chosen": i == (chosen or 0)} for i, s in enumerate(scores)]
    return {"ply": ply, "by": by, **({"decision": {"candidates": cands}} if scores else {})}


def test_losses_come_from_our_own_evals():
    # Blancs : on joue un coup à 30 quand le meilleur vaut 50 ; l'adversaire ramène l'éval à -70 (perte 100).
    moves = [_move(0, "ai", (50, 30), chosen=1), _move(1, "human"), _move(2, "ai", (-70, -90)), _move(3, "human"),
             _move(4, "ai", (2000, 0))]  # gain de l'adversaire impossible : bornée à 0 ; éval plafonnée à 1000
    assert opponents.losses(moves, "white") == (pytest.approx(50.0), pytest.approx(20 / 3, abs=0.1))
    # Noirs : les évals sont retournées.
    black = [_move(1, "ai", (-50, -30)), _move(2, "human"), _move(3, "ai", (100,))]
    assert opponents.losses(black, "black") == (150.0, 0.0)
    assert opponents.losses([_move(0, "human")], "white") == (None, None)


def test_import_merges_lichess_games_with_our_exports(tmp_path):
    export = {"lichess": {"id": "g1"}, "moves": [_move(0, "ai", (50,)), _move(1, "human"), _move(2, "ai", (-50,))]}
    with gzip.open(tmp_path / "2026-11-14-g1.json.gz", "wt") as f:
        json.dump(export, f)
    games = {"dubot": [
        {"id": "g1", "variant": "standard", "speed": "blitz", "rated": False, "status": "mate", "winner": "black",
         "players": {"white": {"user": {"id": "dubot", "title": "BOT"}}, "black": {"user": {"id": "Fishy", "title": "BOT"}, "rating": 2900}},
         "moves": "e4 e5 Qh5", "createdAt": 1_000_000, "lastMoveAt": 2_000_000},
        {"id": "g2", "variant": "standard", "speed": "rapid", "rated": True, "status": "draw",
         "players": {"white": {"aiLevel": 5}, "black": {"user": {"id": "dubot"}}}, "createdAt": 3_000_000},
        {"id": "g3", "variant": "chess960", "players": {"white": {"user": {"id": "dubot"}}, "black": {}}, "createdAt": 0},
    ]}
    assert opponents.import_history(["DuBot"], tmp_path, fetch=lambda a: games[a.lower()]) == 2
    rows = opponents.report(bots=True)
    assert [r[:4] for r in sorted(rows)] == [("ai-level-5", None, 1, 50), ("fishy", "BOT", 1, 0)]
    fishy = next(r for r in rows if r[0] == "fishy")
    assert fishy[4] == 100.0 and fishy[6] == 3  # perte de l'adversaire (50 → -50), 3 demi-coups
