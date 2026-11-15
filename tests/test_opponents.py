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


def test_the_welcome_recalls_a_balanced_score_from_the_player_side():
    fr, en = opponents.welcome(History(4, 1, 1, 2, 0), now=DAY, rng=random.Random(0))
    assert fr.endswith("Le compteur : toi 2, moi 1, 1 nulle.")
    assert en.endswith("Score so far: you 2, me 1, 1 draw.")
    fr, en = opponents.welcome(History(5, 2, 3, 0, 0), now=DAY, rng=random.Random(0))
    assert fr.endswith("toi 0, moi 2, 3 nulles.") and en.endswith("you 0, me 2, 3 draws.")
    assert opponents.welcome(History(), now=DAY) is None


@pytest.mark.parametrize("past", [History(18, 18, 0, 0, 0), History(4, 0, 0, 4, 0), History(1, 1, 0, 0, 0),
                                  History(9, 7, 0, 2, 0)])
def test_a_lopsided_score_is_not_recalled(past):
    fr, en = opponents.welcome(past, now=DAY, rng=random.Random(0))
    assert "compteur" not in fr and "Score" not in en


def test_milestones_are_celebrated():
    fr, en = opponents.welcome(History(19, 19, 0, 0, 0), now=DAY, rng=random.Random(0))
    assert fr.endswith("C'est notre 20e partie ensemble !") and en.endswith("This is our 20th game together!")
    fr, en = opponents.welcome(History(49, 25, 0, 24, 0), now=DAY, rng=random.Random(0))
    assert "notre 50e partie ensemble ! Le compteur : toi 24, moi 25." in fr
    assert [n for n in range(1, 401) if opponents.milestone(n)] == [10, 20, 50, 100, 200, 300, 400]
    assert "partie ensemble" not in opponents.welcome(History(20, 10, 0, 10, 0), now=DAY)[0]


@pytest.mark.parametrize("past", [History(899, 299, 300, 300, 0), History(49, 19, 10, 20, 0), History(9999, 3333, 3333, 3333, 0),
                                  History(999, 999, 0, 0, 0)])
def test_every_welcome_fits_in_a_lichess_chat_line(past):
    for kind, texts in opponents.WELCOMES.items():
        now = {"revanche": 1, "retour": DAY, "habitué": DAY, "retrouvailles": 30 * DAY}[kind]
        for i in range(len(texts)):
            rng = type("R", (), {"choice": staticmethod(lambda xs, i=i: xs[i])})()
            assert all(len(t) <= 140 for t in opponents.welcome(past, now=now, rng=rng))


def test_a_greeting_variant_always_keeps_room_for_the_cap_and_the_score():
    """Les variantes tiennent avec le cap et un compteur à trois chiffres : rien n'est coupé en pratique."""
    for kind, texts in opponents.WELCOMES.items():
        now = {"revanche": 1, "retour": DAY, "habitué": DAY, "retrouvailles": 30 * DAY}[kind]
        for i in range(len(texts)):
            rng = type("R", (), {"choice": staticmethod(lambda xs, i=i: xs[i])})()
            fr, en = opponents.welcome(History(899, 299, 300, 300, 0), now=now, rng=rng)
            assert "900e" in fr and "toi 300" in fr and "900th" in en and "you 300" in en, (fr, en)


def test_too_long_a_welcome_drops_the_score_first(monkeypatch):
    first = type("R", (), {"choice": staticmethod(lambda xs: xs[0])})()
    monkeypatch.setattr(opponents, "CHAT_MAX", 100)  # 57 + 34 (le cap) + 30 (le compteur) : le compteur saute
    fr, _ = opponents.welcome(History(49, 25, 0, 24, 0), now=DAY, rng=first)
    assert fr == "Tiens, qui voilà ! Toujours un plaisir de jouer avec toi. C'est notre 50e partie ensemble !"
    monkeypatch.setattr(opponents, "CHAT_MAX", 10)  # puis le cap ; la salutation reste
    assert opponents.welcome(History(49, 25, 0, 24, 0), now=DAY, rng=first)[0].endswith("avec toi.")


def _move(ply, by, scores=(), chosen=None):
    """Un coup d'export : `scores` du point de vue des Blancs, le premier est choisi sauf mention."""
    cands = [{"score": s, "chosen": i == (chosen or 0)} for i, s in enumerate(scores)]
    return {"ply": ply, "by": by, **({"decision": {"candidates": cands}} if scores else {})}


def test_losses_come_from_our_own_evals():
    # Blancs : on joue un coup à 30 quand le meilleur vaut 50 ; l'adversaire laisse l'éval monter à 130 (perte 100).
    moves = [_move(0, "ai", (50, 30), chosen=1), _move(1, "human"), _move(2, "ai", (130, 90)), _move(3, "human"),
             _move(4, "ai", (-2000, -3000))]  # l'adversaire joue mieux que prévu : perte 0 ; éval plafonnée
    assert opponents.losses(moves, "white") == (pytest.approx(50.0), pytest.approx(20 / 3, abs=0.1))
    moves[4] = _move(4, "ai", (5000, 0))  # gaffe énorme : comptée jusqu'au plafond de 1000 seulement
    assert opponents.losses(moves, "white")[0] == pytest.approx((100 + 870) / 2)
    # Noirs : les évals sont retournées (-150 pour les Blancs, c'est +150 pour nous).
    black = [_move(1, "ai", (-50, -30), chosen=1), _move(2, "human"), _move(3, "ai", (-150,))]
    assert opponents.losses(black, "black") == (120.0, 10.0)  # 30 → 150 ; écarts 20 puis 0
    assert opponents.losses([_move(0, "human")], "white") == (None, None)


def test_import_merges_lichess_games_with_our_exports(tmp_path):
    export = {"lichess": {"id": "g1"}, "moves": [_move(0, "ai", (-50,)), _move(1, "human"), _move(2, "ai", (50,))]}
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
    assert fishy[4] == 100.0 and fishy[6] == 3  # perte de l'adversaire (-50 → 50), 3 demi-coups


def test_an_import_does_not_erase_measures_noted_by_the_bot():
    opponents.record(rec("g1", "win", 1000, their_loss=75.0, our_loss=20.0))
    opponents.record(rec("g1", "win", 1001, status="mate"))  # l'import, sans export : pas de mesure
    with opponents.connect() as con:
        assert con.execute("SELECT their_loss, our_loss, status, ended_at FROM games").fetchall() == [(75.0, 20.0, "mate", 1001)]


def _record_many(args):
    path, worker = args
    for i in range(5):
        opponents.record(rec(f"w{worker}-{i}", "win", i), path)


def test_several_bots_can_open_a_new_base_at_once(tmp_path):
    import multiprocessing

    with multiprocessing.get_context("spawn").Pool(5) as pool:
        for round_ in range(4):
            path = tmp_path / f"neuve{round_}.db"
            pool.map(_record_many, [(path, w) for w in range(5)])
            with opponents.connect(path) as con:
                assert con.execute("SELECT count(*) FROM games").fetchone()[0] == 25
