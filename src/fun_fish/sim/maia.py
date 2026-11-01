"""Maia-2 : probabilité qu'un humain d'un Elo donné joue chaque coup.

Le modèle couvre les tranches < 1100, 1100-1199, ..., 1900-1999 et ≥ 2000. Les poids
(280 Mo) sont téléchargés une fois dans le cache de fun_fish, avec vérification SHA-256.
"""

from __future__ import annotations

import contextlib
import io
import random

import chess

from fun_fish.engines import cache_dir


class Maia:
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
        self._inference = inference

    def probabilities(self, board: chess.Board, elo: int, opponent_elo: int | None = None) -> dict[chess.Move, float]:
        """Probabilité de chaque coup légal pour un joueur de `elo` face à un adversaire de `opponent_elo`."""
        probs, _ = self._inference.inference_each(self._model, self._prepared, board.fen(), elo,
                                                  opponent_elo if opponent_elo is not None else elo)
        legal = {m.uci(): m for m in board.legal_moves}
        result = {legal[u]: p for u, p in probs.items() if u in legal}
        total = sum(result.values())
        if not result or total <= 0:
            return {m: 1 / len(legal) for m in legal.values()}
        return {m: p / total for m, p in result.items()}


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
