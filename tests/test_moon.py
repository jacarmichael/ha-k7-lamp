from datetime import datetime, timedelta, timezone

import pytest

from k7_lamp.moon import altitude_factor, moon_illumination, phase_name, rise_set

LAT, LON, TZ = 51.4769, -0.0005, timezone.utc  # Royal Observatory, Greenwich

# US Naval Observatory (aa.usno.navy.mil) rise/set for this location, UTC.
USNO = {
    (2026, 10, 8): ("03:27", "16:23"),
    (2026, 10, 15): ("12:14", "18:55"),
    (2026, 10, 26): ("16:20", "07:16"),
}


def _minutes(dt, hhmm):
    h, m = map(int, hhmm.split(":"))
    ref = dt.replace(hour=h, minute=m)
    return min(abs((dt - ref).total_seconds()), abs((dt - ref - timedelta(days=1)).total_seconds()),
               abs((dt - ref + timedelta(days=1)).total_seconds())) / 60


@pytest.mark.parametrize("day,expected", USNO.items())
def test_rise_set_matches_usno(day, expected):
    rise, sets = rise_set(datetime(*day, tzinfo=TZ), LAT, LON)
    assert _minutes(rise.astimezone(TZ), expected[0]) <= 15
    assert _minutes(sets.astimezone(TZ), expected[1]) <= 15


def test_new_and_full_moon_october_2026():
    assert moon_illumination(datetime(2026, 10, 10, 12, tzinfo=timezone.utc))[0] < 0.01
    frac, phase = moon_illumination(datetime(2026, 10, 26, 5, tzinfo=timezone.utc))
    assert frac > 0.99 and phase_name(phase) == "Full moon"


def test_altitude_factor():
    assert altitude_factor(-5) == 0 and altitude_factor(7.5) == 0.5 and altitude_factor(40) == 1
