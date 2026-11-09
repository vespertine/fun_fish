from fun_fish.personalities import create
from fun_fish.personalities.attacker import Outcome
from fun_fish.personalities.corsair import Corsair, SlyOutcome


def test_strongest_motive_leads():
    c = Corsair(lead_share=0.3)
    trap = c.lead(c.motives(SlyOutcome(value=300, error=0.8), 0))  # vrai piège, pas d'attaque
    attack = c.lead(c.motives(SlyOutcome(mate=0.5, win=0.5, value=300), 0))  # vraie attaque
    lukewarm = c.lead(c.motives(SlyOutcome(win=0.2, value=60, error=0.2), 0))  # un peu des deux
    assert trap > lukewarm and attack > lukewarm


def test_errors_add_up_through_the_tree():
    o = SlyOutcome()
    o.add(SlyOutcome(error=1.0), 0.3, ["a"])
    o.add(Outcome(value=10), 0.7, ["b"])  # une feuille du cogneur : pas d'erreur notée
    assert abs(o.error - 0.3) < 1e-9


def test_corsair_profile_keeps_romantic_tastes_and_exposes_fusion_settings():
    names = {s["name"]: s["default"] for s in Corsair.param_specs()}
    assert names["w_develop"] == 0.6 and names["vision_elo"] == 1800 and names["w_trap"] == 0.5
    c = create("corsair", w_trap=1.0, tolerance=300)
    assert c.w_trap == 1.0 and c.tolerance == 300 and c.params()["lead_share"] == 0.3


def winning(c: Corsair, degree: float) -> Corsair:
    c._degree = degree  # degré de conversion du coup en cours (posé par _choose_move)
    return c


def test_panache_makes_material_matter_less_when_winning():
    grab = SlyOutcome(win=1.0, value=900)  # il rafle encore 3 pions
    plain = Corsair(panache=0)
    flashy = Corsair(panache=1)
    assert plain.motives(grab, 600)["gain"] == winning(plain, 1.0).motives(grab, 600)["gain"] > 0
    assert winning(flashy, 0.0).motives(grab, 600)["gain"] == plain.motives(grab, 600)["gain"]  # pas gagnant : inchangé
    assert winning(flashy, 1.0).motives(grab, 600)["gain"] == 0.0  # conversion complète : le matériel ne compte plus
    assert winning(flashy, 0.5).flair() == 1.5 and winning(flashy, 1.0).greed() == 0.0
    mate = SlyOutcome(mate=1.0, win=1.0, value=2000)
    assert winning(flashy, 1.0).motives(mate, 600)["mat"] == plain.motives(mate, 600)["mat"]  # le mat, si


def test_luxury_pays_part_of_the_price_for_the_corsair_too():
    from fun_fish.analysis import Line
    from fun_fish.personalities.attacker import CandidateEval
    import chess

    line = Line(move=chess.Move.from_uci("e2e4"), score=500, mate=None, wdl=None, pv=(), depth=6)
    e = CandidateEval(line, loss=200, outcome=SlyOutcome())
    c = Corsair()
    c._luxury = 0
    full = c.scored(e, line).score
    c._luxury = 200  # gagnant, il s'autorise à lâcher 2 pions pour un coup de style : il en paie la moitié
    assert c.scored(e, line).score == full + 1.0
