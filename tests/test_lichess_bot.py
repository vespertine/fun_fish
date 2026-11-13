import os
import time

import chess
import pytest

from fun_fish.engines import StockfishNotFound, find_stockfish

from fun_fish import lichess_bot


class FakeApi:
    """Flux d'une partie : l'adversaire (Noirs) répond 1...e5 au premier coup du bot, puis abandonne."""

    def __init__(self):
        self.posts = []

    def post(self, path, data=None, quick=False):
        self.posts.append(path)
        return True

    def stream(self, path):
        yield {"type": "gameFull", "initialFen": "startpos", "white": {"id": "bot"}, "black": {"id": "x", "name": "x"},
               "state": {"moves": "", "status": "started", "wtime": 60000, "btime": 60000}}
        first = next(p for p in self.posts if "/move/" in p).rsplit("/", 1)[1]
        reply = _reply(first)
        yield {"type": "gameState", "moves": f"{first} {reply}", "status": "started", "wtime": 59000, "btime": 59000}
        yield {"type": "gameState", "moves": f"{first} {reply}", "status": "resign", "winner": "white"}



@pytest.fixture(autouse=True)
def no_drain(tmp_path, monkeypatch):
    """Les tests ne dépendent pas d'un drapeau de vidange réel (déploiement en cours sur la machine)."""
    monkeypatch.setattr(lichess_bot, "DRAIN_PATH", tmp_path / "drain")

def _reply(first):
    board = chess.Board()
    board.push_uci(first)
    return next(iter(board.legal_moves)).uci()


class FakeBot:
    def __init__(self, api, stockfish):
        self.api, self.me, self.stockfish = api, "bot", stockfish
        self.personality, self.params = "stockfish", {"depth": 4}
        self.done = []

    def finished(self, game_id, over):
        self.done.append(game_id if over else (game_id, "fin non confirmée"))


def test_bot_game_plays_and_saves(tmp_path, monkeypatch):
    try:
        stockfish_path = find_stockfish(download=False)
    except StockfishNotFound:
        pytest.skip("Stockfish absent")
    monkeypatch.setattr(lichess_bot, "GAMES_DIR", tmp_path)
    api = FakeApi()
    bot = FakeBot(api, stockfish_path)
    game = lichess_bot.BotGame(bot, "abc")
    game.run()
    assert bot.done == ["abc"]
    assert sum("/move/" in p for p in api.posts) == 2
    assert len(list(tmp_path.glob("*abc.json.gz"))) == 1


def test_decline_reasons():
    bot = lichess_bot.Bot("t", "sf", "stockfish", {})
    ok = {"variant": {"key": "standard"}, "speed": "blitz", "challenger": {"name": "h"},
          "timeControl": {"type": "clock", "limit": 300, "increment": 3}}
    assert bot.decline_reason({**ok, "speed": "rapid", "timeControl": {"type": "clock", "limit": 600, "increment": 0}}) is None
    assert bot.decline_reason({**ok, "speed": "rapid", "timeControl": {"type": "clock", "limit": 900, "increment": 10}}) == "tooSlow"
    assert bot.decline_reason({**ok, "speed": "correspondence", "timeControl": {"type": "unlimited"}}) == "tooSlow"
    assert bot.decline_reason(ok) is None
    for limit, inc, reason in ((120, 1, None), (120, 0, "tooFast"), (60, 1, "tooFast"), (60, 0, "tooFast"),
                               (180, 0, None), (15, 0, "tooFast")):
        bullet = {**ok, "timeControl": {"type": "clock", "limit": limit, "increment": inc}}
        assert bot.decline_reason(bullet) == reason, (limit, inc)
    assert bot.decline_reason({**ok, "variant": {"key": "chess960"}}) == "standard"
    slow = {"type": "clock", "limit": 600, "increment": 5}  # 10+5 : pour un humain, oui
    assert bot.decline_reason({**ok, "timeControl": slow, "challenger": {"title": "BOT"}}) == "tooSlow"


class Api:
    def __init__(self, fail_accept=False):
        self.fail_accept, self.calls = fail_accept, []

    def post(self, path, data=None):  # comme Lichess.post : False en cas d'échec, une exception si le flux tombe
        self.calls.append(path)
        if self.fail_accept == "raise" and path.endswith("/accept"):
            raise OSError("réseau coupé")
        return not (self.fail_accept and path.endswith("/accept"))


@pytest.fixture(autouse=True)
def bot_games_dir(tmp_path, monkeypatch):
    """Jamais le vrai dossier partagé des parties contre des bots."""
    monkeypatch.setattr(lichess_bot, "BOT_GAMES_DIR", tmp_path / "bot_games")
    return tmp_path / "bot_games"


def rationed_bot(monkeypatch, now=None, **kw):
    """Un bot et son horloge simulée ; `now` : partager celle d'un autre bot (plusieurs comptes)."""
    now = now or [10_000.0]
    monkeypatch.setattr(lichess_bot.time, "monotonic", lambda: now[0])
    bot = lichess_bot.Bot("t", "sf", "stockfish", {}, **kw)
    bot.api = Api()
    return bot, now


def challenge(speed, limit, inc, title="BOT", who="r", id="c"):
    return {"id": id, "variant": {"key": "standard"}, "speed": speed,
            "challenger": {"title": title, "name": who, "id": who.lower()},
            "timeControl": {"type": "clock", "limit": limit, "increment": inc}}


def accepted(bot, c):
    bot.api.calls.clear()
    bot.handle_challenge(c)
    return bot.api.calls == [f"/api/challenge/{c['id']}/accept"]


