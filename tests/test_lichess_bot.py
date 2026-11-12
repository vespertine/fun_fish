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

    def finished(self, game_id):
        self.done.append(game_id)


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
    assert bot.decline_reason({**ok, "speed": "classical", "challenger": {"title": "BOT"}}) == "noBot"  # hors quota


class Api:
    def __init__(self, fail_accept=False):
        self.fail_accept, self.calls = fail_accept, []

    def post(self, path, data=None):  # comme Lichess.post : False en cas d'échec, une exception si le flux tombe
        self.calls.append(path)
        if self.fail_accept == "raise" and path.endswith("/accept"):
            raise OSError("réseau coupé")
        return not (self.fail_accept and path.endswith("/accept"))


def rationed_bot(monkeypatch, **kw):
    now = [10_000.0]
    monkeypatch.setattr(lichess_bot.time, "monotonic", lambda: now[0])
    bot = lichess_bot.Bot("t", "sf", "stockfish", {}, **kw)
    bot.api = Api()
    return bot, now


def challenge(speed, limit, inc, title="BOT"):
    return {"id": "c", "variant": {"key": "standard"}, "speed": speed, "challenger": {"title": title, "name": "r"},
            "timeControl": {"type": "clock", "limit": limit, "increment": inc}}


def accepted(bot, c):
    bot.api.calls.clear()
    bot.handle_challenge(c)
    return bot.api.calls == ["/api/challenge/c/accept"]


def test_bot_challenges_are_rationed(monkeypatch):
    bot, now = rationed_bot(monkeypatch)
    blitz, bullet, rapid = challenge("blitz", 180, 2), challenge("bullet", 120, 1), challenge("rapid", 600, 0)
    assert bot.decline_reason(challenge("bullet", 60, 0)) == "tooFast"
    assert accepted(bot, blitz)  # le premier de l'heure
    assert not accepted(bot, bullet)  # bullet et blitz partagent leur quota
    assert accepted(bot, rapid) and not accepted(bot, rapid)  # le rapide a le sien
    assert accepted(bot, challenge("blitz", 180, 2, title=None))  # les humains, toujours
    now[0] += 3600
    assert accepted(bot, bullet) and not accepted(bot, rapid)
    now[0] += 3600
    assert accepted(bot, rapid)
    free, _ = rationed_bot(monkeypatch, accept_bots=True)
    assert accepted(free, blitz) and accepted(free, blitz)


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
    assert bot.bot_accepted == {}  # rien de consommé
    bot.handle_challenge(blitz)
    assert "vif" in bot.bot_accepted
