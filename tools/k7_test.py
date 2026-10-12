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
  python3 k7_test.py midnight-test        # are all-zero 00:00 slots disabled? (backs up, then restores)
  python3 k7_test.py restore FILE         # ALL_SET a backup made by midnight-test, local time
  python3 k7_test.py demo start|stop      # the lamp's 24 h demo in 4 minutes

Every ALL_SET rewrites the lamp's flash and makes it pause for a few seconds: allset-same and
restore write once, midnight-test twice.

Options: --host <lamp-ip> (required) --port 8266
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
# Works from the repo (tools/) and from inside an installed custom_components/k7_lamp/tools/.
for _p in (os.path.join(_HERE, "..", "custom_components", "k7_lamp"), os.path.join(_HERE, "..")):
    if os.path.exists(os.path.join(_p, "protocol.py")):
        sys.path.insert(0, os.path.abspath(_p))
        break
from protocol import (  # noqa: E402
    CHANNELS, K7Error, acked, preview_levels, read_all, send_command, set_levels, set_mode,
    sort_rows, state_rows, sync_time, write_all_set,
)

CMD_DEMO = (0x10, 0x0A)  # 00 = start, 01 = stop

# Royal Blue 100% at 23:00 and 01:00 with an all-zero slot at 00:00, shown at 23:55. If the
# firmware skips that slot (as the app and the integration assume) the tank stays at 100%;
# if it is a real point, the light is ramping down to ~8%.
MIDNIGHT_TEST = [[0] * 8 for _ in range(20)] + [
    [22, 0, 0, 0, 0, 0, 0, 0], [23, 0, 0, 100, 0, 0, 0, 0],
    [1, 0, 0, 100, 0, 0, 0, 0], [2, 0, 0, 0, 0, 0, 0, 0],
]


def parse_levels(vals):
    nums = [int(v) for v in vals]
    if len(nums) == 1:
        nums *= len(CHANNELS)
    if len(nums) != len(CHANNELS):
        raise SystemExit(f"give 1 or {len(CHANNELS)} levels")
    return nums


def show_reply(label, reply):
    print(f"{label}: reply {len(reply)} bytes: {reply.hex(' ') if reply else '(none)'}")


async def write_and_compare(host, port, manual, rows, auto, t):
    """ALL_SET, then read back and show every slot the lamp stores differently."""
    reply = await write_all_set(host, port, manual, rows, auto, t.hour, t.minute, t.second)
    show_reply("ALL_SET", reply)
    print("acknowledged" if acked(reply) else "NOT acknowledged")
    await asyncio.sleep(4.0)
    after = await read_all(host, port)
    got, want = state_rows(after), sort_rows(rows)
    if got == want and after.manual == manual and after.auto == auto:
        print("read-back identical")
        return
    print(f"!! read-back differs ({len(after.schedule)} slots on the wire)")
    if after.manual != manual or after.auto != auto:
        print(f"   manual {after.manual} auto {after.auto}, wrote manual {manual} auto {auto}")
    for i, (w, g) in enumerate(zip(want, got)):
        if w != g:
            print(f"   slot {i:2}: wrote {w}, read {g}")


def save_backup(state):
    path = f"k7-backup-{state.name}-{datetime.now():%Y%m%d-%H%M%S}.json"
    with open(path, "w") as f:
        json.dump({"name": state.name, "auto": state.auto, "manual": state.manual,
                   "schedule": state_rows(state)}, f, indent=1)
    return path


async def restore(host, port, path):
    with open(path) as f:
        backup = json.load(f)
    t = datetime.now()
    print(f"Restoring {path} ({backup['name']}), clock {t:%H:%M:%S}")
    await write_and_compare(host, port, backup["manual"], backup["schedule"], backup["auto"], t)


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
            await write_and_compare(h, p, before.manual, rows, True, t)
        elif a.cmd == "midnight-test":
            before = await read_all(h, p)
            backup = save_backup(before)
            print(f"Backup: {backup}  (if anything goes wrong: k7_test.py restore {backup})")
            print("Writing the test schedule with the lamp clock at 23:55...")
            await write_and_compare(h, p, before.manual, MIDNIGHT_TEST, True, datetime(2000, 1, 1, 23, 55))
            print("\nLook at the tank (Royal Blue only):")
            print("  ~100%            -> all-zero 00:00 slots are disabled (what the integration assumes)")
            print("  ~8%, going dark  -> they are real points; please report this")
            await asyncio.to_thread(input, "\nPress Enter to restore the original schedule and clock... ")
            await restore(h, p, backup)
        elif a.cmd == "restore":
            if len(a.args) != 1:
                raise SystemExit("restore FILE")
            await restore(h, p, a.args[0])
        elif a.cmd == "demo":
            if a.args not in (["start"], ["stop"]):
                raise SystemExit("demo start|stop")
            show_reply(f"DEMO {a.args[0]}", await send_command(h, p, CMD_DEMO, bytes([a.args[0] == "stop"])))
        else:
            raise SystemExit(__doc__)
        if a.cmd != "status":
            await asyncio.sleep(1.0)
        await status(h, p)
    except K7Error as err:
        print(f"ERROR: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
