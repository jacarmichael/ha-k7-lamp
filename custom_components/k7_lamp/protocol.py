"""TCP commands and lossless schedules for Noo-Psyche K7 lamps."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

CHANNELS = ["White", "Royal Blue", "Green", "UV", "Cyan", "Red"]
READ_ALL = bytes([0xAA, 0xA5, 0x10, 0x08, 0xBB])
HEADER = bytes([0xAB, 0xAA])
END = 0xBB


class K7Error(Exception):
    """Lamp unreachable or reply not understood."""


@dataclass
class K7State:
    name: str
    auto: bool
    manual: list[int]
    # (minute_of_day, [c0..c5]) sorted by time
    schedule: list[tuple[int, list[int]]] = field(default_factory=list)
    raw: bytes = b""

    def scheduled_levels(self, minute_of_day: int) -> list[int]:
        """Linearly interpolate the schedule at a minute of the day (wraps midnight)."""
        # The app uses all-zero midnight rows as disabled slots. Duplicate
        # active times use the last value for interpolation, but remain on wire.
        pts = sorted({m: lv for m, lv in self.schedule if m or any(lv)}.items())
        if not pts:
            return [0] * len(CHANNELS)
        if len(pts) == 1:
            return list(pts[0][1])
        prev = pts[-1]
        nxt = pts[0]
        for p in pts:
            if p[0] <= minute_of_day:
                prev = p
            else:
                nxt = p
                break
        else:
            nxt = pts[0]
        span = (nxt[0] - prev[0]) % 1440 or 1440
        frac = ((minute_of_day - prev[0]) % 1440) / span
        return [round(a + (b - a) * frac) for a, b in zip(prev[1], nxt[1])]

    def output_levels(self, minute_of_day: int) -> list[int]:
        """What the lamp should be emitting now, assuming its clock matches ours."""
        return self.scheduled_levels(minute_of_day) if self.auto else list(self.manual)


def _parse_frame(frame: bytes) -> K7State | None:
    """Decode a reply starting at an AB AA header; None if it doesn't fit."""
    if len(frame) < 12 or frame[:2] != HEADER:
        return None
    pos = 4
    manual = list(frame[pos:pos + 6])
    pos += 6
    count = frame[pos]
    pos += 1
    if count > 24 or pos + count * 8 + 1 > len(frame):
        return None
    if any(v > 100 for v in manual):
        return None
    points: list[tuple[int, list[int]]] = []
    for _ in range(count):
        h, m, *levels = frame[pos:pos + 8]
        pos += 8
        if h > 23 or m > 59 or any(v > 100 for v in levels):
            return None
        points.append((h * 60 + m, levels))
    mode = frame[pos]
    pos += 1
    if mode > 1:
        return None
    end = frame.find(bytes([END]), pos)
    if end == -1:
        return None  # incomplete
    name = frame[pos:end][:11].split(b"\x00")[0].decode("ascii", "replace")
    return K7State(
        name=name,
        auto=mode == 1,
        manual=manual,
        schedule=sorted(points, key=lambda p: p[0]),
        raw=frame[:end + 1],
    )


def parse_reply(data: bytes) -> K7State | None:
    """Try every AB AA occurrence until one parses (the first is often an echo)."""
    start = 0
    while (i := data.find(HEADER, start)) != -1:
        if state := _parse_frame(data[i:]):
            return state
        start = i + 1
    return None


