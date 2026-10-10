"""Effective schedule maths (no Home Assistant imports).

effective = target -> acclimation scaling -> moon overlay, for the next 24 hours.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from .acclimation import _round, percent_for, scale
from .moon import moon_level
from .protocol import Row, day_start

DEFAULT_MOON = {
    "enabled": True,
    "max_level": 15,
    "channels": {"1": 100, "4": 70},  # Royal Blue full, Cyan at 70% (as bitbarista)
    "follow_moon": True,
}
FALLBACK_REBUILD = (3, 30)


def rebuild_time(schedule: list[Row]) -> tuple[int, int]:
    """When to write the daily plan: in the dark, shortly before lights-on.

    hh:30 two hours before the day starts if both of those slots are dark (the lamp may
    already be ramping during the last dark hour); otherwise the top of the last dark
    hour; 03:30 if the schedule is never dark.
    """
    start = day_start(schedule)
    if start is None:
        return FALLBACK_REBUILD
    levels = {r[0]: r[2:] for r in schedule}
    two_before, one_before = (start - 2) % 24, (start - 1) % 24
    if not any(levels.get(two_before, [1])):
        return two_before, 30
    return one_before, 0


def acclimation_percent(program: dict[str, Any], today: date) -> int | None:
    acc = program.get("acclimation")
    if not acc:
        return None
    return percent_for(acc["start_pct"], acc["days"], date.fromisoformat(acc["started"]), today)


def next_slot_time(now: datetime, hour: int, minute: int) -> datetime:
    """The next time hh:mm comes round after `now` (both in local time)."""
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return slot if slot > now else slot + timedelta(days=1)


def build_effective(
    program: dict[str, Any], now: datetime, lat: float, lon: float
) -> tuple[list[int], list[Row]]:
    """What the lamp should run for the next 24 h. `now` must be timezone-aware local time."""
    manual = list(program["target_manual"])
    schedule = [list(r) for r in program["target_schedule"]]
    pct = acclimation_percent(program, now.date())
    if pct is not None and pct < 100:
        manual, schedule = scale(manual, schedule, pct)
    moon = program.get("moon")
    if moon and moon.get("enabled"):
        weights = {int(ch): w for ch, w in moon["channels"].items() if w > 0}
        for row in schedule:
            when = next_slot_time(now, row[0], row[1]).astimezone(timezone.utc)
            level = moon_level(when, lat, lon, moon["max_level"], moon.get("follow_moon", True))
            for ch, weight in weights.items():
                v = min(100, _round(level * weight / 100))
                row[2 + ch] = max(row[2 + ch], v)
    return manual, schedule
