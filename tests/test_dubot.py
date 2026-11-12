"""DuBot : Fun Fish 2 et le goût des positions d'attaque (`fun_fish.position_style`)."""

from types import SimpleNamespace

import chess
import pytest

from fun_fish import personalities, position_style
from fun_fish.analysis import Line

ATTACK = "rnbqk2r/ppp2p1p/5n2/3Pb3/2B2ppP/8/PPPP2P1/RNBQR1K1 b kq - 1 9"  # Marshall, après 9.Te1
SOLID = "r2r2k1/pbq1bppp/1pn1pn2/2p5/2p5/1P1PPNP1/PB1NQPBP/2R2RK1 w - - 0 13"  # Karpov, après 12…Fb7


def line(uci: str, score: int = 0) -> Line:
    return Line(chess.Move.from_uci(uci), score, None, None, (), 1)


def bonuses(monkeypatch, values, **params):
    monkeypatch.setattr(position_style, "logits", lambda boards: values)
    bot = personalities.create("dubot", **params)
    bot._creative = {}
    sound = [line("e2e4"), line("d2d4"), line("g1f3")]
    bonus, style = {}, SimpleNamespace(reasons={})
    bot.more_bonus(chess.Board(), sound, sound[0], bonus, style)
    return bonus, style


def test_registered_with_its_settings():
    specs = {s["name"]: s["default"] for s in personalities.PERSONALITIES["dubot"].param_specs()}
    assert specs["w_pos"] == 4.0 and specs["w_repertoire"] == 0
    bot = personalities.create("dubot")
    assert bot.w_pos == 4.0 and bot.w_repertoire == 0


def test_bonus_follows_the_position_taste_around_the_mean(monkeypatch):
    bonus, style = bonuses(monkeypatch, [1.0, 0.0, -1.0])
    assert bonus[chess.Move.from_uci("e2e4")] == pytest.approx(4.0)
    assert bonus[chess.Move.from_uci("d2d4")] == pytest.approx(0.0)
    assert bonus[chess.Move.from_uci("g1f3")] == pytest.approx(-4.0)
    assert "joueur d'attaque" in style.reasons[chess.Move.from_uci("e2e4")]
    assert chess.Move.from_uci("g1f3") not in style.reasons


def test_without_style_model_it_plays_like_fun_fish_2(monkeypatch):
    bonus, style = bonuses(monkeypatch, None)
    assert bonus == {} and style.reasons == {}


def test_weight_zero_asks_nothing(monkeypatch):
    def boom(boards):
        raise AssertionError("pas d'appel au modèle avec w_pos = 0")
    monkeypatch.setattr(position_style, "logits", boom)
    bot = personalities.create("dubot", w_pos=0)
    bot._creative = {}
    bot.more_bonus(chess.Board(), [line("e2e4"), line("d2d4")], line("e2e4"), {}, SimpleNamespace(reasons={}))


@pytest.mark.skipif(not position_style.ready(download=False), reason="modèle de style absent")
def test_real_model_tells_attack_from_solid():
    attack, solid = position_style.logits([chess.Board(ATTACK), chess.Board(SOLID)])
    assert attack > 1.0 and solid < -1.0
