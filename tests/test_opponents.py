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


def bot_game(game_id, opponent, result, days_ago, loss, now=1000 * DAY):
    return rec(game_id, result, now - days_ago * DAY, opponent=opponent, bot=True, title="BOT", their_loss=loss)


def test_bot_profiles_sort_out_stockfish_like_bots_from_interesting_ones():
    now = 1000 * DAY
    games = [("sf", "loss", 15), ("sf", "loss", 16), ("sf", "draw", 14), ("sf", "loss", None),
             ("rival", "win", 30), ("rival", "loss", 35), ("rival", "draw", 28),
             ("proie", "win", 80), ("proie", "win", 90), ("proie", "win", 70),
             ("costaud", "loss", 35), ("costaud", "loss", 40), ("costaud", "loss", 30),
             ("inconnu", "loss", 10), ("inconnu", "loss", 10)]
    for i, (name, result, loss) in enumerate(games):
        opponents.record(bot_game(f"g{i}", name, result, 1, loss))
    opponents.record(rec("h", "loss", now, opponent="humain", their_loss=5))  # un humain n'a pas de profil
    profiles = opponents.bot_profiles(now)
    assert {n: p.kind for n, p in profiles.items()} == {
        "sf": "stockfish", "rival": "disputé", "proie": "dominé", "costaud": "coriace", "inconnu": "nouveau"}
    assert profiles["sf"].their_loss == pytest.approx(15.0) and profiles["sf"].score == pytest.approx(0.5 / 4)
    assert profiles["sf"].interest < 1 < profiles["rival"].interest and profiles["proie"].interest == 1


def test_an_updated_bot_sheds_its_old_profile():
    """Lissage exponentiel : trois défaites d'il y a 4 mois pèsent moins que trois parties disputées récentes."""
    now = 1000 * DAY
    for i in range(3):
        opponents.record(bot_game(f"old{i}", "maj", "loss", 120, 15))
    assert opponents.bot_profiles(now - 119 * DAY)["maj"].kind == "stockfish"
    for i, result in enumerate(["win", "draw", "loss"]):
        opponents.record(bot_game(f"new{i}", "maj", result, 2, 40))
    maj = opponents.bot_profiles(now)["maj"]
    assert maj.kind == "disputé" and maj.games == pytest.approx(3 * 0.5 ** (2 / 30) + 3 * 0.5 ** 4)


def test_one_old_measure_is_not_enough_to_call_a_bot_stockfish():
    now = 1000 * DAY
    opponents.record(bot_game("old", "x", "loss", 365, 15))
    for i in range(3):
        opponents.record(bot_game(f"new{i}", "x", "loss", 1, None))  # importées sans export : pas de mesure
    assert opponents.bot_profiles(now)["x"].kind == "coriace"


def test_refusals_last_according_to_their_reason():
    day, now = 86400, 1_000_000_000.0
    for name, reason, clock, age in [("jamais", "nobot", "180+0", 29), ("plus_jamais", "nobot", "180+0", 31),
                                     ("occupe", "generic", "180+0", 6), ("plus_occupe", "generic", "180+0", 8),
                                     ("tard", "later", "180+0", 0.5), ("plus_tard", "later", "180+0", 1.5),
                                     ("bizarre", "inconnue", "180+0", 0.5), ("classe", "rated", "180+0", 29),
                                     ("lent", "toofast", "120+1", 6), ("lent", "toofast", "180+0", 8)]:
        opponents.decline(name, reason, clock, at=now - age * day)
    refused = opponents.refusals(now)
    assert {n for n, r in refused.items() if r.blocked} == {"jamais", "occupe", "tard", "bizarre", "classe"}
    assert refused["lent"] == opponents.Refusal(False, frozenset({"120+1"}))  # la cadence seule, une semaine


