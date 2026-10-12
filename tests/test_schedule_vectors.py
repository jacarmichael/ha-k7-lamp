"""protocol.py against the vectors the card's copy is also tested with (test_frontend.cjs)."""
import json
import pathlib

import pytest

from k7_lamp.protocol import active_rows, align_day_start, day_start, photoperiod, shift_rows

CASES = json.loads((pathlib.Path(__file__).parent / "schedule_vectors.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_schedule_maths_matches_shared_vectors(case):
    rows = case["rows"]
    assert active_rows(rows) == case["active_rows"]
    assert day_start(rows) == case["day_start"]
    period = photoperiod(rows)
    assert (list(period) if period else None) == case["photoperiod"]
    for fn, key, expected in [(shift_rows, "hours", s) for s in case["shift"]] + [
        (align_day_start, "hour", a) for a in case["align"]
    ]:
        if "error" in expected:
            with pytest.raises(ValueError, match="dark boundary"):
                fn(rows, expected[key])
        else:
            assert fn(rows, expected[key]) == expected["rows"]
