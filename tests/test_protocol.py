import pytest

from helpers import hourly, sample_schedule
from k7_lamp.protocol import (
    CMD_ALL_SET, align_day_start, build_all_set, build_frame, day_start, parse_qr_payload,
    parse_reply, photoperiod, shift_rows, to_qr_payload,
)

# ALL_READ reply as received from a K7 Pro IV (prefix echo + frame), schedule dark.
READ_REPLY = bytes.fromhex(
    "abaaa5a1" "abaa1008" "323232323232" "18"
    + "".join(f"{h:02x}" + "00" * 7 for h in range(24)) + "01"
    + "4b375f50726f3132333435" + "00" * 6 + "bb"
)


def test_parse_reply_skips_echo_prefix():
    state = parse_reply(READ_REPLY)
    assert state.name == "K7_Pro12345"
    assert state.auto and state.manual == [50] * 6 and len(state.schedule) == 24


def test_parse_reply_incomplete_returns_none():
    assert parse_reply(READ_REPLY[:-1]) is None


def test_all_set_payload_layout():
    sched = sample_schedule()
    p = build_all_set([50] * 6, sched, True, 12, 34, 56)
    assert len(p) == 203 and p[6] == 24 and p[-4] == 1 and list(p[-3:]) == [12, 34, 56]
    frame = build_frame(CMD_ALL_SET, p)
    assert frame[:4] == bytes([0xAA, 0xA5, 0x10, 0x07]) and frame[-1] == 0xBB


def test_qr_round_trip():
    sched = sample_schedule()
    q = to_qr_payload([10, 85, 4, 42, 78, 0], sched)
    assert len(q) == 397 and q[12] == "#"
    assert parse_qr_payload(q.lower()) == ([10, 85, 4, 42, 78, 0], sched)


@pytest.mark.parametrize("bad", ["", "00#00", "ZZ" + "0" * 395, "0" * 12 + "#" + "0" * 382 + "FF"])
def test_qr_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        parse_qr_payload(bad)


def test_day_start_and_shift():
    s = sample_schedule()
    assert day_start(s) == 9 and photoperiod(s) == (9, 21)
    s11 = align_day_start(s, 11)
    assert photoperiod(s11) == (11, 23) and s11[13][2:] == [5, 80, 50, 10, 80, 0]
    wrap = align_day_start(s, 20)
    assert photoperiod(wrap) == (20, 8)
    assert shift_rows(shift_rows(s, 5), -5) == s


def test_never_dark_is_not_shifted():
    always = hourly({h: [1] * 6 for h in range(24)})
    assert day_start(always) is None and align_day_start(always, 11) == always
