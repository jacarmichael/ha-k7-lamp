#!/usr/bin/env python3
"""Hands-on write tests for a K7 lamp. Run while watching the tank.

Every command prints the lamp's raw reply, then re-reads and prints its state.

  python3 k7_test.py status
  python3 k7_test.py manual 30            # manual mode + all channels 30%
  python3 k7_test.py manual 40 60 0 10 30 0   # per channel: W RB G UV C R
  python3 k7_test.py levels 30            # HAND_LUMINANCE only, no mode change
  python3 k7_test.py preview 30           # transient levels (reverts on next tick)
  python3 k7_test.py mode auto|manual
  python3 k7_test.py sync local|utc|HH:MM[:SS]
  python3 k7_test.py auto                 # safety net: back to auto mode
  python3 k7_test.py allset-same          # ALL_SET with the lamp's own schedule, auto, local time

Options: --host <lamp-ip> (required) --port 8266
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
# Lives in custom_components/k7_lamp/tools/ (so HACS installs it); protocol.py is one folder up.
for _p in (os.path.join(_HERE, "..", "custom_components", "k7_lamp"), os.path.join(_HERE, "..")):
    if os.path.exists(os.path.join(_p, "protocol.py")):
        sys.path.insert(0, os.path.abspath(_p))
        break
from protocol import (  # noqa: E402
    CHANNELS, K7Error, acked, preview_levels, read_all, set_levels, set_mode,
    state_rows, sync_time, write_all_set,
)


def parse_levels(vals):
    nums = [int(v) for v in vals]
    if len(nums) == 1:
        nums *= len(CHANNELS)
    if len(nums) != len(CHANNELS):
        raise SystemExit(f"give 1 or {len(CHANNELS)} levels")
    return nums


def show_reply(label, reply):
    print(f"{label}: reply {len(reply)} bytes: {reply.hex(' ') if reply else '(none)'}")


async def status(host, port):
    s = await read_all(host, port)
    now = datetime.now()
    m = now.hour * 60 + now.minute
    print(f"\n{s.name}  mode={'AUTO' if s.auto else 'MANUAL'}  (this machine: {now:%H:%M:%S})")
    print("  manual levels : " + ", ".join(f"{c} {v}" for c, v in zip(CHANNELS, s.manual)))
    print("  schedule now  : " + ", ".join(
        f"{c} {v}" for c, v in zip(CHANNELS, s.scheduled_levels(m))) + "  (if lamp clock = local)")


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True, help="lamp IP address (LAN mode)")
    ap.add_argument("--port", type=int, default=8266)
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    a = ap.parse_args()
    h, p = a.host, a.port

    try:
        if a.cmd == "status":
            pass
        elif a.cmd == "manual":
            levels = parse_levels(a.args)
            show_reply("CHANGE_MODE manual", await set_mode(h, p, False))
            await asyncio.sleep(0.5)
            show_reply(f"HAND_LUMINANCE {levels}", await set_levels(h, p, levels))
        elif a.cmd == "levels":
            levels = parse_levels(a.args)
            show_reply(f"HAND_LUMINANCE {levels}", await set_levels(h, p, levels))
        elif a.cmd == "preview":
            levels = parse_levels(a.args)
            show_reply(f"PREVIEW {levels}", await preview_levels(h, p, levels))
        elif a.cmd == "mode":
            if a.args not in (["auto"], ["manual"]):
                raise SystemExit("mode auto|manual")
            show_reply(f"CHANGE_MODE {a.args[0]}", await set_mode(h, p, a.args[0] == "auto"))
        elif a.cmd == "auto":
            show_reply("CHANGE_MODE auto", await set_mode(h, p, True))
        elif a.cmd == "sync":
            arg = a.args[0] if a.args else "local"
            if arg == "local":
                t = datetime.now()
            elif arg == "utc":
                t = datetime.now(timezone.utc)
            else:
                parts = [int(x) for x in arg.split(":")] + [0]
                t = datetime(2000, 1, 1, parts[0], parts[1], parts[2])
            show_reply(f"SYNC_TIME {t:%H:%M:%S} ({arg})", await sync_time(h, p, t.hour, t.minute, t.second))
        elif a.cmd == "allset-same":
            before = await read_all(h, p)
            rows = state_rows(before)
            t = datetime.now()
            print(f"Writing back {len(rows)} slots, manual {before.manual}, auto, {t:%H:%M:%S}")
            await asyncio.sleep(1.0)
            reply = await write_all_set(h, p, before.manual, rows, True, t.hour, t.minute, t.second)
            show_reply("ALL_SET", reply)
            print("acknowledged" if acked(reply) else "NOT acknowledged")
            await asyncio.sleep(3.0)
            after = await read_all(h, p)
            same = state_rows(after) == rows and after.manual == before.manual and after.auto
            print("schedule identical after write" if same else "!! schedule differs after write")
        else:
            raise SystemExit(__doc__)
        if a.cmd in ("manual", "levels"):
            await asyncio.sleep(5.0)  # the lamp ignores ALL_READ for a few seconds after HAND_LUMINANCE
        elif a.cmd != "status":
            await asyncio.sleep(1.0)
        await status(h, p)
    except K7Error as err:
        print(f"ERROR: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
