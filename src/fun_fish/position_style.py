"""Goût des positions : ressemble-t-elle à celles des joueurs d'attaque, ou à celles des joueurs solides ?

Un classifieur linéaire lit la représentation interne de Maia-2 (sortie de `last_ln`, la tranche d'Elo
la plus haute pour les deux camps) de la position juste après un coup, vue du camp au trait. Il a été
appris sur environ 64 000 positions de parties lentes (après le 8e coup, évaluation dans ±6 pions) :
16 joueurs d'attaque (Tal, Nezhmetdinov, Shirov, Polgar, Velimirović…) contre 14 joueurs solides
(Karpov, Petrosian, Andersson, Capablanca, Flohr…). Sur des joueurs jamais vus pendant l'apprentissage,
il range du bon côté 86 % d'entre eux (moyenne de leurs positions). Une position seule ne dit pas grand
chose (60 % du bon côté) : c'est un goût, qui pèse coup après coup sur toute une partie.

Le réseau et le classifieur sont exportés ensemble en un seul fichier ONNX (`export`, qui demande
PyTorch et maia2), publié comme les fichiers de Maia et téléchargé à la première utilisation.
Les poids du classifieur sont dans `data/position_style.npz` (mu, sd, w, b).
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import chess

from fun_fish.sim import maia

RELEASE_URL = "https://github.com/vespertine/fun_fish/releases/download/maia2-style-v1"
FILE = "rapid_style.onnx"
SHA256 = "aa8cd9f4f4935285a607bc90eec1b77effc20c01655a0d79b2def5806d705016"
WEIGHTS = Path(__file__).parent / "data" / "position_style.npz"
ELO = 2400  # tranche d'Elo de Maia utilisée à l'apprentissage (la plus haute)


def model_path() -> Path:
    return maia.model_dir() / FILE


def ready(download: bool = True) -> bool:
    """onnxruntime est là et le modèle aussi (téléchargé au besoin)."""
    if not maia._has("onnxruntime"):
        return False
    if model_path().exists():
        return True
    if not download:
        return False
    try:
        _download()
    except OSError as exc:
        print(f"Téléchargement du goût des positions impossible : {exc}", file=sys.stderr)
        return False
    return True


def _download() -> None:
    import hashlib

    from fun_fish.engines import _download as fetch

    dest = model_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Téléchargement du goût des positions : {FILE}", file=sys.stderr)
    tmp = dest.with_suffix(".part")
    fetch(f"{RELEASE_URL}/{FILE}", tmp, lambda m: print(m, file=sys.stderr))
    if hashlib.sha256(tmp.read_bytes()).hexdigest() != SHA256:
        tmp.unlink()
        raise OSError(f"empreinte SHA-256 inattendue pour {FILE}")
    tmp.replace(dest)


class _Session:
    def __init__(self, threads: int = 1):
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_path()), options, providers=["CPUExecutionProvider"])
        self.lock = threading.Lock()

    def run(self, boards: list[chess.Board]):
        import numpy as np

        seen = [b if b.turn == chess.WHITE else b.mirror() for b in boards]
        x = np.stack([maia.encode(b) for b in seen])
        with self.lock:
            return self.session.run(["style"], {"boards": x})[0]


_shared: dict[str, _Session | None] = {}
_shared_lock = threading.Lock()


def logits(boards: list[chess.Board]):
    """Logit « attaque » de chaque position (> 0 : côté attaque), ou None si le modèle manque."""
    with _shared_lock:
        if "s" not in _shared:
            _shared["s"] = _Session() if ready() else None
    session = _shared["s"]
    if session is None or not boards:
        return None
    return session.run(boards)


def export(out: Path | None = None) -> Path:
    """Exporte Maia-2 et le classifieur en un ONNX (entrée : positions encodées ; sortie : logit)."""
    import numpy as np
    import torch

    net = maia._TorchNet("rapid", threads=4)
    body = net.model.module if hasattr(net.model, "module") else net.model
    w = np.load(WEIGHTS)

    class Style(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.body = body
            for name in ("mu", "sd", "w"):
                self.register_buffer(name, torch.from_numpy(w[name].astype(np.float32)))
            self.b = float(w["b"])
            self.elo = maia.elo_category(ELO)

        def forward(self, boards):
            grab = {}
            hook = self.body.last_ln.register_forward_hook(lambda m, i, o: grab.__setitem__("x", o))
            e = torch.full((boards.shape[0],), self.elo, dtype=torch.long)
            self.body(boards, e, e)
            hook.remove()
            return ((grab["x"] - self.mu) / self.sd) @ self.w + self.b

    out = out or model_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    sample = torch.from_numpy(maia.encode(chess.Board())).unsqueeze(0).repeat(2, 1, 1, 1)
    torch.onnx.export(Style().eval(), (sample,), str(out), input_names=["boards"], output_names=["style"],
                      dynamic_axes={"boards": {0: "n"}, "style": {0: "n"}}, opset_version=17, dynamo=False)
    return out
