"""Maia-2 : probabilité qu'un humain d'un Elo donné joue chaque coup.

Le modèle couvre les tranches < 1100, 1100-1199, ..., 1900-1999 et ≥ 2000. Deux moteurs :

- **ONNX** (`onnxruntime`, 24 Mo) : le modèle exporté (93 Mo) et sa liste de coups, dans
  le cache de fun_fish. Pas besoin de PyTorch ; c'est aussi le plus rapide.
- **PyTorch** (`maia2`, extra `sim`) : les poids d'origine (280 Mo), téléchargés par maia2.

Les fichiers ONNX sont téléchargés depuis la release GitHub du projet (vérifiés par SHA-256)
quand onnxruntime est installé ; `fun-fish maia-export` les produit aussi à partir de PyTorch.
Maia-2 : https://github.com/CSSLab/maia2 (licence MIT).

Maia-2 n'a pas été entraîné sur les 10 premiers demi-coups des parties : dans ces positions,
`Maia` répond avec le livre d'ouverture humain (`fun_fish.book`) quand il les connaît.
"""

from __future__ import annotations

import contextlib
import io
import json
import random
import threading
from collections import OrderedDict
from pathlib import Path

import chess

from fun_fish.book import PLIES as BOOK_PLIES
from fun_fish.engines import cache_dir

RELEASE_URL = "https://github.com/vespertine/fun_fish/releases/download/maia2-onnx-v1"
SHA256 = {
    "rapid_model.onnx": "ac1842eac72070cb9771895fc2d32a5f926b0aefdd9ae64244f534ce05d7f7f8",
    "rapid_moves.json": "f0dd3915826cb6c3dffb3d2b5ba0bb6ab448e11ed4d2a881bea5005f006eda0c",
}
PIECES = (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN, chess.KING)


def model_dir() -> Path:
    return cache_dir() / "maia2"


def onnx_files(kind: str = "rapid") -> tuple[Path, Path]:
    return model_dir() / f"{kind}_model.onnx", model_dir() / f"{kind}_moves.json"