def test_bot_challenges_are_rationed(monkeypatch):
    bot, now = rationed_bot(monkeypatch)
    assert bot.decline_reason(challenge("bullet", 60, 0)) == "tooFast"
    assert bot.decline_reason(challenge("rapid", 600, 5)) == "tooSlow"  # au-delà de 10+0 : pas contre les bots
    assert bot.decline_reason(challenge("rapid", 600, 0)) is None  # 10+0, oui
    assert accepted(bot, challenge("blitz", 180, 2, who="a", id="g1"))
    assert not accepted(bot, challenge("bullet", 120, 1, who="b"))  # en pleine partie contre un bot
    assert accepted(bot, challenge("rapid", 600, 0, title=None))  # les humains, toujours
    now[0] += 300
    bot.finished("g1")  # 5 minutes de partie : 15 minutes de pause
    now[0] += 899
    assert not accepted(bot, challenge("bullet", 120, 1, who="b"))
    now[0] += 1
    assert accepted(bot, challenge("bullet", 120, 1, who="b", id="g2"))
    now[0] += 20
    bot.finished("g2")  # partie annulée : une minute de pause
    now[0] += 60
    assert bot.decline_reason(challenge("blitz", 180, 2, who="c")) is None
    free, _ = rationed_bot(monkeypatch, accept_bots=True)
    blitz = challenge("blitz", 180, 2)
    assert accepted(free, blitz) and accepted(free, blitz) and accepted(free, challenge("rapid", 900, 0))


def test_a_human_can_chain_games_before_any_bot(monkeypatch):
    bot, now = rationed_bot(monkeypatch)
    assert accepted(bot, challenge("blitz", 180, 2, title=None, who="h", id="h1"))
    now[0] += 600
    bot.finished("h1")
    now[0] += 119
    assert bot.decline_reason(challenge("blitz", 180, 2)) == "later"  # le temps pour l'humain d'enchaîner
    assert bot.decline_reason(challenge("blitz", 180, 2, title=None, who="h")) is None
    now[0] += 1
    assert bot.decline_reason(challenge("blitz", 180, 2)) is None


def test_one_bot_does_not_take_all_the_bot_games(monkeypatch):
    bot, now = rationed_bot(monkeypatch)
    assert accepted(bot, challenge("bullet", 120, 1, who="Glouton", id="g1"))
    bot.finished("g1")
    now[0] += 3600
    assert bot.decline_reason(challenge("bullet", 120, 1, who="Glouton")) == "later"
    assert bot.decline_reason(challenge("bullet", 120, 1, who="Autre")) is None
    now[0] += 3600
    assert bot.decline_reason(challenge("bullet", 120, 1, who="Glouton")) is None  # deux heures plus tard


def test_never_two_bot_games_at_once_across_our_accounts(monkeypatch, bot_games_dir):
    import os
    fun, now = rationed_bot(monkeypatch)
    crazy, _ = rationed_bot(monkeypatch, now=now)
    fun.me, crazy.me = "funfishbot", "crazyfishbot"
    blitz = challenge("blitz", 180, 2)
    assert accepted(fun, blitz)
    assert crazy.decline_reason(blitz) == "later"  # l'autre compte joue déjà contre un bot
    assert accepted(crazy, challenge("blitz", 180, 2, title=None))  # les humains, toujours
    fun.finished("c")
    assert accepted(crazy, blitz)
    crazy.api.get = lambda path: {"id": "crazyfishbot", "username": "CrazyFishBot", "title": "BOT"}
    crazy.api.stream = lambda path: (_ for _ in ()).throw(SystemExit)
    with pytest.raises(SystemExit):
        crazy.run()  # redémarrage après un arrêt brutal : ses marqueurs sont effacés
    assert fun.decline_reason(challenge("blitz", 180, 2, who="autre")) is None
    blitz = challenge("blitz", 180, 2, who="autre")
    mark = bot_games_dir / "autre--x"
    mark.touch()
    assert fun.decline_reason(blitz) == "later"
    os.utime(mark, (0, 0))  # marqueur oublié depuis longtemps : ignoré
    assert fun.decline_reason(blitz) is None


def test_quota_spent_only_when_lichess_confirms(monkeypatch):
    bot, _ = rationed_bot(monkeypatch)
    blitz = challenge("blitz", 180, 2)
    bot.api.fail_accept = True  # Lichess répond par une erreur : post() renvoie False
    bot.handle_challenge(blitz)
    bot.api.fail_accept = "raise"
    with pytest.raises(OSError):
        bot.handle_challenge(blitz)
    bot.api.fail_accept = False
    assert accepted(bot, blitz)  # aucun des deux échecs n'a consommé le quota


def test_busy_bot_does_not_spend_the_bot_quota(monkeypatch):
    bot, _ = rationed_bot(monkeypatch, max_games=1)
    bot.games["x"] = object()
    assert bot.decline_reason(challenge("blitz", 180, 2)) == "later"
    bot.games.clear()
    assert accepted(bot, challenge("blitz", 180, 2))


def test_describe_challenge_pace():
    c = {"speed": "bullet", "challenger": {"title": "BOT"}, "timeControl": {"type": "clock", "limit": 30, "increment": 0}}
    assert lichess_bot.describe(c) == "(0.5+0 bullet, bot)"
    assert lichess_bot.describe({"speed": "correspondence", "timeControl": {"type": "unlimited"}}) == "(unlimited correspondence)"


def test_blocked_players_are_declined(tmp_path, monkeypatch):
    bot = lichess_bot.Bot("t", "sf", "stockfish", {})
    ok = {"variant": {"key": "standard"}, "speed": "blitz", "challenger": {"id": "indianraw_chess", "name": "INDIANRAW_chess"},
          "timeControl": {"type": "clock", "limit": 300, "increment": 3}}
    monkeypatch.setattr(lichess_bot, "BLOCKED_PATH", tmp_path / "blocked")
    assert bot.decline_reason(ok) is None  # pas de fichier : personne n'est bloqué
    (tmp_path / "blocked").write_text("# tricheurs\n\n  IndianRaw_Chess  # engine, 10/11/2026\nautre\n")
    assert bot.decline_reason(ok) == "generic"  # relu à chaque défi, sans tenir compte de la casse
    assert bot.decline_reason({**ok, "challenger": {"name": "AUTRE"}}) == "generic"
    assert bot.decline_reason({**ok, "challenger": {"id": "h", "name": "h"}}) is None
    (tmp_path / "blocked").write_bytes(b"caf\xe9 # latin-1\nindianraw_chess\n")  # mal encodé : pas d'exception
    assert bot.decline_reason(ok) == "generic"
    assert bot.decline_reason({**ok, "challenger": {"id": "h", "name": "h"}}) is None


