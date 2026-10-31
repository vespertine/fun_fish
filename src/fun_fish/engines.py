"""Localisation et téléchargement du binaire Stockfish.

Ordre de recherche : chemin explicite, variable FUN_FISH_STOCKFISH, version
épinglée déjà téléchargée dans le cache utilisateur, `stockfish` dans le PATH,
puis téléchargement de la release officielle épinglée.
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

STOCKFISH_RELEASE = "sf_19"
_BASE_URL = "https://github.com/official-stockfish/Stockfish/releases/download"

Log = Callable[[str], None]


def _stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class StockfishNotFound(RuntimeError):
    pass


def cache_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "fun_fish"


def asset_stem() -> str:
    """Nom de la release officielle adaptée à l'OS et à l'architecture courants."""
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    x86 = machine in ("x86_64", "amd64")
    if sys.platform == "darwin":
        return "stockfish-macos-universal"
    if sys.platform == "win32":
        if arm:
            return "stockfish-windows-arm64-universal"
        if x86:
            return "stockfish-windows-x86-64-universal"
    if sys.platform.startswith("linux"):
        if arm:
            return "stockfish-linux-arm64-universal"
        if x86:
            return "stockfish-linux-x86-64-universal"
        if machine == "riscv64":
            return "stockfish-linux-riscv64-universal"
    raise StockfishNotFound(
        f"Pas de binaire Stockfish officiel pour {sys.platform}/{machine} : "
        "installez Stockfish et passez son chemin avec --stockfish."
    )


def _is_windows() -> bool:
    return sys.platform == "win32"


def _archive_name(stem: str) -> str:
    return stem + (".zip" if _is_windows() else ".tar.gz")


def _binary_name(stem: str) -> str:
    return stem + (".exe" if _is_windows() else "")


def installed_path() -> Path:
    return cache_dir() / STOCKFISH_RELEASE / _binary_name(asset_stem())


def find_stockfish(explicit: str | os.PathLike | None = None, *, download: bool = True,
                   log: Log = _stderr) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise StockfishNotFound(f"Stockfish introuvable : {path}")
        return path
    env = os.environ.get("FUN_FISH_STOCKFISH")
    if env:
        return find_stockfish(env, download=False)
    try:
        cached = installed_path()
    except StockfishNotFound:
        cached = None
    if cached is not None and cached.is_file():
        return cached
    which = shutil.which("stockfish")
    if which:
        return Path(which)
    if not download:
        raise StockfishNotFound("Stockfish introuvable (ni en cache, ni dans le PATH).")
    return download_stockfish(log=log)


def download_stockfish(*, log: Log = _stderr) -> Path:
    stem = asset_stem()
    archive = _archive_name(stem)
    url = f"{_BASE_URL}/{STOCKFISH_RELEASE}/{archive}"
    target_dir = cache_dir() / STOCKFISH_RELEASE
    target_dir.mkdir(parents=True, exist_ok=True)
    binary = target_dir / _binary_name(stem)

    log(f"Téléchargement de Stockfish ({STOCKFISH_RELEASE}) : {url}")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_archive = Path(tmp) / archive
        _download(url, tmp_archive, log)
        _extract_member(tmp_archive, binary.name, binary)
        try:
            _extract_member(tmp_archive, "Copying.txt", target_dir / "Copying.txt")
        except KeyError:
            pass
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log(f"Stockfish installé : {binary}")
    return binary


def _download(url: str, dest: Path, log: Log) -> None:
    with urllib.request.urlopen(url) as response, open(dest, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done, next_report = 0, 10
        while chunk := response.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total and done * 100 // total >= next_report:
                log(f"  {done * 100 // total} %")
                next_report += 10


def _extract_member(archive: Path, basename: str, dest: Path) -> None:
    """Extrait le fichier de l'archive dont le nom de base est `basename` (sans suivre ses chemins)."""
    tmp_dest = dest.with_suffix(dest.suffix + ".part")
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            name = next((n for n in zf.namelist() if Path(n).name == basename), None)
            if name is None:
                raise KeyError(basename)
            with zf.open(name) as src, open(tmp_dest, "wb") as out:
                shutil.copyfileobj(src, out)
    else:
        with tarfile.open(archive) as tf:
            member = next((m for m in tf.getmembers() if m.isfile() and Path(m.name).name == basename), None)
            if member is None:
                raise KeyError(basename)
            src = tf.extractfile(member)
            assert src is not None
            with src, open(tmp_dest, "wb") as out:
                shutil.copyfileobj(src, out)
    os.replace(tmp_dest, dest)
