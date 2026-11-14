"""DuBotV2 et CapaBotV2 : théorie des maîtres-modèles, tolérance d'ouverture, réglages."""

import random

import chess

from fun_fish import personalities, theory


def test_registered_with_their_settings():
    v2 = personalities.create("dubot_v2")
    assert (v2.opponent_elo, v2.tolerance, v2.opening_tolerance, v2.w_pos, v2.theory_book) == (1500, 200, 100, 3.0, "attaque")
    capa = personalities.create("capabot_v2")
    assert (capa.opponent_elo, capa.tolerance, capa.w_pos, capa.theory_book, capa.theory_mix) == (2000, 150, -4.0, "solide", 1.0)
    specs = {s["name"]: s["default"] for s in personalities.PERSONALITIES["capabot_v2"].param_specs()}
    assert specs["theory_book"] == "solide" and specs["opponent_elo"] == 2000
    assert personalities.create("dubot").opening_tolerance == 0  # DuBot ne change pas


def test_opening_tolerance_caps_fun_moves_too_then_restores(monkeypatch):
    bot = personalities.create("dubot_v2", theory_rate=0)
    seen = []
    monkeypatch.setattr(type(bot), "_choose_move", lambda self, board, analyser: seen.append(
        (self.tolerance, self.fun_budget, self.creative_max)))
    board = chess.Board()
    board.push_san("d4")
    bot.choose_move(board, None)
    for san in ("e5", "dxe5", "Nc6", "Nf3", "Qe7"):
        board.push_san(san)
    bot.choose_move(board, None)  # 6e demi-coup : ouverture finie
    assert seen == [(100, 100, 100), (200, 270, 120)]
    assert (bot.tolerance, bot.fun_budget, bot.creative_max) == (200, 270, 120)


def test_the_model_book_only_holds_what_the_masters_played():
    board = chess.Board()
    board.push_san("d4")
    rng = random.Random(0)
    moves = {board.san(theory.book_move(board, "attaque", rng)) for _ in range(200)}
    assert "e5" not in moves and {"Nf6", "d5"} <= moves  # jamais le gambit Englund
    board = chess.Board("8/8/8/4k3/8/8/8/4K3 w - - 0 1")
    assert theory.book_move(board, "attaque", rng) is None


def test_a_theory_game_follows_the_model_book_then_plays_freely(monkeypatch):
    bot = personalities.create("capabot_v2", rng=random.Random(3), theory_rate=1.0, theory_moves=1)
    board = chess.Board()
    played = []
    for _ in range(3):
        found = bot.theory_move(board)
        played.append(found and found[1])
        move = found[0] if found else next(iter(board.legal_moves))
        board.push(move)
        board.push(next(iter(board.legal_moves)))
    assert played == ["ouverture des maîtres-modèles", None, None]
