"""Schedule regressions found in the 0.5.1 review."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from helpers import hourly
from k7_lamp.effective import build_effective, plan_window, rebuild_time, rebuild_window
from k7_lamp.model import DEFAULT_MOON, Program, validate_moon
from k7_lamp.protocol import (
    K7State, align_day_start, build_all_set, parse_qr_payload,
    parse_reply, shift_rows, state_rows, to_qr_payload,
)


def sparse(*rows):
    return [[0] * 8 for _ in range(24 - len(rows))] + list(rows)


def reply(rows):
    return (bytes.fromhex("abaa1008") + bytes([50] * 6 + [len(rows)])
            + b"".join(bytes(r) for r in rows) + b"\x01K7_Pro12345\x00\xbb")


def test_app_padding_survives_read_qr_and_write():
    rows = sparse([9, 30, 20, 0, 0, 0, 0, 0], [21, 45, 0, 0, 0, 0, 0, 0])
    state = parse_reply(reply(rows))
    assert state_rows(state) == rows
    assert parse_qr_payload(to_qr_payload(state.manual, state_rows(state)))[1] == rows
    payload = build_all_set(state.manual, state_rows(state), True, 12, 0, 0)
    assert payload[7:199] == b"".join(bytes(r) for r in rows)


def test_duplicate_active_times_preserve_wire_order():
    rows = sparse([9, 0, 60, 0, 0, 0, 0, 0], [9, 0, 20, 0, 0, 0, 0, 0])
    assert state_rows(parse_reply(reply(rows))) == rows


def test_short_reply_only_adds_disabled_padding():
    rows = [[9, 0, 40, 0, 0, 0, 0, 0], [21, 0, 0, 0, 0, 0, 0, 0]]
    assert state_rows(parse_reply(reply(rows))) == sparse(*rows)


def test_padding_does_not_dim_midnight_or_become_moonlight():
    rows = sparse([1, 0, 40, 0, 0, 0, 0, 0], [23, 0, 40, 0, 0, 0, 0, 0])
    assert parse_reply(reply(rows)).scheduled_levels(0)[0] == 40
    prog = Program([50] * 6, rows, moon={**DEFAULT_MOON, "follow_moon": False})
    _, actual = build_effective(prog, datetime(2026, 10, 26, tzinfo=ZoneInfo("UTC")), 0, 0)
    assert actual[:22] == [[0] * 8 for _ in range(22)]
    assert actual[22][3] > 0


@pytest.mark.parametrize("key", ["-1", "-2", "6", "7", "x", "1.5", 1.5, True])
def test_moon_indices_rejected_before_indexing(key):
    cfg = {**DEFAULT_MOON, "channels": {key: 100}}
    with pytest.raises(ValueError):
        validate_moon(cfg)
    with pytest.raises(ValueError):
        Program([50] * 6, hourly({}), moon=cfg)


def test_nonhourly_alignment_preserves_intervals_and_padding():
    rows = sparse([8, 45, 0, 0, 0, 0, 0, 0], [9, 30, 20, 0, 0, 0, 0, 0], [21, 15, 0, 0, 0, 0, 0, 0])
    shifted = align_day_start(rows, 11)
    assert shifted == sparse(
        [10, 15, 0, 0, 0, 0, 0, 0], [11, 0, 20, 0, 0, 0, 0, 0], [22, 45, 0, 0, 0, 0, 0, 0]
    )
    assert shift_rows(shift_rows(rows, 2), -2) == rows


def test_refuse_unrepresentable_midnight_dark_boundary():
    rows = sparse([8, 0, 0, 0, 0, 0, 0, 0], [9, 0, 40, 0, 0, 0, 0, 0], [21, 0, 0, 0, 0, 0, 0, 0])
    with pytest.raises(ValueError, match="dark boundary"):
        shift_rows(rows, 3)


def test_rebuild_requires_actual_dark_interval_not_rounded_output():
    rows = hourly({h: [40] * 6 for h in range(24)})
    rows[8] = [8, 30, 0, 0, 0, 0, 0, 0]
    assert rebuild_time(rows) is None
    rows[7] = [7, 45, 0, 0, 0, 0, 0, 0]
    assert rebuild_window(rows) == (480, 30)
    state = K7State("test", True, [50] * 6, [(r[0] * 60 + r[1], r[2:]) for r in rows])
    for minute in range(480, 510):
        assert state.scheduled_levels(minute) == [0] * 6


def test_dark_window_wraps_midnight_and_anchor_is_stable():
    rows = sparse([0, 20, 0, 0, 0, 0, 0, 0], [1, 0, 40, 0, 0, 0, 0, 0], [23, 40, 0, 0, 0, 0, 0, 0])
    assert rebuild_window(rows) == (1430, 30)
    now = datetime(2026, 10, 27, 0, 5, tzinfo=ZoneInfo("Europe/Amsterdam"))
    start, end = plan_window(rows, now)
    assert (start.day, start.hour, start.minute) == (26, 23, 50)
    assert (end.day, end.hour, end.minute) == (27, 0, 20)
    assert plan_window(rows, now.replace(minute=10)) == (start, end)


def test_dark_intervals_shorter_than_one_minute_are_not_invented():
    assert rebuild_window(sparse([8, 0, 0, 0, 0, 0, 0, 0], [8, 0, 40, 0, 0, 0, 0, 0])) is None


def test_empty_schedule_has_safe_daily_window():
    assert rebuild_window(sparse()) == (210, 30)


def test_dst_repeated_hour_keeps_one_cycle_identifier():
    rows = sparse([1, 0, 0, 0, 0, 0, 0, 0], [3, 0, 0, 0, 0, 0, 0, 0], [4, 0, 40, 0, 0, 0, 0, 0])
    first = datetime(2026, 10, 25, 2, 35, tzinfo=ZoneInfo("Europe/Amsterdam"), fold=0)
    second = first.replace(fold=1)
    assert plan_window(rows, first)[0].isoformat() == plan_window(rows, second)[0].isoformat()
