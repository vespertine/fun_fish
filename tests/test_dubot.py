"""DuBot et CapaBot : Fun Fish 2 et le goût des positions d'attaque ou solides (`fun_fish.position_style`)."""

from types import SimpleNamespace

import chess
import pytest

from fun_fish import personalities, position_style
from fun_fish.analysis import Line

ATTACK = "rnbqk2r/ppp2p1p/5n2/3Pb3/2B2ppP/8/PPPP2P1/RNBQR1K1 b kq - 1 9"  # Marshall, après 9.Te1
SOLID = "r2r2k1/pbq1bppp/1pn1pn2/2p5/2p5/1P1PPNP1/PB1NQPBP/2R2RK1 w - - 0 13"  # Karpov, après 12…Fb7


def line(uci: str, score: int = 0) -> Line:
    return Line(chess.Move.from_uci(uci), score, None, None, (), 1)


def bonuses(monkeypatch, values, key="dubot", **params):
    monkeypatch.setattr(position_style, "logits", lambda boards: values)
    bot = personalities.create(key, **params)
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


def test_capabot_is_dubot_the_other_way_round(monkeypatch):
    specs = {s["name"]: s["default"] for s in personalities.PERSONALITIES["capabot"].param_specs()}
    assert specs["w_pos"] == -4.0 and specs["w_repertoire"] == 0
    bot = personalities.create("capabot")
    assert bot.w_pos == -4.0 and bot.w_repertoire == 0 and personalities.PERSONALITIES["dubot"]().w_pos == 4.0
    bonus, style = bonuses(monkeypatch, [1.0, 0.0, -1.0], key="capabot")
    assert bonus[chess.Move.from_uci("e2e4")] == pytest.approx(-4.0)
    assert bonus[chess.Move.from_uci("g1f3")] == pytest.approx(4.0)
    assert "joueur solide" in style.reasons[chess.Move.from_uci("g1f3")]
    assert chess.Move.from_uci("e2e4") not in style.reasons


@pytest.mark.parametrize("key", ["dubot", "capabot"])
def test_lichess_bot_greets_as_itself(key):
    from fun_fish import lichess_bot
    assert all(personalities.PERSONALITIES[key].name in text for text in lichess_bot.GREETINGS[key])


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


def test_no_download_during_a_game_once_the_bot_prepared(monkeypatch, tmp_path):
    from fun_fish.sim import maia
    monkeypatch.setattr(position_style, "model_path", lambda: tmp_path / "rapid_style.onnx")
    monkeypatch.setattr(position_style, "_shared", {})
    monkeypatch.setattr(maia, "_has", lambda module: True)
    monkeypatch.setattr(position_style, "DOWNLOAD_IN_GAME", True)

    def offline():
        raise OSError("réseau coupé")
    monkeypatch.setattr(position_style, "_download", offline)
    assert position_style.prepare() is False and position_style.DOWNLOAD_IN_GAME is False

    def forbidden():
        raise AssertionError("téléchargement en pleine partie")
    monkeypatch.setattr(position_style, "_download", forbidden)
    assert position_style.logits([chess.Board()]) is None


def test_prepare_fetches_the_model_before_any_game(monkeypatch, tmp_path):
    from fun_fish.sim import maia
    target = tmp_path / "rapid_style.onnx"
    monkeypatch.setattr(position_style, "model_path", lambda: target)
    monkeypatch.setattr(maia, "_has", lambda module: True)
    monkeypatch.setattr(position_style, "DOWNLOAD_IN_GAME", True)
    monkeypatch.setattr(position_style, "_download", lambda: target.write_bytes(b"modele"))
    assert position_style.prepare() is True and target.exists()


def test_lichess_bot_stops_before_any_game_without_its_model(monkeypatch):
    from fun_fish import cli, lichess_bot
    from fun_fish.personalities.dubot import DuBot
    monkeypatch.setattr(lichess_bot, "read_token", lambda *a: "jeton")
    monkeypatch.setattr(DuBot, "prepare", staticmethod(lambda: False))

    def no_bot(*a, **k):
        raise AssertionError("le bot ne doit pas démarrer")
    monkeypatch.setattr(lichess_bot, "Bot", no_bot)
    args = cli.build_parser().parse_args(["lichess-bot", "-p", "dubot", "--token-file", "x"])
    with pytest.raises(SystemExit, match="modèle de style indisponible"):
        args.func(args)
