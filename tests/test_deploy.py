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
    "install": 'args=(); while [ $# -gt 0 ]; do case "$1" in -o|-g) shift 2;; *) args+=("$1"); shift;; esac; done; exec /usr/bin/install "${args[@]}"',
    "sleep": "exit 0",
    "git": r'''# git clone -q --depth 1 --branch TAG REPO DIR : une fausse version installable
dir="${@: -1}"; mkdir -p "$dir/.venv/bin" "$dir/deploy"
echo "unit $STUB_TAG" > "$dir/deploy/fun-fish-bot.service"
for tool in pip fun-fish; do printf '#!/bin/sh\nexit 0\n' > "$dir/.venv/bin/$tool"; done
printf '#!/bin/sh\ncase "$*" in *pytest*) exit ${STUB_TESTS_FAIL:-0};; esac\nexit 0\n' > "$dir/.venv/bin/python"
chmod +x "$dir"/.venv/bin/*''',
    "python3": r'''[ "$1 $2" = "-m venv" ] && { mkdir -p "$3/.venv"; exit 0; }
exec "$REAL_PYTHON" "$@"''',
    "curl": r'''case "$STUB_PLAYING" in
  fail) exit 22;;
  *) echo "{\"nowPlaying\": [$(seq -s, 1 "$STUB_PLAYING" | sed 's/[0-9]*/{}/g')]}";;
esac''',
    "systemctl": r'''echo "$*" >> "$STUB_LOG"
case "$1" in is-active) [ "${STUB_ACTIVE:-1}" = 1 ];; *) exit 0;; esac''',
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
    unit = tmp_path / "unit"
    unit.write_text("unit ancienne\n")
    old = base / "releases" / "v0.1.0"
    old.mkdir(parents=True)
    (base / "current").symlink_to(old)

    def run(tag="v0.2.0", **stub):
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REAL_PYTHON": sys.executable,
               "FUN_FISH_BASE": str(base), "FUN_FISH_UNIT": str(unit), "FUN_FISH_CONFIG": str(config),
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
