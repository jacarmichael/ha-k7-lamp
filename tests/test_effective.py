from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from helpers import hourly, sample_schedule
from k7_lamp.effective import DEFAULT_MOON, build_effective, next_slot_time, rebuild_time
from k7_lamp.protocol import align_day_start

LAT, LON, TZ = 8.98, -79.52, ZoneInfo("America/Panama")  # Panama City (tropics, UTC-5)
FULL_MOON_WRITE = datetime(2026, 10, 26, 3, 30, tzinfo=TZ)


def program(**kw):
    return {"target_manual": [50] * 6, "target_schedule": align_day_start(sample_schedule(), 11),
            "acclimation": None, "moon": dict(DEFAULT_MOON), **kw}


def test_next_slot_time_rolls_over():
    assert next_slot_time(FULL_MOON_WRITE, 4, 0).day == 26
    assert next_slot_time(FULL_MOON_WRITE, 3, 0).day == 27


def test_full_moon_overlay_only_at_night():
    prog = program()
    _, eff = build_effective(prog, FULL_MOON_WRITE, LAT, LON)
    target = prog["target_schedule"]
    assert eff[2][3] == 15 and eff[2][6] == 10  # Royal Blue 15% (99.8% lit), Cyan 70% of 14.97
    for e, t in zip(eff, target):
        if t[3] >= 15:
            assert e == t  # bright hours untouched


def test_new_moon_adds_nothing():
    prog = program()
    _, eff = build_effective(prog, datetime(2026, 10, 10, 3, 30, tzinfo=TZ), LAT, LON)
    assert eff == prog["target_schedule"]


def test_acclimation_scales_target_not_moon():
    plain = build_effective(program(), FULL_MOON_WRITE, LAT, LON)[1]
    acc = program(acclimation={"start_pct": 50, "days": 30, "started": "2026-10-26"})
    _, eff = build_effective(acc, FULL_MOON_WRITE, LAT, LON)
    assert eff[15][3] == 40 and eff[2][3] == plain[2][3]


def test_invalid_moon_channel_is_skipped():
    prog = program(moon={**DEFAULT_MOON, "channels": {"9": 100, "1": 100}})
    _, eff = build_effective(prog, FULL_MOON_WRITE, LAT, LON)
    assert eff[2][3] == 15  # Royal Blue still gets the moon; channel 9 is ignored


def test_moon_off_is_plain_target():
    prog = program(moon=None)
    assert build_effective(prog, FULL_MOON_WRITE, LAT, LON)[1] == prog["target_schedule"]


@pytest.mark.parametrize("start,expected", [(11, (9, 30)), (8, (6, 30)), (20, (18, 30))])
def test_rebuild_time_before_lights_on(start, expected):
    assert rebuild_time(align_day_start(sample_schedule(), start)) == expected


def test_rebuild_time_short_dark_period_and_never_dark():
    one_dark_hour = hourly({h: [1] * 6 for h in range(24) if h != 10})
    assert rebuild_time(one_dark_hour) == (10, 0)
    assert rebuild_time(hourly({h: [1] * 6 for h in range(24)})) == (3, 30)
