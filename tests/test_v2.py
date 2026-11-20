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
    dubot = personalities.create("dubot")
    assert (v2.theory_rate, v2.theory_moves, v2.theory_mix) == (0.8, 8, 0.8)  # au moins 4 coups prévus une fois sur deux
    assert (dubot.theory_rate, dubot.theory_moves, dubot.theory_mix) == (0.35, 6, 0.5)  # DuBot ne change pas
    specs = {s["name"]: s["default"] for s in personalities.PERSONALITIES["dubot_v2"].param_specs()}
    assert (specs["theory_rate"], specs["theory_moves"], specs["theory_mix"]) == (0.8, 8, 0.8)  # le panneau aussi


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

    seen.clear()  # même avec une tolérance déjà basse, les coups fun restent plafonnés
    bot = personalities.create("dubot_v2", theory_rate=0, tolerance=100)
    bot.choose_move(chess.Board(), None)
    assert seen == [(100, 100, 100)]


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


def test_a_flatter_draw_varies_the_model_book_lines():
    """theory_spread < 1 : moins de Gambit Dame après 1. d4 d5, et jamais un coup joué moins de BOOK_MIN fois."""
    board = chess.Board()
    for san in ("d4", "d5"):
        board.push_san(san)
    counts = theory.model_book("solide")[board.epd()]
    rare = {board.san(chess.Move.from_uci(u)) for u, n in counts.items() if n < theory.BOOK_MIN}

    def draws(spread):
        rng = random.Random(0)
        return [board.san(theory.book_move(board, "solide", rng, spread)) for _ in range(2000)]

    usual, varied = draws(1.0), draws(0.5)
    assert varied.count("c4") < usual.count("c4") - 200 and varied.count("Nf3") > usual.count("Nf3") + 200
    assert rare and not rare & set(varied)


def test_capabot_has_its_own_rapid_settings():
    tuned = personalities.speed_params("capabot_v2", "rapid")
    assert tuned == {"theory_rate": 0.7, "theory_spread": 0.5, "trap_min_error": 0.2}
    capa = personalities.create("capabot_v2", **tuned)
    assert (capa.theory_rate, capa.theory_spread, capa.trap_min_error) == (0.7, 0.5, 0.2)
    assert personalities.speed_params("capabot_v2", "blitz") == {}
    assert personalities.speed_params("dubot_v2", "rapid") == {} and personalities.speed_params("capabot_v2", None) == {}
    default = personalities.create("capabot_v2")
    assert (default.theory_rate, default.theory_spread, default.trap_min_error) == (0.35, 1.0, 0.0)


def test_the_personality_draws_from_the_book_with_its_spread(monkeypatch):
    seen = []
    monkeypatch.setattr(theory, "book_move", lambda board, name, rng, spread=1.0: seen.append(spread))
    bot = personalities.create("capabot_v2", rng=random.Random(3), theory_rate=1.0, theory_spread=0.5)
    bot.theory_move(chess.Board())
    assert seen == [0.5]
