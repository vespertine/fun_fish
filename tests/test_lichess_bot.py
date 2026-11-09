import chess
import pytest

from fun_fish.engines import StockfishNotFound, find_stockfish

from fun_fish import lichess_bot


class FakeApi:
    """Flux d'une partie : l'adversaire (Noirs) répond 1...e5 au premier coup du bot, puis abandonne."""

    def __init__(self):
        self.posts = []

    def post(self, path, data=None):
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
    assert bot.decline_reason({**ok, "challenger": {"title": "BOT"}}) == "noBot"


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
    assert sum("/draw/no" in p for p in api.posts) == (0 if dead else 1)
