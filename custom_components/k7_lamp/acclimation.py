"""Acclimation ramp maths (no Home Assistant imports).

Same formula as bitbarista/k7-led-controller Effects.cpp acclimationPercentNow():
pct = start + (100 - start) * elapsed_days / duration_days, stepping once a day.
"""
from __future__ import annotations

from datetime import date

from .protocol import Row


def _round(x: float) -> int:
    """Round halves up, like roundf() in the reference firmware."""
    return int(x + 0.5)


def percent_for(start_pct: int, days: int, started: date, today: date) -> int:
    elapsed = (today - started).days
    if elapsed <= 0:
        return start_pct
    if elapsed >= days:
        return 100
    return max(1, min(100, _round(start_pct + (100 - start_pct) * elapsed / days)))


def day_number(started: date, today: date) -> int:
    """Day 0 is the start day."""
    return max(0, (today - started).days)


def _scaled(v: int, pct: int) -> int:
    # Keep any lit channel at least 1% so the curve keeps its shape.
    return 0 if v == 0 else max(1, min(100, _round(v * pct / 100)))


def scale(manual: list[int], schedule: list[Row], pct: int) -> tuple[list[int], list[Row]]:
    """Scale the schedule levels; manual levels are left as they are."""
    return list(manual), [[r[0], r[1], *(_scaled(v, pct) for v in r[2:])] for r in schedule]
