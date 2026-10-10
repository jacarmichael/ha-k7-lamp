from datetime import date, timedelta

from k7_lamp.acclimation import percent_for, scale

D0 = date(2026, 10, 8)


def test_linear_ramp():
    got = [percent_for(50, 30, D0, D0 + timedelta(days=d)) for d in (-1, 0, 15, 29, 30, 40)]
    assert got == [50, 50, 75, 98, 100, 100]


def test_scale_keeps_lit_channels_and_rounds_half_up():
    _, rows = scale([50] * 6, [[9, 0, 5, 80, 50, 10, 80, 0], [0, 0, 1, 2, 100, 0, 0, 0]], 50)
    assert rows == [[9, 0, 3, 40, 25, 5, 40, 0], [0, 0, 1, 1, 50, 0, 0, 0]]