def test_drain_flag_declines_everything(tmp_path, monkeypatch):
    bot = lichess_bot.Bot("t", "sf", "stockfish", {})
    ok = {"variant": {"key": "standard"}, "speed": "blitz", "challenger": {"name": "h"},
          "timeControl": {"type": "clock", "limit": 300, "increment": 3}}
    monkeypatch.setattr(lichess_bot, "DRAIN_PATH", tmp_path / "drain")
    assert bot.decline_reason(ok) is None
    (tmp_path / "drain").touch()  # déploiement en cours : il finit ses parties sans en accepter
    assert bot.decline_reason(ok) == "later"


LOCKED = "8/8/2k5/1p1p1p1p/1P1P1P1P/2K5/8/8 w - - 0 50"  # mur de pions : aucun roi ne passe
OPEN_EQUAL = "r3k2r/pp3ppp/2n5/3p4/3P4/2N5/PP3PPP/R3K2R w KQkq - 0 30"  # égale, mais il reste à jouer


def test_locked_and_bare_positions():
    assert lichess_bot.locked(chess.Board(LOCKED))
    assert not lichess_bot.locked(chess.Board("8/8/2k5/1p1p1p1p/1P1P1P1P/2K5/8/7B w - - 0 50"))  # un fou peut se sacrifier
    assert not lichess_bot.locked(chess.Board("8/8/2k5/1p1p1p2/1P1P1P1P/2K5/8/8 w - - 0 50"))  # h5 libre : un pion avance
    assert not lichess_bot.locked(chess.Board("8/8/2k5/2pp1p1p/1P1P1P1P/2K5/8/8 w - - 0 50"))  # bxc5 : une prise
    # contre-exemple de la revue : d4/d5 ne ferment rien, Rd3-c3-b4 contourne et mange d4 (ou d5)
    assert not lichess_bot.locked(chess.Board("8/8/4k3/3p4/3P4/4K3/8/8 w - - 0 50"))
    # contre-exemple de la revue : après …g7-g5, hxg6 en passant ouvre le mur
    assert not lichess_bot.locked(chess.Board("8/5k2/7p/p1p1p1pP/P1P1P1P1/2K5/8/8 w - g6 0 50"))
    assert lichess_bot.bare(chess.Board("8/8/3k4/3r4/8/3K4/3R4/8 w - - 0 60"))  # tour contre tour
    assert not lichess_bot.bare(chess.Board("8/8/3k4/3b4/8/3K4/3R4/8 w - - 0 60"))  # tour contre fou : à creuser


def test_dead_draw_only_for_absolute_draws():
    try:
        path = find_stockfish(download=False)
    except StockfishNotFound:
        pytest.skip("Stockfish absent")
    from fun_fish.analysis import Analyser
    with Analyser(path, threads=1, hash_mb=16) as analyser:
        assert lichess_bot.dead_draw(chess.Board(LOCKED), analyser)
        assert not lichess_bot.dead_draw(chess.Board(OPEN_EQUAL), analyser)
        assert not lichess_bot.dead_draw(chess.Board("8/5k2/7p/p1p1p1pP/P1P1P1P1/2K5/8/8 w - g6 0 50"), analyser)  # en passant
        early = chess.Board(LOCKED.replace(" 0 50", " 0 10"))
        assert not lichess_bot.dead_draw(early, analyser)  # trop tôt dans la partie


def test_offers_after_a_streak_and_not_too_often():
    sense, board = lichess_bot.DrawSense(), chess.Board(LOCKED)
    offers = [sense.offer(board, True) for _ in range(lichess_bot.DRAW_STREAK)]
    assert offers == [False] * (lichess_bot.DRAW_STREAK - 1) + [True]
    assert not sense.offer(board, True)  # pas deux propositions coup sur coup
    assert not lichess_bot.DrawSense().offer(board, False)


class DrawOfferApi(FakeApi):
    """L'adversaire (Noirs) propose la nulle après 1...e5."""

    def stream(self, path):
        yield {"type": "gameFull", "initialFen": "startpos", "white": {"id": "bot"}, "black": {"id": "x", "name": "x"},
               "state": {"moves": "", "status": "started", "wtime": 60000, "btime": 60000}}
        first = next(p for p in self.posts if "/move/" in p).rsplit("/", 1)[1]
        moves = f"{first} {_reply(first)}"
        yield {"type": "gameState", "moves": moves, "status": "started", "wtime": 59000, "btime": 59000, "bdraw": True}
        yield {"type": "gameState", "moves": moves, "status": "draw"}


@pytest.mark.parametrize("dead", [True, False])
def test_answers_a_draw_offer(tmp_path, monkeypatch, dead):
    try:
        stockfish_path = find_stockfish(download=False)
    except StockfishNotFound:
        pytest.skip("Stockfish absent")
    monkeypatch.setattr(lichess_bot, "GAMES_DIR", tmp_path)
    monkeypatch.setattr(lichess_bot, "dead_draw", lambda board, analyser: dead)
    api = DrawOfferApi()
    lichess_bot.BotGame(FakeBot(api, stockfish_path), "nul").run()
    assert sum("/draw/yes" in p for p in api.posts) == (1 if dead else 0)
    assert not any("/draw/no" in p for p in api.posts)  # refuser, c'est jouer : aucune requête en plus


