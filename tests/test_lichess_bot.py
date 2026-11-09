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
    assert bot.decline_reason({**ok, "speed": "bullet"}) == "tooFast"
    assert bot.decline_reason({**ok, "variant": {"key": "chess960"}}) == "standard"
    assert bot.decline_reason({**ok, "challenger": {"title": "BOT"}}) == "noBot"
