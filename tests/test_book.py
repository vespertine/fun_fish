import chess

from fun_fish.book import Book, _count, _games, bucket, key
from fun_fish.style import development

PGN = """[Event "Rated Blitz game"]
[WhiteElo "1510"]
[BlackElo "1490"]
[TimeControl "300+0"]

1. e4 { [%clk 0:05:00] } 1... e5 { [%clk 0:05:00] } 2. Nf3 Nc6 3. Bb5 a6 1-0

[Event "Rated Bullet game"]
[WhiteElo "1500"]
[BlackElo "1500"]
[TimeControl "60+0"]

1. d4 d5 1-0
"""


def test_build_counts_moves_by_mover_elo():
    games = list(_games(PGN.splitlines()))
    assert games == [(1510, 1490, ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "1-0"])]  # pas de bullet
    counts = _count(games)
    start = key(chess.Board())
    assert counts[start] == {bucket(1510): {"e2e4": 1}}
    after = chess.Board()
    after.push_uci("e2e4")
    assert counts[key(after)] == {bucket(1490): {"e7e5": 1}}


def test_probabilities_use_neighbour_buckets_when_thin():
    start = key(chess.Board())
    book = Book({start: {str(bucket(1500)): {"e2e4": 6, "d2d4": 4}, str(bucket(1700)): {"e2e4": 10, "c2c4": 10}}})
    p = book.probabilities(chess.Board(), 1500)  # 10 parties à 1400-1599 : on ajoute 1200 et 1600
    assert p is not None and abs(sum(p.values()) - 1) < 1e-9
    assert p[chess.Move.from_uci("e2e4")] == 16 / 30
    assert book.probabilities(chess.Board("8/8/8/8/8/8/8/K1k5 w - - 0 1"), 1500) is None


def test_freeing_a_bishop_counts_as_half_development():
    e4 = chess.Board()
    e4.push_uci("e2e4")
    a3 = chess.Board()
    a3.push_uci("a2a3")
    assert development(e4, chess.WHITE) == 0.5 and development(a3, chess.WHITE) == 0
