"""Effective schedule maths (no Home Assistant imports).

effective = target -> acclimation scaling -> moon overlay, for the next 24 hours.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .acclimation import _round, percent_for, scale
from .model import Program
from .moon import moon_level
from .protocol import Row, active_rows


def rebuild_window(schedule: list[Row]) -> tuple[int, int] | None:
    """(start minute, duration): up to 30 min of actual darkness, on 5 min ticks.

    Darkness requires consecutive zero points: a lone zero is an instantaneous
    dip, not time in which a flash write can safely finish. No dark interval means
    automatic writes are suspended; explicit user actions remain available.
    """
    rows = active_rows(schedule)
    if not any(any(r[2:]) for r in rows):
        return 210, 30  # Entirely dark: 03:30-04:00.
    for i, row in enumerate(rows):
        if not any(row[2:]) or any(rows[i - 1][2:]):
            continue
        end_idx = (i - 1) % len(rows)
        first = end_idx
        while not any(rows[(first - 1) % len(rows)][2:]):
            first = (first - 1) % len(rows)
        start = rows[first][0] * 60 + rows[first][1]
        end = rows[end_idx][0] * 60 + rows[end_idx][1]
        duration = (end - start) % 1440
        if duration == 0:
            continue
        end = start + duration
        write = ((max(start, end - 30) + 4) // 5) * 5
        if write + 1 <= end:
            return write % 1440, min(30, end - write)
    return None


def rebuild_time(schedule: list[Row]) -> tuple[int, int] | None:
    window = rebuild_window(schedule)
    return divmod(window[0], 60) if window else None


def plan_window(schedule: list[Row], now: datetime) -> tuple[datetime, datetime] | None:
    """Most recent daily window, including one that crosses local midnight."""
    window = rebuild_window(schedule)
    if window is None:
        return None
    minute, duration = window
    # A repeated clock hour at DST end is still one daily plan cycle.
    start = now.replace(hour=minute // 60, minute=minute % 60, second=0, microsecond=0, fold=0)
    if start > now:
        start -= timedelta(days=1)
    return start, start + timedelta(minutes=duration)


def acclimation_percent(program: Program, today: date) -> int | None:
    acc = program.acclimation
    if acc is None:
        return None
    return percent_for(acc.start_pct, acc.days, acc.started, today)


def next_slot_time(now: datetime, hour: int, minute: int) -> datetime:
    """The next time hh:mm comes round after `now` (both in local time)."""
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return slot if slot > now else slot + timedelta(days=1)


def build_effective(
    program: Program, now: datetime, lat: float, lon: float
) -> tuple[list[int], list[Row]]:
    """What the lamp should run for the next 24 h. `now` must be timezone-aware local time."""
    manual = list(program.target_manual)
    schedule = [list(r) for r in program.target_schedule]
    pct = acclimation_percent(program, now.date())
    if pct is not None and pct < 100:
        manual, schedule = scale(manual, schedule, pct)
    if program.moon_enabled:
        moon = program.moon
        weights = {int(ch): w for ch, w in moon["channels"].items()}
        for row in schedule:
            if not any(row):
                continue  # Keep the app's disabled midnight slots disabled.
            when = next_slot_time(now, row[0], row[1]).astimezone(timezone.utc)
            level = moon_level(when, lat, lon, moon["max_level"], moon["follow_moon"])
            for ch, weight in weights.items():
                v = min(100, _round(level * weight / 100))
                row[2 + ch] = max(row[2 + ch], v)
    return manual, schedule
