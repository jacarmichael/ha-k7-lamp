"""Polls the lamp with ALL_READ and serialises the few writes we make."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from functools import wraps
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .acclimation import day_number, percent_for, scale
from .const import ALL_SET_SETTLE, DOMAIN, SCAN_INTERVAL
from .moon import moon_illumination, moon_level, phase_icon, phase_name, rise_set
from .effective import DEFAULT_MOON, build_effective, plan_window, rebuild_time, validate_moon
from .program import ProgramStore
from .protocol import (
    K7Error,
    K7State,
    Row,
    acked,
    photoperiod,
    read_all,
    state_rows,
    sort_rows,
    sync_time,
    validate_schedule,
    write_all_set,
)

_LOGGER = logging.getLogger(__name__)


class AutomaticWriteDeferred(HomeAssistantError):
    """The user's manual mode takes precedence over background maintenance."""


def _program_action(method):
    """Serialize the entire read/modify/write transaction, including storage."""
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        async with self._program_lock:
            return await method(self, *args, **kwargs)
    return wrapped


class K7Coordinator(DataUpdateCoordinator[K7State]):
    """Single coordinator per lamp; the lock keeps lamp access one-at-a-time."""

    def __init__(self, hass: HomeAssistant, entry: K7ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_HOST]}",
            update_interval=SCAN_INTERVAL,
        )
        self.host: str = entry.data[CONF_HOST]
        self.port: int = entry.data[CONF_PORT]
        self.lock = asyncio.Lock()
        self._program_lock = asyncio.Lock()
        self._last_plan_notice: str | None = None

    async def _read(self) -> K7State:
        """ALL_READ with one retry; the lamp copes badly with concurrent clients."""
        try:
            return await read_all(self.host, self.port)
        except K7Error:
            await asyncio.sleep(2)
            return await read_all(self.host, self.port)

    async def _async_update_data(self) -> K7State:
        async with self.lock:
            try:
                return await self._read()
            except K7Error as err:
                raise UpdateFailed(str(err)) from err

    async def async_apply_schedule(
        self, manual: list[int], schedule: list[Row], *, automatic: bool = False,
    ) -> bool:
        """Write the schedule with ALL_SET (auto mode, local time). Returns False if unchanged."""
        try:
            validate_schedule(manual, schedule)
        except ValueError as err:
            raise HomeAssistantError(f"Invalid schedule: {err}") from err
        schedule = sort_rows(schedule)
        async with self.lock:
            try:
                current = await self._read()
            except K7Error as err:
                raise HomeAssistantError(f"Lamp not reachable: {err}") from err
            self.async_set_updated_data(current)
            if automatic:
                if not current.auto:
                    raise AutomaticWriteDeferred("Lamp is in manual mode; automatic plan update deferred")
                manual = list(current.manual)
            if current.auto and current.manual == manual and state_rows(current) == schedule:
                _LOGGER.debug("Schedule already on the lamp; skipping ALL_SET")
                return False
            now = dt_util.now()
            if automatic:
                window = plan_window(self.target()[1], now)
                if window is None or now + timedelta(seconds=ALL_SET_SETTLE + 5) > window[1]:
                    raise AutomaticWriteDeferred("Safe write window ended while waiting for the lamp; automatic update deferred")
            try:
                reply = await write_all_set(
                    self.host, self.port, manual, schedule, True,
                    now.hour, now.minute, now.second,
                )
            except K7Error as err:
                raise HomeAssistantError(f"Writing schedule failed: {err}") from err
            if not acked(reply):
                raise HomeAssistantError(
                    f"Lamp did not acknowledge the schedule (reply: {reply.hex(' ') or 'none'})"
                )
            _LOGGER.info("Schedule written to %s", self.host)
            await asyncio.sleep(ALL_SET_SETTLE)
            try:
                actual = await self._read()
            except K7Error as err:
                raise HomeAssistantError("Schedule acknowledged but read-back failed; update remains pending") from err
            self.async_set_updated_data(actual)
            if not actual.auto or actual.manual != manual or state_rows(actual) != schedule:
                raise HomeAssistantError("Lamp read-back does not match the requested schedule")
        return True

    async def async_sync_clock(self) -> None:
        """Send HA's local time; the lamp runs its schedule on this clock."""
        async with self.lock:
            now = dt_util.now()
            try:
                reply = await sync_time(self.host, self.port, now.hour, now.minute, now.second)
            except K7Error as err:
                raise HomeAssistantError(f"Clock sync failed: {err}") from err
        if not acked(reply):
            raise HomeAssistantError("Lamp did not acknowledge the clock sync")
        _LOGGER.debug("Clock synced to %s on %s", now.strftime("%H:%M:%S"), self.host)

    # --- Program: target schedule in HA + acclimation + moonlight, rebuilt daily ---

    @property
    def _store(self) -> ProgramStore:
        return self.hass.data[DOMAIN]["program"]

    @property
    def _id(self) -> str:
        return self.config_entry.entry_id

    def program(self) -> dict[str, Any] | None:
        return self._store.get(self._id)

    def _validate(self, manual: list[int], schedule: list[Row]) -> list[Row]:
        try:
            validate_schedule(manual, schedule)
        except ValueError as err:
            raise HomeAssistantError(f"Invalid schedule: {err}") from err
        return sort_rows(schedule)

    def _new_program(self) -> dict[str, Any]:
        """Start a program from what the lamp runs now."""
        return {
            "target_manual": list(self.data.manual),
            "target_schedule": state_rows(self.data),
            "acclimation": None,
            "moon": None,
        }

    def target(self) -> tuple[list[int], list[Row]]:
        prog = self.program()
        if prog:
            return prog["target_manual"], prog["target_schedule"]
        return self.data.manual, state_rows(self.data)

    def effective(self) -> tuple[list[int], list[Row]] | None:
        """What was actually read from the lamp, not a newly calculated forecast."""
        if self.program() is None:
            return None
        return list(self.data.manual), state_rows(self.data)

    def plan_pending(self) -> bool:
        prog = self.program()
        if prog is None:
            return False
        applied = prog.get("applied")
        if prog.get("pending") or not applied:
            return True
        window = plan_window(prog["target_schedule"], dt_util.now())
        return window is not None and applied.get("cycle") != window[0].isoformat()

    def _notify(self, topic: str, message: str) -> None:
        persistent_notification.async_create(
            self.hass, message,
            title=f"{self.config_entry.title} {topic}",
            notification_id=f"{DOMAIN}_{topic}_{self._id}",
        )

    @_program_action
    async def async_rebuild(self) -> None:
        """Background catch-up and retries, only inside the target's dark window."""
        await self._async_rebuild_locked(explicit=False)

    def _plan_notice(self, key: str, message: str) -> None:
        if self._last_plan_notice != key:
            self._last_plan_notice = key
            self._notify("schedule", message)

    async def _async_rebuild_locked(self, *, explicit: bool) -> bool:
        """Caller owns _program_lock, so an older rebuild cannot clear a new program."""
        prog = self.program()
        if prog is None:
            return False
        now = dt_util.now()
        window = plan_window(prog["target_schedule"], now)
        cycle = window[0].isoformat() if window else None
        applied = prog.get("applied") or {}
        if not explicit:
            if window is None:
                self._plan_notice(str(now.date()), "No dark interval long enough for an automatic write. Apply changes explicitly or add a dark interval.")
                return False
            if applied.get("cycle") == cycle and not prog.get("pending"):
                return False
            if now >= window[1]:
                self._plan_notice(cycle, "The daily plan has not been confirmed. The safe write window has passed; it will be retried in the next dark window, or you can Apply explicitly.")
                return False
        # Use one fixed anchor for all retries of this daily cycle.
        anchor = now if explicit else window[0]
        cfg = self.hass.config
        manual, schedule = build_effective(prog, anchor, cfg.latitude, cfg.longitude)
        try:
            written = await self.async_apply_schedule(manual, schedule, automatic=not explicit)
        except AutomaticWriteDeferred as err:
            self._plan_notice(f"{cycle}:deferred", str(err))
            return False
        except HomeAssistantError:
            self._plan_notice(f"{cycle}:failed", "The plan could not be confirmed. Automatic updates retry every five minutes within the dark window and check the lamp before writing again.")
            raise
        acc = prog.get("acclimation")
        percent = percent_for(acc["start_pct"], acc["days"], date.fromisoformat(acc["started"]), anchor.date()) if acc else 100
        finished = acc is not None and percent >= 100
        if finished:
            prog = {**prog, "acclimation": None}
        if not prog.get("acclimation") and not (prog.get("moon") or {}).get("enabled"):
            await self._store.async_clear(self._id)
        else:
            await self._store.async_set(self._id, {
                **prog, "pending": False,
                "applied": {"cycle": cycle, "at": now.isoformat(), "percent": percent,
                            "manual": list(self.data.manual), "schedule": state_rows(self.data)},
            })
        if finished:
            self._notify("acclimation", "Acclimation finished; the full schedule was confirmed on the lamp.")
        self._last_plan_notice = None
        persistent_notification.async_dismiss(self.hass, f"{DOMAIN}_schedule_{self._id}")
        self.async_update_listeners()
        return written

    @_program_action
    async def async_set_target(self, manual: list[int], schedule: list[Row]) -> bool:
        """Apply a schedule; with a program it becomes the new target instead."""
        schedule = self._validate(manual, schedule)
        prog = self.program()
        if prog is None:
            return await self.async_apply_schedule(manual, schedule)
        await self._store.async_set(
            self._id, {**prog, "target_manual": list(manual), "target_schedule": schedule, "pending": True}
        )
        return await self._async_rebuild_locked(explicit=True)

    # --- Acclimation ---

    def acclimation_info(self) -> dict[str, Any] | None:
        prog = self.program()
        acc = prog and prog.get("acclimation")
        if not acc:
            return None
        started = date.fromisoformat(acc["started"])
        today = dt_util.now().date()
        return {
            "start_percent": acc["start_pct"],
            "days": acc["days"],
            "started": acc["started"],
            "ends": (started + timedelta(days=acc["days"])).isoformat(),
            "day": day_number(started, today),
            "percent": percent_for(acc["start_pct"], acc["days"], started, today),
            "applied_percent": (prog.get("applied") or {}).get("percent"),
            "applied_at": (prog.get("applied") or {}).get("at"),
            "target_manual": prog["target_manual"],
            "target_schedule": prog["target_schedule"],
        }

    @_program_action
    async def async_start_acclimation(
        self, manual: list[int], schedule: list[Row], start_pct: int, days: int,
        started: date | None = None,
    ) -> None:
        schedule = self._validate(manual, schedule)
        if not 1 <= start_pct <= 99 or not 1 <= days <= 120:
            raise HomeAssistantError("Start must be 1-99% and duration 1-120 days")
        started = started or dt_util.now().date()
        prog = self.program() or self._new_program()
        await self._store.async_set(self._id, {
            **prog,
            "target_manual": list(manual),
            "target_schedule": schedule,
            "acclimation": {"start_pct": start_pct, "days": days, "started": started.isoformat()},
            "pending": True,
        })
        await self._async_rebuild_locked(explicit=True)
        if info := self.acclimation_info():
            self._notify("acclimation",
                f"Acclimation started: {start_pct}% rising to 100% over {days} days "
                f"(until {info['ends']}). Today: {info['percent']}%. "
                "The lamp gets the next step daily, shortly before lights-on.")

    @_program_action
    async def async_stop_acclimation(self, keep_current: bool) -> None:
        info = self.acclimation_info()
        if info is None:
            return
        prog = self.program()
        target = (prog["target_manual"], prog["target_schedule"])
        if keep_current:
            if info["applied_percent"] is None:
                raise HomeAssistantError("No confirmed acclimation level is available yet; apply the target first or choose the full schedule")
            target = scale(*target, info["applied_percent"])
        await self._store.async_set(self._id, {
            **prog, "target_manual": target[0], "target_schedule": target[1], "acclimation": None, "pending": True,
        })
        await self._async_rebuild_locked(explicit=True)
        self._notify("acclimation", "Acclimation stopped; " + (
            "the lamp keeps today's reduced schedule." if keep_current
            else "the lamp is back on the full schedule."))

    # --- Moonlight ---

    def moon_config(self) -> dict[str, Any]:
        prog = self.program()
        cfg = prog.get("moon") if prog else None
        return cfg or {**DEFAULT_MOON, "enabled": False}

    @_program_action
    async def async_set_moon(self, config: dict[str, Any]) -> None:
        try:
            cfg = validate_moon({**self.moon_config(), **config})
        except (ValueError, TypeError) as err:
            raise HomeAssistantError(str(err)) from err
        prog = self.program()
        if prog is None:
            if not cfg["enabled"]:
                return
            prog = self._new_program()
        await self._store.async_set(self._id, {**prog, "moon": cfg, "pending": True})
        self._moon_cache = None  # before rebuild, which refreshes the sensors
        await self._async_rebuild_locked(explicit=True)

    _moon_cache: tuple[datetime, dict[str, Any]] | None = None

    def moon_info(self) -> dict[str, Any]:
        """Tonight's moon at the HA location (cached for 10 minutes)."""
        now = dt_util.now()
        if self._moon_cache and now - self._moon_cache[0] < timedelta(minutes=10):
            return self._moon_cache[1]
        lat, lon = self.hass.config.latitude, self.hass.config.longitude
        utc = dt_util.utcnow()
        frac, phase = moon_illumination(utc)
        rise, sets = rise_set(utc, lat, lon)
        cfg = self.moon_config()
        level = moon_level(utc, lat, lon, cfg["max_level"], cfg.get("follow_moon", True))
        info = {
            "illumination": round(frac * 100),
            "phase": round(phase, 3),
            "phase_name": phase_name(phase),
            "icon": phase_icon(phase),
            "next_rise": dt_util.as_local(rise).isoformat() if rise else None,
            "next_set": dt_util.as_local(sets).isoformat() if sets else None,
            "level_now": round(level) if cfg["enabled"] else 0,
            "config": cfg,
        }
        self._moon_cache = (now, info)
        return info

    # --- Photoperiod preference ---

    def day_start_pref(self) -> int | None:
        return self._store.pref(self._id, "day_start")

    async def async_set_day_start_pref(self, hour: int | None) -> None:
        await self._store.async_set_pref(self._id, "day_start", hour)

    def photoperiod(self) -> tuple[float, float] | None:
        return photoperiod(self.target()[1])

    def rebuild_time(self) -> tuple[int, int] | None:
        """Daily plan write time: in the dark, shortly before the target's lights-on."""
        return rebuild_time(self.target()[1])


K7ConfigEntry = ConfigEntry[K7Coordinator]
