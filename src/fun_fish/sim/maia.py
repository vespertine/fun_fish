"""Maia-2 : probabilité qu'un humain d'un Elo donné joue chaque coup.

Le modèle couvre les tranches < 1100, 1100-1199, ..., 1900-1999 et ≥ 2000. Les poids
(280 Mo) sont téléchargés une fois dans le cache de fun_fish, avec vérification SHA-256.
"""

from __future__ import annotations

import contextlib
import io
import random
import threading
from collections import OrderedDict

import chess

from fun_fish.engines import cache_dir


def available() -> bool:
    """Maia-2 (et PyTorch) sont-ils installés ?"""
    try:
        import maia2  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


_shared: dict[str, "Maia"] = {}
_shared_lock = threading.Lock()


def shared(kind: str = "rapid", threads: int = 1) -> "Maia":
    """Un seul Maia par processus et par type : le chargement prend quelques secondes et 300 Mo."""
    with _shared_lock:
        if kind not in _shared:
            _shared[kind] = Maia(kind=kind, threads=threads)
        return _shared[kind]


class Maia:
    CACHE_SIZE = 20_000

    def __init__(self, kind: str = "rapid", threads: int = 1):
        try:
            import torch
            from maia2 import inference, model
        except ImportError as exc:  # pragma: no cover - dépend de l'installation
            raise RuntimeError("Maia-2 n'est pas installé : pip install -e '.[sim]' (voir README).") from exc
        torch.set_num_threads(threads)
        with contextlib.redirect_stdout(io.StringIO()):
            self._model = model.from_pretrained(type=kind, device="cpu", save_root=str(cache_dir() / "maia2"))
        self._prepared = inference.prepare()
        from maia2.utils import board_to_tensor

        self._board_to_tensor = board_to_tensor
        self._model.eval()
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple, dict[chess.Move, float]] = OrderedDict()

    def probabilities(self, board: chess.Board, elo: int, opponent_elo: int | None = None) -> dict[chess.Move, float]:
        """Probabilité de chaque coup légal pour un joueur de `elo` face à un adversaire de `opponent_elo`."""
        return self.probabilities_many([board], elo, opponent_elo)[0]

    def probabilities_many(self, boards: list[chess.Board], elo: int,
                           opponent_elo: int | None = None) -> list[dict[chess.Move, float]]:
        """Comme `probabilities`, pour plusieurs positions en un seul passage du réseau (bien plus rapide)."""
        opponent_elo = opponent_elo if opponent_elo is not None else elo
        keys = [(b.fen(), elo, opponent_elo) for b in boards]
        with self._lock:
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
        import torch
        from maia2.utils import map_to_category

        all_moves, elo_dict, all_moves_reversed = self._prepared
        tensors, masks = [], []
        for board in boards:
            # Maia raisonne toujours du point de vue des Blancs : on retourne l'échiquier aux Noirs.
            seen = board if board.turn == chess.WHITE else board.mirror()
            tensors.append(self._board_to_tensor(seen))
            mask = torch.zeros(len(all_moves))
            mask[[all_moves[m.uci()] for m in seen.legal_moves]] = 1
            masks.append(mask)
        n = len(boards)
        elo_self = torch.tensor([map_to_category(elo, elo_dict)] * n)
        elo_oppo = torch.tensor([map_to_category(opponent_elo, elo_dict)] * n)
        legal = torch.stack(masks).bool()
        with torch.no_grad():
            logits, _, _ = self._model(torch.stack(tensors), elo_self, elo_oppo)
            probs = logits.masked_fill(~legal, float("-inf")).softmax(dim=-1)
        results = []
        for board, row, mask in zip(boards, probs, legal):
            result = {}
            for index in mask.nonzero(as_tuple=True)[0].tolist():
                move = chess.Move.from_uci(all_moves_reversed[index])
                if board.turn == chess.BLACK:
                    move = chess.Move(chess.square_mirror(move.from_square), chess.square_mirror(move.to_square),
                                      move.promotion)
                result[move] = float(row[index])
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