def _has(module: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(module) is not None


def onnx_ready(kind: str = "rapid", download: bool = True) -> bool:
    """onnxruntime est installé et les fichiers ONNX sont là (téléchargés au besoin)."""
    if not _has("onnxruntime"):
        return False
    if all(f.exists() for f in onnx_files(kind)):
        return True
    if not download:
        return False
    try:
        download_onnx(kind)
    except OSError as exc:
        import sys

        print(f"Téléchargement de Maia-2 (ONNX) impossible : {exc}", file=sys.stderr)
        return False
    return True


def download_onnx(kind: str = "rapid") -> None:
    import hashlib
    import sys

    from fun_fish.engines import _download

    model_dir().mkdir(parents=True, exist_ok=True)
    for dest in onnx_files(kind):
        if dest.exists():
            continue
        expected = SHA256.get(dest.name)
        if expected is None:
            raise OSError(f"pas de fichier publié pour {dest.name}")
        print(f"Téléchargement de Maia-2 : {dest.name}", file=sys.stderr)
        tmp = dest.with_suffix(dest.suffix + ".part")
        _download(f"{RELEASE_URL}/{dest.name}", tmp, lambda m: print(m, file=sys.stderr))
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        if digest != expected:
            tmp.unlink()
            raise OSError(f"empreinte SHA-256 inattendue pour {dest.name}")
        tmp.replace(dest)


def torch_ready() -> bool:
    return _has("maia2") and _has("torch")


def available(kind: str = "rapid") -> bool:
    """Maia-2 est-il utilisable (ONNX, téléchargé au besoin, ou PyTorch) ?"""
    return onnx_ready(kind) or torch_ready()


def elo_category(elo: int) -> int:
    """Tranche d'Elo de Maia-2 : 0 pour < 1100, 1 à 9 pour 1100-1999, 10 pour ≥ 2000."""
    if elo < 1100:
        return 0
    if elo >= 2000:
        return 10
    return 1 + (elo - 1100) // 100


def encode(board: chess.Board):
    """Les 18 plans 8×8 de Maia-2 (pièces, trait, roques, prise en passant), en numpy."""
    import numpy as np

    planes = np.zeros((18, 8, 8), dtype=np.float32)
    for i, piece in enumerate(PIECES):
        for color, offset in ((chess.WHITE, 0), (chess.BLACK, 6)):
            for square in board.pieces(piece, color):
                planes[i + offset, square // 8, square % 8] = 1.0
    if board.turn == chess.WHITE:
        planes[12] = 1.0
    rights = (board.has_kingside_castling_rights(chess.WHITE), board.has_queenside_castling_rights(chess.WHITE),
              board.has_kingside_castling_rights(chess.BLACK), board.has_queenside_castling_rights(chess.BLACK))
    for i, right in enumerate(rights):
        if right:
            planes[13 + i] = 1.0
    if board.ep_square is not None and board.has_legal_en_passant():  # comme la FEN que lit maia2
        planes[17, board.ep_square // 8, board.ep_square % 8] = 1.0
    return planes


class _OnnxNet:
    def __init__(self, kind: str, threads: int):
        import onnxruntime as ort

        model, moves = onnx_files(kind)
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model), options, providers=["CPUExecutionProvider"])
        self.moves: list[str] = json.loads(moves.read_text())

    def logits(self, boards, elo_self, elo_oppo):
        return self.session.run(["logits"], {"boards": boards, "elo_self": elo_self, "elo_oppo": elo_oppo})[0]


class _TorchNet:
    def __init__(self, kind: str, threads: int):
        import torch
        from maia2 import inference, model

        torch.set_num_threads(threads)
        with contextlib.redirect_stdout(io.StringIO()):
            self.model = model.from_pretrained(type=kind, device="cpu", save_root=str(model_dir()))
        self.model.eval()
        reversed_moves = inference.prepare()[2]
        self.moves = [reversed_moves[i] for i in range(len(reversed_moves))]
        self.torch = torch

    def logits(self, boards, elo_self, elo_oppo):
        t = self.torch
        with t.no_grad():
            return self.model(t.from_numpy(boards), t.from_numpy(elo_self), t.from_numpy(elo_oppo))[0].numpy()


def export_onnx(kind: str = "rapid") -> tuple[Path, Path]:
    """Exporte le modèle PyTorch en ONNX, avec sa liste de coups (nécessite l'extra `sim`)."""
    import torch

    net = _TorchNet(kind, threads=4)
    module = net.model.module if hasattr(net.model, "module") else net.model
    model, moves = onnx_files(kind)
    sample = torch.from_numpy(encode(chess.Board())).unsqueeze(0).repeat(2, 1, 1, 1)
    elos = torch.tensor([3, 3])
    axes = {name: {0: "n"} for name in ("boards", "elo_self", "elo_oppo", "logits", "aux", "value")}
    with contextlib.redirect_stdout(io.StringIO()):
        torch.onnx.export(module, (sample, elos, elos), str(model), input_names=["boards", "elo_self", "elo_oppo"],
                          output_names=["logits", "aux", "value"], dynamic_axes=axes, opset_version=17, dynamo=False)
    moves.write_text(json.dumps(net.moves))
    return model, moves


def _book():
    from fun_fish import book

    return book.shared()

_shared: dict[str, "Maia"] = {}
_shared_lock = threading.Lock()


def shared(kind: str = "rapid", threads: int = 1) -> "Maia":
    """Un seul Maia par processus et par type : le chargement prend quelques secondes."""
    with _shared_lock:
        if kind not in _shared:
            _shared[kind] = Maia(kind=kind, threads=threads)
        return _shared[kind]


class Maia:
    CACHE_SIZE = 20_000

    def __init__(self, kind: str = "rapid", threads: int = 1, backend: str | None = None):
        backend = backend or ("onnx" if onnx_ready(kind) else "torch")
        if backend == "onnx":
            self._net = _OnnxNet(kind, threads)
        elif torch_ready():
            self._net = _TorchNet(kind, threads)
        else:  # pragma: no cover - dépend de l'installation
            raise RuntimeError("Maia-2 indisponible : pip install -e '.[sim]', ou les fichiers ONNX (voir README).")
        self.backend = backend
        self._index = {uci: i for i, uci in enumerate(self._net.moves)}
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple, dict[chess.Move, float]] = OrderedDict()

    def probabilities(self, board: chess.Board, elo: int, opponent_elo: int | None = None) -> dict[chess.Move, float]:
        """Probabilité de chaque coup légal pour un joueur de `elo` face à un adversaire de `opponent_elo`."""
        return self.probabilities_many([board], elo, opponent_elo)[0]

    def probabilities_many(self, boards: list[chess.Board], elo: int,
                           opponent_elo: int | None = None) -> list[dict[chess.Move, float]]:
        """Comme `probabilities`, pour plusieurs positions en un seul passage du réseau (bien plus rapide)."""
        opponent_elo = opponent_elo if opponent_elo is not None else elo
        # Maia ne voit que la tranche d'Elo et la position (pas les compteurs de coups) : 600 et 1000
        # Elo, ou une même position atteinte plus tard, partagent le même calcul. En ouverture, le livre
        # a ses propres tranches et dépend du demi-coup : clé exacte.
        keys = [(b.fen(), elo, opponent_elo) if b.ply() <= BOOK_PLIES else
                (b.epd(), elo_category(elo), elo_category(opponent_elo)) for b in boards]
        opening = _book()
        with self._lock:
            if opening is not None:  # en ouverture, ce que jouent vraiment les humains (voir book.py)
                for k, b in zip(keys, boards):
                    if k not in self._cache and b.ply() <= BOOK_PLIES:
                        found = opening.probabilities(b, elo)
                        if found is not None:
                            self._cache[k] = found
            missing = {k: b for k, b in zip(keys, boards) if k not in self._cache and not b.is_game_over()}
            if missing:
                for key, result in zip(missing, self._compute(list(missing.values()), elo, opponent_elo)):
                    self._cache[key] = result
                while len(self._cache) > self.CACHE_SIZE:
                    self._cache.popitem(last=False)
            results = []
            for key in keys:
                result = self._cache.get(key, {})
                if key in self._cache:
                    self._cache.move_to_end(key)
                results.append(result)
            return results

    def _compute(self, boards: list[chess.Board], elo: int, opponent_elo: int) -> list[dict[chess.Move, float]]:
        import numpy as np

        # Maia raisonne toujours du point de vue des Blancs : on retourne l'échiquier aux Noirs.
        seen = [b if b.turn == chess.WHITE else b.mirror() for b in boards]
        n = len(seen)
        logits = self._net.logits(np.stack([encode(b) for b in seen]),
                                  np.full(n, elo_category(elo), dtype=np.int64),
                                  np.full(n, elo_category(opponent_elo), dtype=np.int64))
        results = []
        for board, view, row in zip(boards, seen, logits):
            legal = [(m, self._index.get(m.uci())) for m in view.legal_moves]
            legal = [(m, i) for m, i in legal if i is not None]
            values = np.array([row[i] for _, i in legal], dtype=np.float64)
            probs = np.exp(values - values.max())
            probs /= probs.sum()
            result = {}
            for (move, _), p in zip(legal, probs):
                if board.turn == chess.BLACK:
                    move = chess.Move(chess.square_mirror(move.from_square), chess.square_mirror(move.to_square),
                                      move.promotion)
                result[move] = float(p)
            results.append(result)
        return results


class MaiaPlayer:
    """Adversaire simulé : tire son coup selon les probabilités de Maia (pas toujours le plus probable)."""

    def __init__(self, maia: Maia, elo: int, rng: random.Random | None = None, temperature: float = 1.0):
        self.maia = maia
        self.elo = elo
        self.rng = rng or random.Random()
        self.temperature = temperature
        self.name = f"maia-{elo}"

    def choose(self, board: chess.Board) -> chess.Move:
        probs = self.maia.probabilities(board, self.elo)
        moves = list(probs)
        weights = [probs[m] ** (1 / self.temperature) for m in moves] if self.temperature > 0 else None
        if weights is None:
            return max(moves, key=probs.get)
        return self.rng.choices(moves, weights=weights, k=1)[0]
