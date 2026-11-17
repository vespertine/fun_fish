import chess

from fun_fish import notable, opponents

TRAP = "Joue {} pour tendre un piège : un joueur à 1500 Elo s'y trompe dans {}% des cas et rapporte +{} en moyenne."


def export(decisions, anomalies=()):
    """Une partie où nous avons les Blancs : 1.e4 e5 2.Fc4 Cc6 3.Fxf7+ Rxf7 4.Cf3 Cf6 5.d4 d6 6.O-O ;
    `decisions` : demi-coup → (éval choisie, meilleure éval, explication), du point de vue des Blancs."""
    board, moves = chess.Board(), []
    for ply, san in enumerate("e4 e5 Bc4 Nc6 Bxf7+ Kxf7 Nf3 Nf6 d4 d6 O-O".split()):
        entry = {"ply": ply, "san": san, "fen_before": board.fen(), "by": "ai" if ply % 2 == 0 else "human"}
        if ply in decisions:
            chosen, best, text = decisions[ply]
            entry["decision"] = {"explanation": text, "candidates": [
                {"san": san, "score": chosen, "chosen": True}, {"san": "autre", "score": best, "chosen": False}]}
        moves.append(entry)
        board.push_san(san)
    return {"human_color": "black", "moves": moves, "fen": board.fen(),
            "anomalies": [{"fen": moves[p]["fen_before"]} for p in anomalies]}


def kinds(found):
    return {(h["ply"], h["kind"]) for h in found}


def test_each_kind_of_notable_move_is_found():
    found = notable.highlights(export({
        0: (30, 30, TRAP.format("e4", 45, 2.5)),  # piège annoncé, mais pas choisi pour lui (ne coûte rien)
        2: (20, 60, TRAP.format("Bc4", 45, 2.5)),  # piège : probable, payant, et il coûte 0,4
        4: (0, 0, "Joue Bxf7+ pour attaquer."),  # sacrifice : un fou pour un pion, l'éval tient
        6: (-250, -190, "Joue Nf3, à son goût."),  # style : 0,6 sous le meilleur ; et 3.Fxf7+ était une erreur
        8: (-250, -250, TRAP.format("d4", 30, 3.0)),  # piège trop peu probable ; garde-fou
        10: (-250, -250, "Roque."),
    }, anomalies=[8]))
    assert kinds(found) == {(2, "piège"), (4, "sacrifice"), (4, "notre erreur"), (6, "style"), (8, "garde-fou")}
    trap = next(h for h in found if h["kind"] == "piège")
    assert (trap["cost"], trap["swing"], trap["chance"], trap["gain"]) == (40, -20, 45, 2.5)  # il n'a pas pris


def test_a_sacrifice_needs_the_eval_to_hold():
    found = notable.highlights(export({4: (-300, -300, "Joue Bxf7+."), 6: (-300, -300, "")}))
    assert (4, "sacrifice") not in kinds(found)  # le fou est perdu, pas sacrifié


def test_highlights_are_noted_once_per_game_even_when_noted_again():
    data = export({2: (20, 60, TRAP.format("Bc4", 45, 2.5)), 4: (220, 220, "")})
    assert opponents.note_highlights("g1", "DuBot", data) == 3  # piège qui a pris (+2), puis 3.Fxf7+
    opponents.note_highlights("g1", "DuBot", data)
    with opponents.connect() as con:
        rows = con.execute("SELECT game, account, ply, kind, chance FROM highlights ORDER BY ply, kind").fetchall()
    assert rows == [("g1", "dubot", 2, "leur erreur", 45), ("g1", "dubot", 2, "piège", 45),
                    ("g1", "dubot", 4, "sacrifice", None)]


def test_the_report_compares_announced_and_held_traps():
    opponents.record(opponents.GameRecord("h", "dubot", "humain", None, False, 1500, "blitz", False, "white", "win",
                                          "mate", 40, 1.0))
    opponents.record(opponents.GameRecord("b", "dubot", "robot", "BOT", True, 2000, "blitz", False, "white", "loss",
                                          "mate", 40, 2.0))
    took = export({2: (20, 60, TRAP.format("Bc4", 60, 2.5)), 4: (220, 220, "")})  # a pris
    missed = export({2: (20, 60, TRAP.format("Bc4", 40, 2.5)), 4: (20, 20, "")})  # n'a pas pris
    opponents.note_highlights("h", "dubot", took)
    opponents.note_highlights("b", "dubot", missed)
    kinds, traps = opponents.notable_report()
    assert ("dubot", "piège", 1.0) in kinds
    assert traps == [("dubot", "bots", 1, 40.0, 0.0), ("dubot", "humains", 1, 60.0, 100.0)]


def test_an_exchange_followed_by_a_queen_sacrifice_is_not_a_sacrifice_of_the_first_move():
    board, moves = chess.Board("3r2k1/5ppp/4b3/8/8/8/3R1PPP/3Q2K1 w - - 0 1"), []
    for ply, san in enumerate("Rd5 Rxd5 Qxd5 Bxd5".split()):  # échange de tours, puis la dame est perdue
        moves.append({"ply": ply, "san": san, "fen_before": board.fen(), "by": "ai" if ply % 2 == 0 else "human",
                      **({"decision": {"explanation": "", "candidates": [{"san": san, "score": 0, "chosen": True}]}}
                         if ply % 2 == 0 else {})})
        board.push_san(san)
    data = {"human_color": "black", "moves": moves, "fen": board.fen()}
    assert (0, "sacrifice") not in kinds(notable.highlights(data))  # la tour est reprise au demi-coup 2


def test_the_report_counts_games_without_highlights_and_traps_without_outcome():
    opponents.record(opponents.GameRecord("b", "dubot", "robot", "BOT", True, 2000, "blitz", False, "white", "loss",
                                          "mate", 40, 2.0))
    opponents.note_highlights("b", "dubot", export({2: (20, 60, TRAP.format("Bc4", 40, 2.5))}))  # suite inconnue
    opponents.note_highlights("vide", "dubot", export({}))  # analysée, rien de notable
    kinds_, traps = opponents.notable_report()
    assert ("dubot", "piège", 0.5) in kinds_
    assert traps == [("dubot", "bots", 1, 40.0, None)]