def test_optional_actions_never_wait_like_moves(monkeypatch):
    """Nulle et chat : un seul essai court, jamais les 60 s d'attente après un 429 (perte au temps)."""
    import urllib.error

    calls, sleeps = [], []

    def refuse(req, timeout=None):
        calls.append(timeout)
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(lichess_bot.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(lichess_bot.time, "sleep", sleeps.append)
    api = lichess_bot.Lichess("jeton")
    assert not api.post("/api/bot/game/x/draw/yes", quick=True)
    assert calls == [lichess_bot.QUICK_TIMEOUT] and sleeps == []
    api.post("/api/bot/game/x/move/e2e4")  # un coup, lui, insiste
    assert 60 in sleeps


class OfferApi(FakeApi):
    """Partie déjà au 40e coup dans un mur de pions : le bot doit proposer la nulle avec son coup."""

    def stream(self, path):
        yield {"type": "gameFull", "initialFen": "startpos", "white": {"id": "bot"}, "black": {"id": "x", "name": "x"},
               "state": {"moves": "", "status": "started", "wtime": 60000, "btime": 60000}}
        first = next(p for p in self.posts if "/move/" in p).rsplit("/", 1)[1].split("?")[0]
        yield {"type": "gameState", "moves": f"{first} {_reply(first)}", "status": "draw"}


def test_draw_offer_travels_with_the_move(tmp_path, monkeypatch):
    try:
        stockfish_path = find_stockfish(download=False)
    except StockfishNotFound:
        pytest.skip("Stockfish absent")
    monkeypatch.setattr(lichess_bot, "GAMES_DIR", tmp_path)
    monkeypatch.setattr(lichess_bot, "dead_draw", lambda board, analyser: True)
    monkeypatch.setattr(lichess_bot, "DRAW_STREAK", 1)
    api = OfferApi()
    lichess_bot.BotGame(FakeBot(api, stockfish_path), "offre").run()
    move = next(p for p in api.posts if "/move/" in p)
    assert move.endswith("?offeringDraw=true") and not any(p.endswith("/draw/yes") for p in api.posts)
    assert sum("/chat" in p for p in api.posts) == 2 + len(lichess_bot.DRAW_OFFER)  # accueil + message de nulle


def test_greetings_name_the_bot_and_fit_lichess_chat():
    from fun_fish import personalities
    assert "Fun Fish" in lichess_bot.GREETING[0]
    for key, texts in lichess_bot.GREETINGS.items():
        name = personalities.PERSONALITIES[key].name
        assert name in texts[0] and name in texts[1]
    for texts in (lichess_bot.GREETING, *lichess_bot.GREETINGS.values()):
        assert all(len(t) <= 140 for t in texts)


@pytest.mark.parametrize("failure", ["http", "network"])
def test_quota_kept_when_the_real_client_fails_to_accept(monkeypatch, failure):
    import io
    import urllib.error
    bot, _ = rationed_bot(monkeypatch)
    bot.api = lichess_bot.Lichess("t")  # le vrai client, transport simulé
    monkeypatch.setattr(lichess_bot.time, "sleep", lambda s: None)
    sent = []

    def request(method, path, data=None, timeout=30):
        sent.append(path)
        if path.endswith("/accept") and len(sent) == 1:
            if failure == "http":
                raise urllib.error.HTTPError(path, 404, "défi annulé", {}, io.BytesIO(b"{}"))
            raise OSError("réseau coupé")
        if path.endswith("/accept") and failure == "network" and len(sent) <= 3:
            raise OSError("réseau coupé")
        return io.BytesIO(b"{}")
    monkeypatch.setattr(bot.api, "_request", request)
    blitz = challenge("blitz", 180, 2)
    bot.handle_challenge(blitz)
    assert bot.bot_games == {}  # rien de consommé
    bot.handle_challenge(blitz)
    assert "c" in bot.bot_games


def test_a_lost_game_end_still_pauses_the_bots(monkeypatch):
    bot, now = rationed_bot(monkeypatch)
    monkeypatch.setattr(lichess_bot, "bot_game_running", lambda: False)  # marqueur impossible à écrire
    assert accepted(bot, challenge("bullet", 120, 1, who="a", id="g1"))
    now[0] += 1800  # fin de partie jamais vue (flux coupé) : on ne repart pas pour autant
    assert bot.decline_reason(challenge("bullet", 120, 1, who="b")) == "later"
    now[0] += 1800
    assert bot.decline_reason(challenge("bullet", 120, 1, who="b")) is None


def two_accounts(monkeypatch):
    fun, now = rationed_bot(monkeypatch)
    crazy, _ = rationed_bot(monkeypatch, now=now)
    fun.me, crazy.me = "funfishbot", "crazyfishbot"
    return fun, crazy, now


def test_two_accounts_cannot_both_take_the_bot_place_while_accepting(monkeypatch, bot_games_dir):
    import threading
    fun, crazy, _ = two_accounts(monkeypatch)
    inside, release = threading.Event(), threading.Event()
    post = fun.api.post

    def slow_accept(path, data=None):  # Lichess répond lentement à l'acceptation de fun
        if path.endswith("/accept"):
            inside.set()
            release.wait(5)
        return post(path, data)
    fun.api.post = slow_accept
    worker = threading.Thread(target=fun.handle_challenge, args=(challenge("blitz", 180, 2, who="a", id="g1"),))
    worker.start()
    assert inside.wait(5)
    crazy.decline_reason = lambda c: None  # son contrôle rapide est passé juste avant la réservation de fun
    crazy.handle_challenge(challenge("blitz", 180, 2, who="b", id="g2"))  # pendant l'acceptation de fun
    release.set()
    worker.join(5)
    assert crazy.api.calls == ["/api/challenge/g2/decline"]
    assert [m.name for m in bot_games_dir.glob("*--*")] == ["funfishbot--g1"]


def test_a_failed_accept_frees_the_bot_place(monkeypatch, bot_games_dir):
    fun, crazy, _ = two_accounts(monkeypatch)
    fun.api.fail_accept = True
    fun.handle_challenge(challenge("blitz", 180, 2, who="a", id="g1"))
    assert list(bot_games_dir.glob("*--*")) == []
    assert accepted(crazy, challenge("blitz", 180, 2, who="b", id="g2"))


def test_a_cut_game_stream_keeps_the_bot_place(monkeypatch, bot_games_dir):
    from fun_fish import analysis
    fun, crazy, _ = two_accounts(monkeypatch)
    assert accepted(fun, challenge("blitz", 180, 2, who="a", id="g1"))

    class NoEngine:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False
    monkeypatch.setattr(analysis, "Analyser", NoEngine)

    def cut(path):
        raise OSError("flux coupé")
        yield
    fun.api.stream = cut
    lichess_bot.BotGame(fun, "g1").run()  # le vrai fil de partie : erreur réseau, fin non confirmée
    assert "g1" not in fun.games and "g1" in fun.bot_games
    assert (bot_games_dir / "funfishbot--g1").exists()
    assert crazy.decline_reason(challenge("blitz", 180, 2, who="b")) == "later"
    assert fun.decline_reason(challenge("blitz", 180, 2, who="b")) == "later"  # sa pause de sécurité aussi


def test_a_restart_keeps_the_place_of_a_game_still_running(monkeypatch, bot_games_dir):
    fun, crazy, now = two_accounts(monkeypatch)
    assert accepted(crazy, challenge("blitz", 180, 2, who="a", id="g7"))
    assert accepted(crazy, challenge("blitz", 180, 2, title=None, who="h", id="g8"))
    restarted, _ = rationed_bot(monkeypatch, now=now)  # le service redémarre, la partie g7 continue
    mark = bot_games_dir / "crazyfishbot--g7"
    os.utime(mark, (time.time() - 120, time.time() - 120))  # acceptée il y a deux minutes

    def get(path):
        if path == "/api/account/playing":
            return {"nowPlaying": [{"gameId": "g7"}]}
        return {"id": "crazyfishbot", "username": "CrazyFishBot", "title": "BOT"}
    restarted.api.get = get
    restarted.api.stream = lambda path: (_ for _ in ()).throw(SystemExit)
    with pytest.raises(SystemExit):
        restarted.run()
    assert (bot_games_dir / "crazyfishbot--g7").exists()
    assert fun.decline_reason(challenge("blitz", 180, 2, who="b")) == "later"
    now[0] += 480
    restarted.finished("g7")  # fin après dix minutes de jeu : c'était bien un bot, 30 minutes de pause
    assert not mark.exists()
    assert fun.decline_reason(challenge("blitz", 180, 2, who="b")) is None
    now[0] += 1795  # marge de quelques secondes pour l'âge du marqueur, mesuré à l'horloge réelle
    assert restarted.decline_reason(challenge("blitz", 180, 2, who="b")) == "later"
    now[0] += 10
    assert restarted.decline_reason(challenge("blitz", 180, 2, who="b")) is None


def test_a_busy_lock_never_lets_a_bot_in_without_its_reservation(monkeypatch, bot_games_dir):
    fun, crazy, _ = two_accounts(monkeypatch)
    monkeypatch.setattr(lichess_bot.time, "sleep", lambda s: None)
    bot_games_dir.mkdir()
    (bot_games_dir / "verrou").touch()  # laissé par un processus arrêté il y a un instant
    assert not accepted(fun, challenge("blitz", 180, 2, who="a", id="g1"))
    assert fun.api.calls == ["/api/challenge/g1/decline"] and fun.bot_games == {}
    assert not accepted(crazy, challenge("blitz", 180, 2, who="b", id="g2"))


class MatchApi(Api):
    """Lichess vu par la recherche d'adversaires : notre compte, les bots en ligne, les défis créés."""

    def __init__(self, online, perfs=None):
        super().__init__()
        self.online, self.created = online, []
        self.perfs = perfs or {"bullet": {"rating": 1800, "rd": 150, "games": 3},
                               "blitz": {"rating": 1700, "rd": 60, "games": 40}}
        self.on_create = None

    playing: list | Exception = []

    def get(self, path):
        if path == "/api/account/playing":
            if isinstance(self.playing, Exception):
                raise self.playing
            return {"nowPlaying": self.playing}
        return {"id": "funfishbot", "username": "FunFishBot", "title": "BOT", "perfs": self.perfs}

    def lines(self, path):
        return self.online

    def create(self, path, data):
        self.created.append((path.rsplit("/", 1)[1], data))
        game_id = f"m{len(self.created)}"
        if self.on_create:
            self.on_create(game_id)
        return {"id": game_id}


def online_bot(name, bullet=1800, blitz=1700):
    return {"id": name, "username": name, "title": "BOT",
            "perfs": {"bullet": {"rating": bullet}, "blitz": {"rating": blitz}}}


def seeker(monkeypatch, online, now=None, me="funfishbot", **kw):
    bot, now = rationed_bot(monkeypatch, now=now, matchmaking=True)
    bot.api, bot.me = MatchApi(online, **kw), me
    return bot, now


def test_seeks_a_bot_only_after_ten_idle_minutes(monkeypatch, bot_games_dir):
    bot, now = seeker(monkeypatch, [online_bot("rival")])
    now[0] += 599
    assert bot.seek() is None and bot.api.created == []
    now[0] += 1
    game_id = bot.seek()
    assert game_id == "m1" and bot.outgoing[0] == "m1"
    opponent, data = bot.api.created[0]
    assert opponent == "rival" and data["rated"] == "true"
    assert (data["clock.limit"], data["clock.increment"]) == (120, 1)  # le bullet, au classement le plus incertain
    assert (bot_games_dir / "funfishbot--m1").exists()  # la place commune est prise


def test_seek_picks_only_suitable_opponents(monkeypatch, tmp_path):
    monkeypatch.setattr(lichess_bot, "BLOCKED_PATH", tmp_path / "blocked")
    (tmp_path / "blocked").write_text("tricheur\n")
    online = [online_bot("crazyfishbot"), online_bot("funfishbot"), online_bot("trop_fort", blitz=2100),
              online_bot("tricheur"), online_bot("refuse"), online_bot("deja_joue"), online_bot("le_bon")]
    for _ in range(10):
        bot, now = seeker(monkeypatch, online, perfs={"bullet": {"rating": 1800, "rd": 45, "games": 90},
                                                     "blitz": {"rating": 1700, "rd": 200, "games": 2}})
        bot.declined["refuse"] = bot.bot_opponents["deja_joue"] = now[0]
        now[0] += 600
        assert bot.seek()
        assert bot.api.created[0][0] == "le_bon"
        assert bot.api.created[0][1]["clock.limit"] in (180, 300)  # du blitz, cette fois
        bot.drop_outgoing(bot.outgoing[0])
        os.utime(lichess_bot.BOT_GAMES_DIR / "dernier-defi", (0, 0))


def test_no_seek_while_busy_paused_draining_or_waiting(monkeypatch, tmp_path):
    bot, now = seeker(monkeypatch, [online_bot("rival")])
    now[0] += 600
    bot.games["x"] = object()
    assert bot.seek() is None
    bot.games.clear()
    bot.bot_free_at = now[0] + 1
    assert bot.seek() is None
    bot.bot_free_at = 0
    (tmp_path / "drain").touch()
    assert bot.seek() is None
    (tmp_path / "drain").unlink()
    assert bot.seek() == "m1"
    assert bot.seek() is None  # un défi en attente de réponse
    now[0] += 31
    assert bot.seek() is None and bot.outgoing is None  # sans réponse : oublié, place rendue
    assert list(lichess_bot.BOT_GAMES_DIR.glob("*--*")) == []
    assert bot.api.created == [("rival", bot.api.created[0][1])]


def test_a_declined_challenge_frees_the_place_and_the_bot_is_left_alone(monkeypatch, bot_games_dir):
    bot, now = seeker(monkeypatch, [online_bot("rival")])
    bot.bot_opponents.clear()
    now[0] += 600
    bot.seek()
    bot.drop_outgoing("m1", declined=True)  # événement challengeDeclined
    assert bot.outgoing is None and list(bot_games_dir.glob("*--*")) == []
    os.utime(bot_games_dir / "dernier-defi", (0, 0))
    bot.bot_opponents.clear()
    assert bot.seek() is None  # il nous a refusé : pas avant un jour
    now[0] += 86400
    assert bot.seek() == "m2"


def test_our_accounts_space_their_challenges(monkeypatch, bot_games_dir):
    fun, now = seeker(monkeypatch, [online_bot("rival"), online_bot("autre")])
    crazy, _ = seeker(monkeypatch, [online_bot("rival"), online_bot("autre")], now=now, me="crazyfishbot")
    now[0] += 600
    assert fun.seek() == "m1"
    fun.drop_outgoing("m1", declined=True)
    assert crazy.seek() is None  # moins de 5 minutes depuis le défi de fun
    os.utime(bot_games_dir / "dernier-defi", (time.time() - 300, time.time() - 300))
    assert crazy.seek() == "m1"


def test_an_accepted_challenge_is_a_bot_game(monkeypatch, bot_games_dir):
    bot, now = seeker(monkeypatch, [online_bot("rival")])
    monkeypatch.setattr(bot, "start_game", lambda game_id: bot.games.__setitem__(game_id, object()))
    now[0] += 600
    bot.seek()
    bot.game_started("m1")
    assert bot.outgoing is None and "m1" in bot.bot_games
    now[0] += 300
    bot.finished("m1")
    assert bot.bot_free_at == now[0] + 900 and list(bot_games_dir.glob("*--*")) == []


def test_an_instant_acceptance_is_still_a_bot_game(monkeypatch):
    bot, now = seeker(monkeypatch, [online_bot("rival")])
    monkeypatch.setattr(bot, "start_game", lambda game_id: bot.games.__setitem__(game_id, object()))
    bot.api.on_create = bot.game_started  # gameStart avant la réponse à la création
    now[0] += 600
    bot.seek()
    assert bot.outgoing is None and "m1" in bot.bot_games


def test_a_human_takes_the_place_of_our_pending_challenge(monkeypatch, bot_games_dir):
    bot, now = seeker(monkeypatch, [online_bot("rival")])
    now[0] += 600
    bot.seek()
    bot.api.calls.clear()
    bot.handle_challenge(challenge("blitz", 180, 2, title=None, who="h", id="h1"))
    assert bot.api.calls == ["/api/challenge/m1/cancel", "/api/challenge/h1/accept"]
    assert bot.outgoing is None and list(bot_games_dir.glob("*--*")) == []
    other, now = seeker(monkeypatch, [online_bot("rival")])
    now[0] += 600
    os.utime(bot_games_dir / "dernier-defi", (0, 0))
    other.seek()
    other.api.fail_accept = False
    other.api.post = lambda path, data=None: other.api.calls.append(path) or not path.endswith("/cancel")
    other.api.calls.clear()
    other.handle_challenge(challenge("blitz", 180, 2, title=None, who="h", id="h2"))
    assert other.api.calls == ["/api/challenge/m1/cancel", "/api/challenge/h2/decline"]  # déjà accepté ?


def test_a_human_arriving_while_our_challenge_is_created_waits(monkeypatch):
    bot, now = seeker(monkeypatch, [online_bot("rival")])
    bot.api.on_create = lambda game_id: bot.handle_challenge(challenge("blitz", 180, 2, title=None, who="h", id="h1"))
    now[0] += 600
    bot.seek()
    assert bot.api.calls == ["/api/challenge/h1/decline"]  # on ne sait pas encore si le bot a accepté


def test_the_event_stream_reports_a_declined_challenge(monkeypatch, bot_games_dir):
    bot, now = seeker(monkeypatch, [online_bot("rival")])
    now[0] += 600
    bot.seek()

    def events(path):
        yield {"type": "challenge", "challenge": {"id": "m1", "challenger": {"id": "funfishbot"}}}  # le nôtre
        yield {"type": "challengeDeclined", "challenge": {"id": "m1", "destUser": {"name": "rival"},
                                                          "declineReasonKey": "later"}}
        raise SystemExit
    bot.api.stream = events
    bot.matchmaking = False  # pas de fil de recherche dans ce test
    with pytest.raises(SystemExit):
        bot.run()
    assert bot.outgoing is None and "rival" in bot.declined and not (bot_games_dir / "funfishbot--m1").exists()


def test_reference_rating_ignores_the_placeholder_of_unplayed_speeds():
    unplayed = {"rating": 3000, "rd": 500, "games": 0, "prov": True}
    dubot = {"bullet": unplayed, "blitz": unplayed, "classical": unplayed,
             "rapid": {"rating": 2590, "rd": 227, "games": 19, "prov": True}}
    assert lichess_bot.reference_rating(dubot, "bullet") == 2590
    crazy = {**dubot, "blitz": {"rating": 2433, "rd": 163, "games": 5}, "bullet": {"rating": 2403, "rd": 371, "games": 2}}
    assert lichess_bot.reference_rating(crazy, "bullet") == 2403  # déjà jouée : son propre classement
    assert lichess_bot.reference_rating({**dubot, "rapid": unplayed}, "blitz") == 2000
    assert lichess_bot.reference_rating(crazy, "rapid") == 2590


@pytest.mark.parametrize("meanwhile", ["human_started", "human_accepted", "drain"])
def test_seek_rechecks_the_account_after_its_network_reads(monkeypatch, tmp_path, meanwhile):
    bot, now = seeker(monkeypatch, [online_bot("rival")])
    monkeypatch.setattr(bot, "start_game", lambda game_id: bot.games.__setitem__(game_id, object()))
    online = bot.api.lines

    def lines(path):  # pendant la lecture des bots en ligne, le fil des événements travaille
        if meanwhile == "drain":
            (tmp_path / "drain").touch()
        else:
            bot.handle_challenge(challenge("blitz", 180, 2, title=None, who="h", id="h1"))
            if meanwhile == "human_started":
                bot.game_started("h1")
        return online(path)
    bot.api.lines = lines
    now[0] += 600
    assert bot.seek() is None and bot.api.created == [] and bot.outgoing is None
    assert list(lichess_bot.BOT_GAMES_DIR.glob("*--*")) == []


def test_a_lost_creation_response_keeps_the_place_and_the_game(monkeypatch, bot_games_dir):
    import io
    bot, now = seeker(monkeypatch, [online_bot("rival")])
    monkeypatch.setattr(bot, "start_game", lambda game_id: bot.games.__setitem__(game_id, object()))
    real = lichess_bot.Lichess("t")  # le vrai client, transport simulé

    def request(method, path, data=None, timeout=30):
        bot.game_started("accepted")  # le bot accepte tout de suite...
        raise TimeoutError("réponse perdue")  # ... et la réponse à la création se perd
    monkeypatch.setattr(real, "_request", request)
    bot.api.create = real.create
    now[0] += 600
    assert bot.seek() is None
    assert "accepted" in bot.games and "accepted" in bot.bot_games and bot.outgoing is None
    assert [m.name for m in bot_games_dir.glob("*--*")] == ["funfishbot--accepted"]
    now[0] += 300
    bot.finished("accepted")
    assert bot.bot_free_at == now[0] + 900  # la pause d'une partie contre un bot


def uncertain_seek(monkeypatch):
    bot, now = seeker(monkeypatch, [online_bot("rival")])
    monkeypatch.setattr(bot, "start_game", lambda game_id: bot.games.__setitem__(game_id, object()))

    def lost(path, data):
        raise OSError("réseau coupé")
    bot.api.create = lost
    now[0] += 600
    assert bot.seek() is None and bot.outgoing[0] == ""
    return bot, now


def test_an_uncertain_creation_keeps_the_place_until_lichess_says_no_game(monkeypatch, bot_games_dir):
    bot, now = uncertain_seek(monkeypatch)
    assert [m.name for m in bot_games_dir.glob("*--*")] == ["funfishbot--sortant"]
    assert bot.decline_reason(challenge("blitz", 180, 2, who="autre")) == "later"
    now[0] += 61
    bot.api.playing = OSError("Lichess muet")
    bot.seek()
    assert bot.outgoing is not None and [m.name for m in bot_games_dir.glob("*--*")] == ["funfishbot--sortant"]
    bot.api.playing = [{"gameId": "h9", "opponent": {"id": "un_humain"}}]
    bot.seek()
    assert bot.outgoing is None and list(bot_games_dir.glob("*--*")) == []


def test_a_game_started_without_news_is_found_and_played(monkeypatch, bot_games_dir):
    bot, now = uncertain_seek(monkeypatch)
    now[0] += 61
    bot.api.playing = [{"gameId": "late", "opponent": {"id": "rival", "username": "rival"}}]
    assert bot.seek() is None
    assert "late" in bot.games and "late" in bot.bot_games and bot.outgoing is None
    assert [m.name for m in bot_games_dir.glob("*--*")] == ["funfishbot--late"]
    bot.game_started("late")  # le gameStart retardé arrive enfin : rien de plus
    now[0] += 300
    bot.finished("late")
    assert bot.bot_free_at == now[0] + 3 * 361  # une partie contre un bot, depuis le défi


@pytest.mark.parametrize("event", ["declined", "canceled"])
def test_an_answer_arriving_before_the_creation_response_is_kept(monkeypatch, bot_games_dir, event):
    bot, now = seeker(monkeypatch, [online_bot("rival")])
    bot.api.on_create = lambda game_id: bot.drop_outgoing(game_id, declined=event == "declined")
    now[0] += 600
    assert bot.seek() is None
    assert bot.outgoing is None and list(bot_games_dir.glob("*--*")) == []
    assert ("rival" in bot.declined) == (event == "declined")


def test_a_human_is_not_accepted_if_a_seek_claims_the_account_meanwhile(monkeypatch):
    bot, _ = rationed_bot(monkeypatch)

    def seek_slips_in():  # rien à annuler... mais la recherche réserve le compte juste après
        bot.outgoing = ("", 0.0, "rival")
        return True
    monkeypatch.setattr(bot, "cancel_outgoing", seek_slips_in)
    bot.handle_challenge(challenge("blitz", 180, 2, title=None, who="h", id="h1"))
    assert bot.api.calls == ["/api/challenge/h1/decline"] and bot.accepting == {}


def test_a_slow_human_acceptance_never_expires_while_its_post_runs(monkeypatch):
    import io
    import urllib.error
    bot, now = seeker(monkeypatch, [online_bot("rival")], perfs=None)
    bot.max_games = 1
    real = lichess_bot.Lichess("t")  # le vrai client : un 429, une minute d'attente, puis le succès
    answers = iter([urllib.error.HTTPError("/accept", 429, "trop vite", {}, io.BytesIO(b"")), None])

    def request(method, path, data=None, timeout=30):
        answer = next(answers)
        if answer:
            raise answer
        return io.BytesIO(b"{}")
    monkeypatch.setattr(real, "_request", request)
    seen = []

    def wait(seconds):  # pendant l'attente du 429, le fil de recherche passe
        now[0] += seconds + 1
        seen.append(bot.seek())
    monkeypatch.setattr(lichess_bot.time, "sleep", wait)
    bot.api.post = real.post
    now[0] += 600
    bot.handle_challenge(challenge("blitz", 180, 2, title=None, who="h", id="h1"))
    assert seen == [None] and bot.api.created == [] and bot.outgoing is None
    assert bot.accepting["h1"] is not None  # confirmé : l'attente de gameStart commence maintenant
    now[0] += 59
    assert bot.seek() is None


def test_a_found_game_keeps_the_account_busy_until_its_thread_starts(monkeypatch):
    bot, now = uncertain_seek(monkeypatch)
    bot.max_games = 1
    start = bot.start_game

    def human_slips_in(game_id):  # le fil des événements passe juste avant le démarrage de la partie
        bot.handle_challenge(challenge("blitz", 180, 2, title=None, who="h", id="h1"))
        start(game_id)
    monkeypatch.setattr(bot, "start_game", human_slips_in)
    now[0] += 61
    bot.api.playing = [{"gameId": "late", "opponent": {"id": "rival"}}]
    bot.api.calls.clear()
    bot.seek()
    assert bot.api.calls == ["/api/challenge/h1/decline"] and list(bot.games) == ["late"]


def test_confirmed_acceptances_are_checked_with_lichess_before_being_forgotten(monkeypatch):
    bot, now = seeker(monkeypatch, [online_bot("rival")])
    started = []
    def start(game_id):  # comme le vrai : la partie est en cours, l'acceptation est réglée
        started.append(game_id)
        bot.games[game_id] = object()
        bot.accepting.pop(game_id, None)
    monkeypatch.setattr(bot, "start_game", start)
    now[0] += 600
    assert accepted(bot, challenge("blitz", 180, 2, title=None, who="h", id="h1"))  # puis aucun gameStart
    now[0] += 61
    bot.api.playing = OSError("Lichess muet")
    assert bot.seek() is None and "h1" in bot.accepting and bot.api.created == []
    bot.api.playing = [{"gameId": "h1", "opponent": {"id": "h"}}]
    assert bot.seek() is None and started == ["h1"] and bot.api.created == []  # la partie est jouée
    bot.finished("h1")
    now[0] += 120  # passé le délai laissé à l'humain pour enchaîner
    assert accepted(bot, challenge("blitz", 180, 2, title=None, who="h", id="h2"))
    now[0] += 61
    bot.api.playing = []  # jamais commencée : oubliée, le compte est libre
    assert bot.seek() is None and "h2" not in bot.accepting
    now[0] += 600  # libre depuis dix minutes
    assert bot.seek() == "m1"


def test_the_watch_thread_reconciles_even_without_matchmaking(monkeypatch):
    bot, _ = rationed_bot(monkeypatch)
    calls = []
    monkeypatch.setattr(bot, "reconcile_accepting", lambda: calls.append("vérifié"))
    monkeypatch.setattr(bot, "seek", lambda: calls.append("recherche"))
    ticks, waits = iter([None, None]), []
    monkeypatch.setattr(lichess_bot.time, "sleep", lambda s: waits.append(s) or next(ticks))
    with pytest.raises(StopIteration):
        bot.watch_forever()
    assert calls == ["vérifié", "vérifié"]


def test_our_accounts_take_turns_for_the_shared_slot(monkeypatch):
    """Intervalle tiré au hasard : pas toujours le même compte en premier sur le créneau commun."""
    bot, _ = rationed_bot(monkeypatch)
    monkeypatch.setattr(bot, "reconcile_accepting", lambda: None)
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 50:
            raise StopIteration
    monkeypatch.setattr(lichess_bot.time, "sleep", sleep)
    with pytest.raises(StopIteration):
        bot.watch_forever()
    assert all(30 <= w <= 90 for w in waits) and max(waits) - min(waits) > 20


def test_the_echo_of_our_own_declines_is_not_logged(monkeypatch, capsys):
    bot, _ = rationed_bot(monkeypatch)
    bot.me = "crazyfishbot"

    def events(path):
        yield {"type": "challengeDeclined", "challenge": {"id": "x", "challenger": {"id": "devilfishbot"},
                                                          "destUser": {"name": "CrazyFishBot"}, "declineReasonKey": "toofast"}}
        yield {"type": "challengeDeclined", "challenge": {"id": "y", "challenger": {"id": "crazyfishbot"},
                                                          "destUser": {"name": "Rival"}, "declineReasonKey": "nobot"}}
        raise SystemExit
    bot.api.get = lambda path: {"id": "crazyfishbot", "username": "CrazyFishBot", "title": "BOT"}
    bot.api.stream = events
    monkeypatch.setattr(lichess_bot.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())
    with pytest.raises(SystemExit):
        bot.run()
    out = capsys.readouterr().out
    assert "refusé par Rival : nobot" in out and "refusé par CrazyFishBot" not in out


def test_a_confirmed_acceptance_still_holds_the_place_for_a_second_human(monkeypatch):
    bot, now = rationed_bot(monkeypatch, max_games=1)
    assert accepted(bot, challenge("blitz", 180, 2, title=None, who="h", id="h1"))  # gameStart en retard
    now[0] += 61  # avant toute vérification auprès de Lichess
    assert bot.decline_reason(challenge("blitz", 180, 2, title=None, who="h2")) == "later"