async def read_all(host: str, port: int, timeout: float = 6.0) -> K7State:
    """Open a connection, send ALL_READ, return the decoded state, close."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=3
        )
    except (OSError, asyncio.TimeoutError) as err:
        raise K7Error(f"cannot connect to {host}:{port}: {err}") from err
    data = b""
    try:
        writer.write(READ_ALL)
        await writer.drain()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while (remaining := deadline - loop.time()) > 0:
            try:
                chunk = await asyncio.wait_for(reader.read(1024), remaining)
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            data += chunk
            if state := parse_reply(data):
                return state
    except OSError as err:
        raise K7Error(f"connection error: {err}") from err
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 2)
        except (OSError, asyncio.TimeoutError):
            pass
    raise K7Error(f"unrecognised reply ({len(data)} bytes): {data.hex(' ')}")


# --- Write commands; ALL_SET blocks the lamp and rewrites flash ---

CMD_SYNC_TIME = (0x10, 0x03)
CMD_CHANGE_MODE = (0x10, 0x04)
CMD_HAND_LUMINANCE = (0x10, 0x05)
CMD_PREVIEW = (0x10, 0x06)


def build_frame(cmd: tuple[int, int], data: bytes = b"") -> bytes:
    return bytes([0xAA, 0xA5, *cmd]) + data + bytes([END])


def _levels(levels: list[int]) -> bytes:
    if len(levels) != len(CHANNELS) or any(type(v) is not int or not 0 <= v <= 100 for v in levels):
        raise ValueError(f"need {len(CHANNELS)} levels in 0-100, got {levels}")
    return bytes(levels)


async def send_command(
    host: str, port: int, cmd: tuple[int, int], data: bytes = b"", wait: float = 1.0
) -> bytes:
    """Send one command on a fresh connection; return whatever the lamp replies."""
    frame = build_frame(cmd, data)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=3
        )
    except (OSError, asyncio.TimeoutError) as err:
        raise K7Error(f"cannot connect to {host}:{port}: {err}") from err
    reply = b""
    try:
        writer.write(frame)
        await writer.drain()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while (remaining := deadline - loop.time()) > 0:
            try:
                chunk = await asyncio.wait_for(reader.read(1024), remaining)
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            reply += chunk
    except OSError as err:
        raise K7Error(f"connection error: {err}") from err
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 2)
        except (OSError, asyncio.TimeoutError):
            pass
    return reply


async def set_mode(host: str, port: int, auto: bool) -> bytes:
    return await send_command(host, port, CMD_CHANGE_MODE, bytes([1 if auto else 0]))


async def set_levels(host: str, port: int, levels: list[int]) -> bytes:
    return await send_command(host, port, CMD_HAND_LUMINANCE, _levels(levels))


async def preview_levels(host: str, port: int, levels: list[int]) -> bytes:
    return await send_command(host, port, CMD_PREVIEW, _levels(levels))


async def sync_time(host: str, port: int, hour: int, minute: int, second: int) -> bytes:
    return await send_command(host, port, CMD_SYNC_TIME, bytes([hour, minute, second]))


# --- Full schedule write (ALL_SET) and the app's QR share format ---

CMD_ALL_SET = (0x10, 0x07)
SLOTS = 24

# A schedule here is a list of 24 rows: [hour, minute, c0..c5]
Row = list[int]


def validate_schedule(manual: list[int], schedule: list[Row]) -> None:
    _levels(manual)
    if len(schedule) != SLOTS:
        raise ValueError(f"schedule needs {SLOTS} rows, got {len(schedule)}")
    for i, row in enumerate(schedule):
        if len(row) != 2 + len(CHANNELS):
            raise ValueError(f"row {i} needs {2 + len(CHANNELS)} values")
        if any(type(v) is not int for v in row):
            raise ValueError(f"row {i} must contain integers")
        h, m, *levels = row
        if not (0 <= h <= 23 and 0 <= m <= 59) or any(not 0 <= v <= 100 for v in levels):
            raise ValueError(f"row {i} out of range: {row}")


def state_rows(state: K7State) -> list[Row]:
    """Preserve every slot; short replies are padded with disabled app slots."""
    rows = [[m // 60, m % 60, *levels] for m, levels in state.schedule]
    rows.extend([[0] * 8 for _ in range(max(0, SLOTS - len(rows)))])
    return sort_rows(rows)


def sort_rows(schedule: list[Row]) -> list[Row]:
    """Stable chronological order, including the order of duplicate times."""
    return sorted((list(r) for r in schedule), key=lambda r: (r[0], r[1]))


def active_rows(schedule: list[Row]) -> list[Row]:
    """Calculation view; never use this to reconstruct the raw protocol slots."""
    by_time = {r[0] * 60 + r[1]: r for r in schedule if any(r)}
    return [list(r) for _, r in sorted(by_time.items())]


def build_all_set(
    manual: list[int], schedule: list[Row], auto: bool, hour: int, minute: int, second: int
) -> bytes:
    """203-byte payload: manual(6) count(1) 24x[h m c0..c5] mode(1) hh mm ss."""
    validate_schedule(manual, schedule)
    data = bytes(manual) + bytes([SLOTS])
    for row in sorted(schedule, key=lambda r: (r[0], r[1])):
        data += bytes(row)
    return data + bytes([1 if auto else 0, hour, minute, second])


async def write_all_set(
    host: str, port: int, manual: list[int], schedule: list[Row],
    auto: bool, hour: int, minute: int, second: int,
) -> bytes:
    data = build_all_set(manual, schedule, auto, hour, minute, second)
    return await send_command(host, port, CMD_ALL_SET, data, wait=4.0)


def acked(reply: bytes) -> bool:
    """The lamp answers ab aa a5 a1 on receipt (a2 follows only when something changed)."""
    return bytes([0xAB, 0xAA, 0xA5, 0xA1]) in reply


def to_qr_payload(manual: list[int], schedule: list[Row]) -> str:
    """Noo-Psyche app share format: 6 manual bytes, '#', 24 x 8 bytes, all as hex."""
    validate_schedule(manual, schedule)
    rows = "".join(bytes(r).hex() for r in sorted(schedule, key=lambda r: (r[0], r[1])))
    return f"{bytes(manual).hex()}#{rows}".upper()


def parse_qr_payload(text: str) -> tuple[list[int], list[Row]]:
    """Decode a QR payload; raises ValueError if it isn't the K7 format."""
    text = "".join(text.split())
    head, sep, body = text.partition("#")
    if not sep or len(head) != 12 or len(body) != SLOTS * 16:
        raise ValueError("not a K7 schedule QR code (expected 12 hex, '#', 384 hex)")
    try:
        manual = list(bytes.fromhex(head))
        raw = bytes.fromhex(body)
    except ValueError as err:
        raise ValueError("QR payload is not hexadecimal") from err
    schedule = [list(raw[i:i + 8]) for i in range(0, len(raw), 8)]
    validate_schedule(manual, schedule)
    return manual, sort_rows(schedule)


