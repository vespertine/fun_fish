from fun_fish.pace import COST, Pace


def test_full_effort_with_plenty_of_time():
    assert Pace(full_seconds=3.0).effort(180, 2, fullmove=1) == 0  # 3+2 : ~4,4 s par coup


def test_less_effort_when_clock_runs_down():
    pace = Pace(full_seconds=3.0)
    levels = [pace.effort(remaining, 0, fullmove=30) for remaining in (120, 60, 30, 15, 6)]
    assert levels == sorted(levels) and levels[0] < levels[-1]
    assert levels[-1] == len(COST) - 1


def test_increment_buys_time():
    pace = Pace(full_seconds=3.0)
    assert pace.effort(20, 2, fullmove=30) < pace.effort(20, 0, fullmove=30)


def test_learns_machine_speed_but_ignores_instant_moves():
    pace = Pace(full_seconds=3.0)
    pace.observe(0.01, 0)  # coup de répertoire
    assert pace.full == 3.0
    for _ in range(20):
        pace.observe(6.0 * COST[2], 2)  # machine deux fois plus lente que prévu
    assert abs(pace.full - 6.0) < 0.1
    assert Pace(full_seconds=6.0).effort(180, 0, 1) > Pace(full_seconds=3.0).effort(180, 0, 1)
