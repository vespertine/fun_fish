import random
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
    client = TestClient(create_app(fake_analyser, debug=True))
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


def test_api_settings_apply_and_retry(fake_analyser):
    client = TestClient(create_app(fake_analyser, debug=True))
    info = client.get("/api/info").json()
    hustler = next(p for p in info["personalities"] if p["key"] == "hustler")
    assert any(spec["name"] == "tolerance" and spec["help"] for spec in hustler["params"])

    state = client.post("/api/new", json={"personality": "casual", "color": "white",
                                          "params": {"max_loss": 40, "temperature": 10.5}}).json()
    assert state["personality"]["params"]["max_loss"] == 40
    assert client.post("/api/new", json={"personality": "casual", "params": {"nope": 1}}).status_code == 400

    client.post("/api/move", json={"uci": "e2e4"})
    assert not client.post("/api/move", json={"uci": "d2d4"}).status_code == 200
    state = client.post("/api/engine-move", json={}).json()
    assert state["can_retry"] and len(state["moves"]) == 2

    state = client.post("/api/personality", json={"personality": "stockfish", "params": {"depth": 3}}).json()
    assert state["personality"]["key"] == "stockfish" and len(state["moves"]) == 2

    state = client.post("/api/retry", json={}).json()
    assert len(state["moves"]) == 1 and state["engine_to_move"] and not state["can_retry"]
    assert client.post("/api/retry", json={}).status_code == 409


def test_api_accepts_a_text_setting(fake_analyser):
    client = TestClient(create_app(fake_analyser, debug=True))
    state = client.post("/api/personality", json={"personality": "dubot_v2",
                                                  "params": {"theory_book": "solide", "theory_mix": 1}}).json()
    assert state["personality"]["params"]["theory_book"] == "solide"


def test_create_casts_params_to_declared_types():
    p = personalities.create("hustler", tolerance=149.6, naivety="15")
    assert p.tolerance == 150 and isinstance(p.tolerance, int) and p.naivety == 15.0


def test_state_history_keeps_ai_reasoning(fake_analyser):
    game = Game(personalities.create("casual"), chess.WHITE)
    game.play_human("e2e4")
    game.play_engine(fake_analyser)
    history = game.state()["history"]
    assert [h["by"] for h in history] == ["human", "ai"]
    assert history[0]["decision"] is None and history[1]["decision"]["san"] == history[1]["san"]
    assert history[0]["fen"].startswith("rnbqkbnr/pppppppp/8/8/4P3")
    assert history[1]["last_move"] == [history[1]["uci"][:2], history[1]["uci"][2:4]]


def test_debug_export_records_settings_per_ai_move(fake_analyser):
    from fun_fish.game import Game

    game = Game(personalities.create("casual", rng=random.Random(0)), human_color=chess.WHITE)
    game.play_human("e2e4")
    game.play_engine(fake_analyser)
    data = game.debug_export("test")
    assert data["format"] == "fun_fish-debug/1" and len(data["moves"]) == 2
    human, ai = data["moves"]
    assert human["by"] == "human" and "params" not in human
    assert ai["by"] == "ai" and ai["personality"] == "casual" and "max_loss" in ai["params"]
    assert ai["decision"]["san"] == ai["san"] and ai["fen_before"] == chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1").fen()


def test_play_mode_offers_only_the_public_agents_with_their_settings(fake_analyser):
    from fun_fish.personalities import PUBLIC_PERSONALITIES

    client = TestClient(create_app(fake_analyser))
    info = client.get("/api/info").json()
    assert info["mode"] == "play" and [p["key"] for p in info["personalities"]] == list(PUBLIC_PERSONALITIES)
    assert client.get("/api/state").json()["personality"]["key"] == "fun_fish_2_theorie"  # celui du bot Lichess
    assert client.post("/api/new", json={"personality": "blitz", "color": "white"}).status_code == 400
    state = client.post("/api/new", json={"personality": "fun_fish", "color": "white",
                                          "params": {"fun_budget": 0}}).json()
    assert state["personality"]["params"]["fun_budget"] == 270  # réglages retenus, sans retouche