# --- Photoperiod helpers ---

def _lit(row: Row) -> bool:
    return any(v > 0 for v in row[2:])


def day_start(schedule: list[Row]) -> float | None:
    """Hour of the first lit slot that follows a dark slot (None if never dark or never lit)."""
    rows = active_rows(schedule)
    for i, row in enumerate(rows):
        if _lit(row) and not _lit(rows[i - 1]):
            return row[0] + row[1] / 60
    return None


def photoperiod(schedule: list[Row]) -> tuple[float, float] | None:
    """(start_hour, end_hour) where end is the first dark slot after the start."""
    start = day_start(schedule)
    if start is None:
        return None
    rows = active_rows(schedule)
    idx = next(i for i, r in enumerate(rows) if _lit(r) and not _lit(rows[i - 1]))
    for k in range(1, len(rows) + 1):
        row = rows[(idx + k) % len(rows)]
        if not _lit(row):
            return start, row[0] + row[1] / 60
    return None


def shift_rows(schedule: list[Row], hours: float) -> list[Row]:
    """Move active timestamps by real hours; retain disabled slots as padding."""
    rows = []
    delta = round(hours * 60)
    for row in schedule:
        if not any(row):
            rows.append(list(row))
            continue
        minute = (row[0] * 60 + row[1] + delta) % 1440
        rows.append([minute // 60, minute % 60, *row[2:]])
    # A dark point shifted to midnight becomes disabled in the app's format.
    # It is only safe to omit when both neighbours are dark as well.
    active = active_rows(schedule)
    for i, row in enumerate(active):
        if (row[0] * 60 + row[1] + delta) % 1440 == 0 and not _lit(row):
            if _lit(active[i - 1]) or _lit(active[(i + 1) % len(active)]):
                raise ValueError("Cannot shift a dark boundary to 00:00: K7 treats it as a disabled slot")
    return sort_rows(rows)


def align_day_start(schedule: list[Row], start_hour: int) -> list[Row]:
    """Shift so the day starts at start_hour; unchanged if the schedule has no dark period."""
    current = day_start(schedule)
    if current is None:
        return [list(r) for r in schedule]
    return shift_rows(schedule, start_hour - current)
