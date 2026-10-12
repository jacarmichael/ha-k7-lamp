"""When and what to write: the daily plan rules (no Home Assistant imports).

Every ALL_SET rewrites the lamp's flash and makes it pause, so automatic updates:
- happen only inside the target's dark window (effective.plan_window), once per daily cycle;
- leave a lamp in manual mode alone and keep its manual levels;
- never repeat an acknowledged ALL_SET in the same cycle, even if the read-back differs.
Explicit user actions (Apply, acclimation, moonlight) may write at any time.

The coordinator does the I/O: plan() before touching the lamp, check_lamp() on a fresh
read right before writing, lamp_runs() on the read-back, confirmed() afterwards.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .const import ALL_SET_SETTLE
from .effective import acclimation_percent, build_effective, plan_window
from .model import Applied, Program
from .protocol import K7State, Row, sort_rows, state_rows

# The lamp is busy for ALL_SET_SETTLE seconds after a write; finish well inside the window.
WRITE_MARGIN = timedelta(seconds=ALL_SET_SETTLE + 5)

NO_WINDOW = ("No dark interval long enough for an automatic write. "
             "Apply changes explicitly or add a dark interval.")
MISSED = ("The daily plan has not been confirmed. The safe write window has passed; it will be "
          "retried in the next dark window, or you can Apply explicitly.")
MANUAL = "Lamp is in manual mode; automatic plan update deferred."
LATE = "Safe write window ended while waiting for the lamp; automatic update deferred."
WRITTEN = ("The lamp acknowledged tonight's plan, but reading it back did not confirm it. "
           "Automatic updates write at most once per night; press Apply to write it again.")
FAILED = ("The plan could not be confirmed. Automatic updates retry every five minutes within "
          "the dark window and check the lamp before writing again.")


@dataclass(frozen=True)
class Defer:
    """No write now; the key makes the user hear about it once."""

    key: str
    message: str


@dataclass(frozen=True)
class Write:
    manual: list[int]
    schedule: list[Row]
    cycle: str | None  # daily window start (ISO) this plan belongs to
    percent: int  # acclimation percentage it was built for (100 without acclimation)
    deadline: datetime | None = None  # end of the dark window; None for explicit writes


@dataclass(frozen=True)
class Unchanged:
    """The lamp already runs this plan; confirm it without writing."""


def plan(prog: Program, now: datetime, lat: float, lon: float, *, explicit: bool) -> Write | Defer | None:
    """What to write for `prog` at `now`; None when the current cycle is already confirmed."""
    window = plan_window(prog.target_schedule, now)
    cycle = window[0].isoformat() if window else None
    if not explicit:
        if window is None:
            return Defer(f"no-window:{now.date()}", NO_WINDOW)
        if not prog.pending and prog.applied and prog.applied.cycle == cycle:
            return None
        if now >= window[1]:
            return Defer(cycle, MISSED)
    # Retries within a cycle use one fixed anchor, so they build the same plan.
    anchor = now if explicit else window[0]
    manual, schedule = build_effective(prog, anchor, lat, lon)
    return Write(manual, sort_rows(schedule), cycle, acclimation_percent(prog, anchor.date()) or 100,
                 None if explicit else window[1])


def check_lamp(
    step: Write, lamp: K7State, prog: Program | None, now: datetime, *, explicit: bool,
) -> Write | Defer | Unchanged:
    """Decide on a fresh read of the lamp, right before writing."""
    if not explicit:
        if not lamp.auto:
            return Defer(f"{step.cycle}:manual", MANUAL)
        step = replace(step, manual=list(lamp.manual))  # keep the user's manual levels
    if lamp_runs(lamp, step.manual, step.schedule):
        return Unchanged()
    if not explicit:
        if prog is not None and prog.written_cycle == step.cycle:
            return Defer(f"{step.cycle}:written", WRITTEN)
        if step.deadline is None or now + WRITE_MARGIN > step.deadline:
            return Defer(f"{step.cycle}:late", LATE)
    return step


def lamp_runs(lamp: K7State, manual: list[int], schedule: list[Row]) -> bool:
    return lamp.auto and lamp.manual == manual and state_rows(lamp) == schedule


def confirmed(prog: Program, step: Write, now: datetime) -> tuple[Program | None, bool]:
    """(program after the lamp confirmed `step`, acclimation finished).

    The program is None when nothing is left to layer on the target.
    """
    finished = prog.acclimation is not None and step.percent >= 100
    if finished:
        prog = replace(prog, acclimation=None)
    if not prog.needed:
        return None, finished
    return replace(prog, pending=False, applied=Applied(step.cycle, step.percent, now.isoformat())), finished


def is_pending(prog: Program, now: datetime) -> bool:
    if prog.pending or prog.applied is None:
        return True
    window = plan_window(prog.target_schedule, now)
    return window is not None and prog.applied.cycle != window[0].isoformat()


def carried_over(prog: Program, now: datetime) -> Applied | None:
    """Treat today's plan of an older version (which wrote daily itself) as applied.

    Not while today's window is still open: then the upgrade simply writes as usual.
    """
    window = plan_window(prog.target_schedule, now)
    if window is not None and now < window[1]:
        return None
    return Applied(window[0].isoformat() if window else None, acclimation_percent(prog, now.date()) or 100)
