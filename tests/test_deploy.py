"""deploy/update.sh avec des doubles de git, pip, curl et systemd : jamais de redémarrage pendant une
partie, rien ne change si les tests échouent, retour arrière complet (lien et unité systemd)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "update.sh"
pytestmark = pytest.mark.skipif(sys.platform != "linux" or not shutil.which("bash"), reason="script serveur Linux")

STUBS = {
    # runuser -u funfish -- env HOME=… commande… : on exécute la commande telle quelle
    "runuser": 'while [ "$1" != "--" ]; do shift; done; shift; exec "$@"',
    "chown": "exit 0",
    "install": r'''args=(); while [ $# -gt 0 ]; do case "$1" in -o|-g) shift 2;; *) args+=("$1"); shift;; esac; done
[ "${STUB_FAIL_UNIT:-0}" = 1 ] && [ "${args[-1]}" = "$FUN_FISH_UNIT_DIR/fun-fish-bot.service" ] && exit 23
exec /usr/bin/install "${args[@]}"''',
    "sleep": "exit 0",
    # git clone -q --depth 1 --branch TAG REPO DIR : les sources d'une fausse version
    "git": r'''dir="${@: -1}"; mkdir -p "$dir/deploy" && echo "unit $STUB_TAG" > "$dir/deploy/fun-fish-bot.service" \
&& echo "crazy $STUB_TAG" > "$dir/deploy/fun-fish-crazy.service" \
&& echo "dubot $STUB_TAG" > "$dir/deploy/fun-fish-dubot.service" \
&& echo "capabot $STUB_TAG" > "$dir/deploy/fun-fish-capabot.service"''',
    # python3 -m venv DIR : un faux interpréteur, et des lanceurs (pip, puis fun-fish écrit par pip) dont
    # le shebang fige le chemin ABSOLU de l'interpréteur à la création, comme les vrais : un virtualenv
    # déplacé après coup ne démarre plus.
    "python3": r"""if [ "$1 $2" = "-m venv" ]; then
  mkdir -p "$3/bin"; abs=$(cd "$3" && pwd)
  cat > "$abs/bin/python" <<STUB
#!/bin/sh
case "\$*" in *"-m pytest"*) exit \${STUB_TESTS_FAIL:-0};; esac
case "\$*" in *position_style*) echo "style" >> "$abs/../style.log"; exit \${STUB_STYLE_FAIL:-0};; esac
case "\$1" in */pip)
  case "\$*" in *--upgrade*) exit 0;; esac
  printf '#!%s/bin/python\n' "$abs" > "$abs/bin/fun-fish"; chmod +x "$abs/bin/fun-fish";;
esac
exit 0
STUB
  printf '#!%s/bin/python\n' "$abs" > "$abs/bin/pip"
  chmod +x "$abs/bin/python" "$abs/bin/pip"
  exit 0
