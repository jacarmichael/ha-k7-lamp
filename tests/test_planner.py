"""Daily plan rules (planner.py), without Home Assistant."""
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from helpers import hourly, sample_schedule
from k7_lamp import planner
from k7_lamp.model import DEFAULT_MOON, Acclimation, Applied, Program
from k7_lamp.planner import Defer, Unchanged, Write, carried_over, check_lamp, confirmed, is_pending, plan
from k7_lamp.protocol import K7State

TZ = ZoneInfo("Europe/Amsterdam")
WINDOW = datetime(2026, 10, 26, 7, 30, tzinfo=TZ)  # sample_schedule is dark until 08:00
CYCLE = WINDOW.isoformat()
NEVER_DARK = hourly({h: [40] * 6 for h in range(24)})


def prog(**kw):
    return Program([50] * 6, sample_schedule(), acclimation=Acclimation(50, 30, WINDOW.date()), **kw)


def lamp(rows=None, *, auto=True, manual=None):
    rows = rows if rows is not None else sample_schedule()
    return K7State("K7_Pro1", auto, manual or [50] * 6, [(r[0] * 60 + r[1], r[2:]) for r in rows])


def automatic(p, now):
    return plan(p, now, 52.0, 5.0, explicit=False)


def test_no_dark_window_defers_automatic_but_not_explicit():
    p = Program([50] * 6, NEVER_DARK, moon=dict(DEFAULT_MOON))
    assert automatic(p, WINDOW).message == planner.NO_WINDOW
    assert isinstance(plan(p, WINDOW, 52.0, 5.0, explicit=True), Write)


def test_confirmed_cycle_is_skipped_until_the_next_window():
    p = prog(applied=Applied(CYCLE, 50))
    assert automatic(p, WINDOW + timedelta(minutes=10)) is None
    assert automatic(p, WINDOW + timedelta(hours=8)) is None
    assert isinstance(automatic(p, WINDOW + timedelta(days=1)), Write)
    assert isinstance(automatic(replace(p, pending=True), WINDOW + timedelta(minutes=5)), Write)


def test_missed_window_is_not_caught_up_in_daylight():
    assert automatic(prog(), WINDOW.replace(hour=14)) == Defer(CYCLE, planner.MISSED)


def test_retries_in_a_cycle_build_the_same_plan():
    p = prog(moon=dict(DEFAULT_MOON))
    first = automatic(p, WINDOW)
    assert first == automatic(p, WINDOW + timedelta(minutes=25))
    assert (first.cycle, first.deadline, first.percent) == (CYCLE, WINDOW + timedelta(minutes=30), 50)


def test_manual_mode_defers_and_auto_mode_keeps_manual_levels():
    step = automatic(prog(), WINDOW)
    assert check_lamp(step, lamp(auto=False), prog(), WINDOW, explicit=False).message == planner.MANUAL
    action = check_lamp(step, lamp(hourly({}), manual=[12] * 6), prog(), WINDOW, explicit=False)
    assert isinstance(action, Write) and action.manual == [12] * 6
    # Explicit actions take the lamp to auto with the target's manual levels.
    explicit = plan(prog(), WINDOW, 52.0, 5.0, explicit=True)
    assert check_lamp(explicit, lamp(auto=False), prog(), WINDOW, explicit=True).manual == [50] * 6


def test_plan_already_on_the_lamp_is_not_rewritten():
    step = automatic(prog(), WINDOW)
    assert isinstance(check_lamp(step, lamp(step.schedule), prog(), WINDOW, explicit=False), Unchanged)


def test_one_acknowledged_write_per_cycle():
    step = automatic(prog(), WINDOW)
    written = prog(written_cycle=CYCLE)
    later = WINDOW + timedelta(minutes=5)
    assert check_lamp(step, lamp(hourly({})), written, later, explicit=False) == Defer(
        f"{CYCLE}:written", planner.WRITTEN
    )
    # Apply may always write.
    assert isinstance(check_lamp(step, lamp(hourly({})), written, later, explicit=True), Write)


def test_no_write_starts_too_close_to_the_end_of_the_window():
    step = automatic(prog(), WINDOW)
    late = WINDOW + timedelta(minutes=29, seconds=55)
    assert check_lamp(step, lamp(hourly({})), prog(), late, explicit=False).message == planner.LATE


def test_confirmed_records_the_cycle_and_drops_a_finished_program():
    p = prog(pending=True, written_cycle=CYCLE)
    step = automatic(p, WINDOW)
    after, finished = confirmed(p, step, WINDOW)
    assert not finished and not after.pending
    assert after.applied == Applied(CYCLE, 50, WINDOW.isoformat())
    done = replace(step, percent=100)
    assert confirmed(p, done, WINDOW) == (None, True)
    kept, finished = confirmed(replace(p, moon=dict(DEFAULT_MOON)), done, WINDOW)
    assert finished and kept.acclimation is None and kept.moon_enabled


def test_pending_until_this_cycle_is_confirmed():
    assert is_pending(prog(), WINDOW)
    applied = prog(applied=Applied(CYCLE, 50))
    assert not is_pending(applied, WINDOW.replace(hour=14))
    assert is_pending(applied, WINDOW + timedelta(days=1))
    assert is_pending(replace(applied, pending=True), WINDOW.replace(hour=14))


def test_upgrade_carries_over_todays_plan_only_after_its_window():
    assert carried_over(prog(), WINDOW.replace(hour=14)) == Applied(CYCLE, 50)
    assert carried_over(prog(), WINDOW + timedelta(minutes=10)) is None
    assert carried_over(Program([50] * 6, NEVER_DARK), WINDOW) == Applied(None, 100)


def test_program_round_trips_through_storage_format():
    p = prog(moon=dict(DEFAULT_MOON), pending=True, applied=Applied(CYCLE, 50, CYCLE), written_cycle=CYCLE)
    assert Program.from_dict(p.to_dict()) == p
