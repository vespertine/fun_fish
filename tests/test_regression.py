"""Le comportement de Fun Fish 2 ne change pas sans qu'on le veuille.

Si ce test échoue après un changement voulu (nouveau réglage, nouveau motif…), vérifier l'effet
avec `scripts/perfbench.py` puis régénérer : `python scripts/perfbench.py golden`.
"""

import pytest

from fun_fish.engines import StockfishNotFound, find_stockfish
from fun_fish.sim import maia

from regression import decide, load


@pytest.fixture(scope="module")
def stockfish():
    if not maia.onnx_ready(download=False):
        pytest.skip("Maia-2 ONNX absent (extra `maia`)")
    try:
        return str(find_stockfish(download=False))
    except StockfishNotFound:
        pytest.skip("Stockfish absent")


@pytest.mark.parametrize("case", load(), ids=lambda c: f"{c['game']}-{c['ply']}-e{c['effort']}")
def test_same_choice_as_recorded(case, stockfish):
    got = decide(case, stockfish)
    assert got["move"] == case["move"]
    for move, p in case["candidates"].items():
        assert got["candidates"].get(move, 0.0) == pytest.approx(p, abs=2e-3), move