fi
exec "$REAL_PYTHON" "$@"
""",
    # chaque compte a ses parties : STUB_PLAYING pour FunFishBot, STUB_PLAYING_CRAZY pour CrazyFishBot
    "curl": r'''case "$*" in *jeton-crazy*) n=${STUB_PLAYING_CRAZY:-0};; *) n=$STUB_PLAYING;; esac
case "$n" in
  fail) exit 22;;
  *) echo "{\"nowPlaying\": [$(seq -s, 1 "$n" | sed 's/[0-9]*/{}/g')]}";;
esac''',
    "systemctl": r'''echo "$*" >> "$STUB_LOG"
[ "$1" = "${STUB_SYSTEMCTL_FAIL:-}" ] && exit 1
[ "$1" = restart ] && [ -e "$FUN_FISH_CONFIG/drain" ] && echo "vidange pendant la relance de $2" >> "$STUB_LOG"
[ "$1 $2" = "restart ${STUB_RESTART_FAIL:-}" ] && exit 1
case "$1" in is-active) [ "${STUB_ACTIVE:-1}" = 1 ];; show) echo "invocation-$RANDOM";; *) exit 0;; esac''',
    "journalctl": '[ "${STUB_CONNECT:-1}" = 1 ] && echo "connecté en tant que FunFishBot"; exit 0',
}


@pytest.fixture
def server(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        (bin_dir / name).write_text("#!/usr/bin/env bash\n" + body + "\n")
        (bin_dir / name).chmod(0o755)
    base, config = tmp_path / "srv", tmp_path / "config"
    config.mkdir()
    (config / "lichess_bot_token").write_text("jeton")
    units = tmp_path / "units"
    units.mkdir()
    unit = units / "fun-fish-bot.service"
    unit.write_text("unit ancienne\n")
    old = base / "releases" / "v0.1.0"
    old.mkdir(parents=True)
    (base / "current").symlink_to(old)

    def run(tag="v0.2.0", **stub):
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_PYTHON": sys.executable,
               "FUN_FISH_BASE": str(base), "FUN_FISH_UNIT_DIR": str(units), "FUN_FISH_CONFIG": str(config),
               "FUN_FISH_WAIT_GAMES": "60", "STUB_LOG": str(tmp_path / "systemctl.log"), "STUB_TAG": tag,
               "STUB_PLAYING": "0", **{k.upper(): str(v) for k, v in stub.items()}}
        result = subprocess.run(["bash", str(SCRIPT), tag], env=env, capture_output=True, text=True)
        log = (tmp_path / "systemctl.log").read_text() if (tmp_path / "systemctl.log").exists() else ""
        return result, log

    return run, base, unit, config


def test_switches_when_no_game_is_running(server):
    run, base, unit, config = server
    result, log = run()
    assert result.returncode == 0, result.stderr
    assert os.readlink(base / "current") == str(base / "releases" / "v0.2.0")
    assert unit.read_text() == "unit v0.2.0\n" and "restart fun-fish-bot" in log
    assert (base / "DEPLOYED").read_text().startswith("v0.2.0") and not (config / "drain").exists()


@pytest.mark.parametrize("playing", ["fail", "1"])  # Lichess injoignable, ou une partie qui dure
def test_never_restarts_while_games_may_be_running(server, playing):
    run, base, unit, config = server
    result, log = run(stub_playing=playing)
    assert result.returncode != 0 and "bascule annulée" in result.stderr
    assert "restart" not in log and unit.read_text() == "unit ancienne\n"
    assert os.readlink(base / "current").endswith("v0.1.0") and not (config / "drain").exists()


def test_failing_tests_change_nothing(server):
    run, base, unit, config = server
    result, log = run(stub_tests_fail=1)
    assert result.returncode != 0 and "Tests en échec" in result.stderr
    assert log == "" and unit.read_text() == "unit ancienne\n"
    assert os.readlink(base / "current").endswith("v0.1.0") and not (config / "drain").exists()


def test_dubot_model_is_fetched_before_the_switch(server):
    run, base, unit, config = server
    result, log = run(stub_style_fail=1)  # 77 Mo introuvables : on ne bascule pas
    assert result.returncode != 0 and "Goût des positions" in result.stderr
    assert log == "" and os.readlink(base / "current").endswith("v0.1.0")
    result, log = run()
    assert result.returncode == 0, result.stderr
    assert (base / "releases" / "v0.2.0" / "style.log").read_text() == "style\n"  # demandé pendant l'installation


def test_launcher_works_from_final_location(server):
    run, base, unit, config = server
    result, _ = run()
    launcher = base / "releases" / "v0.2.0" / ".venv" / "bin" / "fun-fish"
    assert result.returncode == 0, result.stderr
    assert subprocess.run([str(launcher), "--help"]).returncode == 0  # chemin de l'interpréteur toujours valide


@pytest.mark.parametrize("failure", [{"stub_fail_unit": 1}, {"stub_systemctl_fail": "daemon-reload"},
                                     {"stub_systemctl_fail": "enable"}, {"stub_systemctl_fail": "restart"}])
def test_any_switch_failure_rolls_back(server, failure):
    run, base, unit, config = server
    result, log = run(**failure)
    assert result.returncode != 0 and "retour à" in result.stderr
    assert os.readlink(base / "current").endswith("v0.1.0")
    assert unit.read_text() == "unit ancienne\n" and not (config / "drain").exists()
    assert "restart fun-fish-bot" in log  # l'ancienne version est relancée (ou du moins tentée)


def test_rollback_restores_link_and_unit(server):
    run, base, unit, config = server
    result, log = run(stub_connect=0)  # la nouvelle version ne se connecte pas
    assert result.returncode != 0 and "retour à" in result.stderr
    assert os.readlink(base / "current").endswith("v0.1.0")
    assert unit.read_text() == "unit ancienne\n" and log.count("restart fun-fish-bot") == 2
    assert not (base / "DEPLOYED").exists() and not (config / "drain").exists()


def test_first_install_does_not_wait(server):
    run, base, unit, config = server
    (base / "current").unlink()
    result, log = run(stub_active=0, stub_playing="fail")  # pas encore de bot : rien à attendre
    assert result.returncode == 0, result.stderr
    assert os.readlink(base / "current").endswith("v0.2.0")


def test_second_bot_without_token_is_installed_but_not_started(server):
    run, base, unit, config = server
    result, log = run()
    assert result.returncode == 0, result.stderr
    assert (unit.parent / "fun-fish-crazy.service").read_text() == "crazy v0.2.0\n"
    assert "fun-fish-crazy" not in log and "pas encore de jeton" in result.stdout


@pytest.mark.parametrize("bot", ["dubot", "capabot"])
def test_style_bots_are_installed_and_only_started_with_their_token(server, bot):
    run, base, unit, config = server
    result, log = run()
    assert result.returncode == 0, result.stderr
    assert (unit.parent / f"fun-fish-{bot}.service").read_text() == f"{bot} v0.2.0\n"
    assert f"fun-fish-{bot}" not in log
    (config / f"{bot}_token").write_text("jeton")
    result, log = run()
    assert result.returncode == 0, result.stderr
    assert f"enable -q fun-fish-{bot}" in log and f"restart fun-fish-{bot}" in log


def test_second_bot_with_token_is_enabled_and_restarted(server):
    run, base, unit, config = server
    (config / "crazyfish_bot_token").write_text("jeton-crazy")
    result, log = run()
    assert result.returncode == 0, result.stderr
    assert "enable -q fun-fish-crazy" in log and "restart fun-fish-crazy" in log and "restart fun-fish-bot" in log
    # la vidange couvre chaque relance : aucun bot relancé n'accepte de partie avant la fin de la bascule
    assert log.count("vidange pendant la relance") == log.count("restart fun-fish") == 2
    assert not (config / "drain").exists()


def test_waits_for_games_of_every_bot(server):
    run, base, unit, config = server
    (config / "crazyfish_bot_token").write_text("jeton-crazy")
    for playing in ("1", "fail"):  # une partie, ou Lichess muet, sur le second compte seulement
        result, log = run(stub_playing_crazy=playing)
        assert result.returncode != 0 and "bascule annulée" in result.stderr
        assert "restart" not in log and os.readlink(base / "current").endswith("v0.1.0")


def test_second_bot_failure_rolls_back_both(server):
    run, base, unit, config = server
    (config / "crazyfish_bot_token").write_text("jeton-crazy")
    result, log = run(stub_restart_fail="fun-fish-crazy")
    assert result.returncode != 0 and "retour à" in result.stderr
    assert os.readlink(base / "current").endswith("v0.1.0") and unit.read_text() == "unit ancienne\n"
    assert not (unit.parent / "fun-fish-crazy.service").exists()  # il n'existait pas avant : retiré
    assert log.count("restart fun-fish-bot") == 2  # le premier bot revient sur l'ancienne version
    assert log.count("vidange pendant la relance") == 3 and not (config / "drain").exists()  # retour arrière compris


def test_drain_covers_restarts_even_when_no_bot_was_running(server):
    run, base, unit, config = server
    (config / "crazyfish_bot_token").write_text("jeton-crazy")
    result, log = run(stub_active=0, stub_restart_fail="fun-fish-crazy")
    assert result.returncode != 0 and "retour à" in result.stderr
    assert log.count("vidange pendant la relance") == log.count("restart fun-fish") == 3
    assert not (config / "drain").exists()
