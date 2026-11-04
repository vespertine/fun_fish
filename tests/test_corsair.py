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
