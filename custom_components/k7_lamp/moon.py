"""Moon position and illumination (no Home Assistant imports).

Ported from SunCalc by Vladimir Agafonkin (BSD-2-Clause,
https://github.com/mourner/suncalc), itself based on Meeus formulas.
Accurate to about a degree, which is plenty for hourly light slots.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from math import acos, asin, atan2, cos, pi, sin, tan

RAD = pi / 180
J1970, J2000 = 2440588, 2451545
E = RAD * 23.4397  # obliquity of the Earth
RISE_ALTITUDE = 0.133 * RAD  # SunCalc's moonrise/set threshold


def _days(dt: datetime) -> float:
    return dt.timestamp() / 86400 - 0.5 + J1970 - J2000


def _ra(l: float, b: float) -> float:
    return atan2(sin(l) * cos(E) - tan(b) * sin(E), cos(l))


def _dec(l: float, b: float) -> float:
    return asin(sin(b) * cos(E) + cos(b) * sin(E) * sin(l))


def _sun_coords(d: float) -> tuple[float, float]:
    m = RAD * (357.5291 + 0.98560028 * d)
    c = RAD * (1.9148 * sin(m) + 0.02 * sin(2 * m) + 0.0003 * sin(3 * m))
    l = m + c + RAD * 102.9372 + pi
    return _dec(l, 0), _ra(l, 0)


def _moon_coords(d: float) -> tuple[float, float, float]:
    """Declination, right ascension, distance (km)."""
    L = RAD * (218.316 + 13.176396 * d)
    M = RAD * (134.963 + 13.064993 * d)
    F = RAD * (93.272 + 13.229350 * d)
    l = L + RAD * 6.289 * sin(M)
    b = RAD * 5.128 * sin(F)
    return _dec(l, b), _ra(l, b), 385001 - 20905 * cos(M)


def moon_altitude(dt: datetime, lat: float, lon: float) -> float:
    """Altitude in degrees above the horizon (with refraction)."""
    d = _days(dt)
    dec, ra, _ = _moon_coords(d)
    H = RAD * (280.16 + 360.9856235 * d) - RAD * -lon - ra
    phi = RAD * lat
    h = asin(sin(phi) * sin(dec) + cos(phi) * cos(dec) * cos(H))
    hr = max(h, 0)
    h += 0.0002967 / tan(hr + 0.00312536 / (hr + 0.08901179))
    return h / RAD


def moon_illumination(dt: datetime) -> tuple[float, float]:
    """(illuminated fraction 0-1, phase 0-1 where 0 = new, 0.5 = full)."""
    d = _days(dt)
    s_dec, s_ra = _sun_coords(d)
    m_dec, m_ra, m_dist = _moon_coords(d)
    sdist = 149598000
    phi = acos(sin(s_dec) * sin(m_dec) + cos(s_dec) * cos(m_dec) * cos(s_ra - m_ra))
    inc = atan2(sdist * sin(phi), m_dist - sdist * cos(phi))
    angle = atan2(
        cos(s_dec) * sin(s_ra - m_ra),
        sin(s_dec) * cos(m_dec) - cos(s_dec) * sin(m_dec) * cos(s_ra - m_ra),
    )
    return (1 + cos(inc)) / 2, 0.5 + 0.5 * inc * (-1 if angle < 0 else 1) / pi


def phase_name(phase: float) -> str:
    names = ["New moon", "Waxing crescent", "First quarter", "Waxing gibbous",
             "Full moon", "Waning gibbous", "Last quarter", "Waning crescent"]
    return names[int((phase + 1 / 16) * 8) % 8]


def phase_icon(phase: float) -> str:
    return "🌑🌒🌓🌔🌕🌖🌗🌘"[int((phase + 1 / 16) * 8) % 8]


def rise_set(start: datetime, lat: float, lon: float, hours: int = 24,
             step_min: int = 5) -> tuple[datetime | None, datetime | None]:
    """First moonrise and moonset in [start, start + hours), found by scanning."""
    rise = sets = None
    t = start
    prev = moon_altitude(t, lat, lon) * RAD > RISE_ALTITUDE
    end = start + timedelta(hours=hours)
    while t < end and (rise is None or sets is None):
        t += timedelta(minutes=step_min)
        up = moon_altitude(t, lat, lon) * RAD > RISE_ALTITUDE
        if up and not prev and rise is None:
            rise = t
        if prev and not up and sets is None:
            sets = t
        prev = up
    return rise, sets


def altitude_factor(alt_deg: float, full_at: float = 15.0) -> float:
    """0 below the horizon, rising linearly to 1 at `full_at` degrees."""
    return max(0.0, min(1.0, alt_deg / full_at))


def moon_level(dt: datetime, lat: float, lon: float, max_level: int,
               follow_moon: bool = True) -> float:
    """Unrounded moonlight level (% of the lamp) at this moment."""
    frac, _ = moon_illumination(dt)
    if not follow_moon:
        return max_level * frac
    return max_level * frac * altitude_factor(moon_altitude(dt, lat, lon))

