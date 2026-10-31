import chess
import pytest
from fastapi.testclient import TestClient

from fun_fish import personalities
from fun_fish.game import Game, GameError
from fun_fish.server import create_app


def test_game_turns_and_undo(fake_analyser):
    game = Game(personalities.create("stockfish"), chess.WHITE)
    assert not game.engine_to_move
    game.play_human("e2e4")
    assert game.engine_to_move
    game.play_engine(fake_analyser)
    assert len(game.board.move_stack) == 2
    assert game.state()["decision"] is not None
    game.undo()
    assert game.board.move_stack == []
    assert game.decisions == {}


def test_game_rejects_illegal_or_out_of_turn_moves(fake_analyser):
    game = Game(personalities.create("stockfish"), chess.WHITE)
    with pytest.raises(GameError):
        game.play_human("e2e5")
    with pytest.raises(GameError):
        game.play_engine(fake_analyser)
    game.play_human("e2e4")
    with pytest.raises(GameError):
        game.play_human("d2d4")


def test_state_dests_only_on_human_turn():
    game = Game(personalities.create("casual"), chess.BLACK)
    assert game.state()["dests"] == {}
    assert game.state()["engine_to_move"]


def test_promotion_dest_listed_once():
    game = Game(personalities.create("casual"), chess.WHITE)
    game.board.set_fen("8/P6k/8/8/8/8/8/K7 w - - 0 1")
    assert game.state()["dests"]["a7"] == ["a8"]


def test_api_full_cycle(fake_analyser):
    client = TestClient(create_app(fake_analyser))
    assert client.get("/").status_code == 200
    assert client.get("/static/vendor/chessground/chessground.min.js").status_code == 200

    state = client.post("/api/new", json={"personality": "casual", "color": "black"}).json()
    assert state["human_color"] == "black" and state["engine_to_move"]

    state = client.post("/api/engine-move", json={}).json()
    assert len(state["moves"]) == 1 and state["decision"]["candidates"]

    assert client.post("/api/engine-move", json={}).status_code == 409
    assert client.post("/api/move", json={"uci": "e2e4"}).status_code == 400

    state = client.post("/api/move", json={"uci": "e7e5"}).json()
    assert state["engine_to_move"]

    state = client.post("/api/undo", json={}).json()
    assert len(state["moves"]) == 1

    assert client.post("/api/new", json={"personality": "nope"}).status_code == 400


def test_real_stockfish_finds_mate_in_one(stockfish_analyser):
    board = chess.Board("6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1")
    decision = personalities.create("stockfish").choose_move(board, stockfish_analyser)
    assert decision.san == "Rd8#"
    casual = personalities.create("casual").choose_move(board, stockfish_analyser)
    assert board.is_legal(casual.move)