def test_an_old_database_gets_the_new_columns_and_keeps_its_games(tmp_path):
    import sqlite3
    path = tmp_path / "ancienne.db"
    con = sqlite3.connect(path)
    old = opponents.SCHEMA.split("CREATE TABLE IF NOT EXISTS challenges")[0]  # le schéma d'avant : games seule
    con.executescript(old)
    con.execute("INSERT INTO games (id, account, opponent, bot, color, result, ended_at) "
                "VALUES ('g1', 'dubot', 'x', 1, 'white', 'win', 1.0)")
    con.commit()
    con.close()
    with opponents.connect(path) as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(games)")}
    assert set(opponents.MIGRATIONS) <= columns
    opponents.record(opponents.GameRecord("g2", "dubot", "y", "BOT", True, 2000, "blitz", False, "black", "draw",
                                          "draw", 300, 2.0, None, None, "tricotage", 12.5, 4.1, 0.25), path)
    opponents.record(opponents.GameRecord("g2", "dubot", "y", "BOT", True, 2000, "blitz", False, "black", "draw",
                                          "draw", 300, 2.0), path)  # un import ensuite : ne les efface pas
    with opponents.connect(path) as con:
        rows = con.execute("SELECT id, ending, min_clock, longest_move, reduced FROM games ORDER BY id").fetchall()
    assert rows == [("g1", None, None, None, None), ("g2", "tricotage", 12.5, 4.1, 0.25)]


def test_a_received_challenge_is_noted_with_its_answer():
    c = {"id": "c1", "speed": "blitz", "rated": True, "timeControl": {"type": "clock", "limit": 180, "increment": 2},
         "challenger": {"id": "humain", "name": "Humain", "rating": 1500}}
    opponents.challenge("DuBot", c, "declined", "later", at=5.0)
    with opponents.connect() as con:
        row = con.execute("SELECT * FROM challenges").fetchone()
    assert row == ("c1", "dubot", "humain", None, 0, 1500, "blitz", "180+2", 1, "declined", "later", 5.0)


def test_the_challenge_report_counts_who_we_could_not_serve():
    human = {"id": "c", "challenger": {"id": "h", "name": "h"}, "timeControl": {"type": "clock", "limit": 180, "increment": 0}}
    bot = {"id": "d", "challenger": {"id": "b", "name": "b", "title": "BOT"}}
    for c, decision, reason, at in [(human, "declined", "later", 100.0), (human, "declined", "later", 200.0),
                                    (human, "accepted", None, 300.0), (bot, "declined", "casual", 400.0),
                                    (human, "declined", "later", -10 * 86400.0)]:  # trop ancien
        opponents.challenge("DuBot", c, decision, reason, at=at)
    assert opponents.challenge_report(days=7, now=500.0) == [
        ("dubot", "humain", "declined", "later", 2), ("dubot", "humain", "accepted", None, 1),
        ("dubot", "bot", "declined", "casual", 1)]


def test_a_confirmed_end_replaces_a_lost_from_sight_one_but_not_the_reverse():
    lost = opponents.GameRecord("g", "dubot", "x", "BOT", True, 1500, "blitz", False, "white", "loss", "outoftime",
                                40, 1.0, None, None, "flux perdu", 60.0, 2.0, 0.1)
    confirmed = opponents.GameRecord("g", "dubot", "x", "BOT", True, 1500, "blitz", False, "white", "win", "mate",
                                     80, 2.0, 30.0, 20.0, None, 10.0, 3.0, 0.4)
    def row():
        with opponents.connect() as con:
            return con.execute("SELECT result, ending, min_clock FROM games").fetchone()
    opponents.record(lost, how="absent")
    opponents.record(confirmed, how="confirmed")  # le fil repris termine : sa fin efface « flux perdu »
    assert row() == ("win", None, 10.0)
    opponents.record(lost, how="absent")  # la relecture différée, arrivée après : sans effet
    assert row() == ("win", None, 10.0)


def test_the_import_notes_the_highlights_of_our_exports(tmp_path, monkeypatch):
    export = {"lichess": {"id": "g1"}, "moves": [_move(0, "ai", (-50,))]}
    with gzip.open(tmp_path / "2026-11-14-g1.json.gz", "wt") as f:
        json.dump(export, f)
    game = {"id": "g1", "variant": "standard", "speed": "blitz", "status": "mate", "winner": "white",
            "players": {"white": {"user": {"id": "dubot"}}, "black": {"user": {"id": "x"}}}, "createdAt": 1}
    seen = []
    monkeypatch.setattr(opponents, "note_highlights", lambda gid, account, data, path=None: seen.append((gid, data)))
    opponents.import_history(["dubot"], tmp_path, fetch=lambda a: [game, {**game, "id": "g2"}])
    assert seen == [("g1", export)]  # g2 : pas d'export, pas de coups notables
