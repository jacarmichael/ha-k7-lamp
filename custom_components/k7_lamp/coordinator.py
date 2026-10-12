"""Polls the lamp with ALL_READ and serialises the few writes we make."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from functools import wraps
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .acclimation import day_number, percent_for, scale
from .const import ALL_SET_SETTLE, DOMAIN, SCAN_INTERVAL
from .effective import rebuild_time
from .model import DEFAULT_MOON, Acclimation, Program, validate_moon
from .moon import moon_illumination, moon_level, phase_icon, phase_name, rise_set
from .planner import (
    FAILED,
    WRITTEN,
    Defer,
    Unchanged,
    Write,
    check_lamp,
    confirmed,
    is_pending,
    lamp_runs,
    plan,
)
from .program import ProgramStore
from .protocol import (
    K7Error,
    K7State,
    Row,
    acked,
    photoperiod,
    read_all,
    sort_rows,
    state_rows,
    sync_time,
    validate_schedule,
    write_all_set,
)

_LOGGER = logging.getLogger(__name__)


class PlanDeferred(HomeAssistantError):
    """The lamp was read, and the planner decided not to write now."""

    def __init__(self, defer: Defer) -> None:
        super().__init__(defer.message)
        self.defer = defer


class ReadbackError(HomeAssistantError):
    """The lamp acknowledged an ALL_SET but doesn't (verifiably) run it."""


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
        self._failed_polls = 0

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
                state = await self._read()
            except K7Error as err:
                # Another client (the app, the Mac app) can briefly block the lamp; one miss is noise.
                self._failed_polls += 1
                if self.data is not None and self._failed_polls < 2:
                    _LOGGER.debug("Poll of %s failed, keeping the last state: %s", self.host, err)
                    return self.data
                raise UpdateFailed(str(err)) from err
        self._failed_polls = 0
        return state

    async def async_apply_schedule(self, manual: list[int], schedule: list[Row]) -> bool:
        """Write a schedule now (no program). Returns False if the lamp already has it."""
        schedule = self._validate(manual, schedule)
        return await self._write_plan(Write(list(manual), schedule, None, 100), explicit=True)

    async def _write_plan(self, step: Write, *, explicit: bool) -> bool:
        """Read, decide, ALL_SET and read back under the lamp lock. False if unchanged."""
        async with self.lock:
            try:
                current = await self._read()
            except K7Error as err:
                raise HomeAssistantError(f"Lamp not reachable: {err}") from err
            self.async_set_updated_data(current)
            action = check_lamp(step, current, self.program(), dt_util.now(), explicit=explicit)
            if isinstance(action, Defer):
                raise PlanDeferred(action)
            if isinstance(action, Unchanged):
                _LOGGER.debug("Schedule already on the lamp; skipping ALL_SET")
                return False
            now = dt_util.now()
            try:
                reply = await write_all_set(
                    self.host, self.port, action.manual, action.schedule, True,
                    now.hour, now.minute, now.second,
                )
            except K7Error as err:
                raise HomeAssistantError(f"Writing schedule failed: {err}") from err
            if not acked(reply):
                raise HomeAssistantError(
                    f"Lamp did not acknowledge the schedule (reply: {reply.hex(' ') or 'none'})"
                )
            _LOGGER.info("Schedule written to %s", self.host)
            if (prog := self.program()) is not None:
                # Before the read-back: whatever it shows, this cycle has had its write.
                await self._store.async_set(self._id, replace(prog, written_cycle=action.cycle))
            await asyncio.sleep(ALL_SET_SETTLE)
            try:
                actual = await self._read()
            except K7Error as err:
                raise ReadbackError(
                    "Schedule acknowledged but read-back failed; update remains pending"
                ) from err
            self.async_set_updated_data(actual)
            if not lamp_runs(actual, action.manual, action.schedule):
                raise ReadbackError("Lamp read-back does not match the requested schedule")
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

    def program(self) -> Program | None:
        return self._store.get(self._id)

    async def _set_program(self, prog: Program) -> None:
        """Store a program the user configured; it replaces one set aside as invalid."""
        await self._store.async_set(self._id, prog)
        ir.async_delete_issue(self.hass, DOMAIN, f"invalid_program_{self._id}")

    def _validate(self, manual: list[int], schedule: list[Row]) -> list[Row]:
        try:
            validate_schedule(manual, schedule)
        except ValueError as err:
            raise HomeAssistantError(f"Invalid schedule: {err}") from err
        return sort_rows(schedule)

    def _new_program(self) -> Program:
        """Start a program from what the lamp runs now."""
        return Program(list(self.data.manual), state_rows(self.data))

    def target(self) -> tuple[list[int], list[Row]]:
        if prog := self.program():
            return prog.target_manual, prog.target_schedule
        return self.data.manual, state_rows(self.data)

    def plan_pending(self) -> bool:
        prog = self.program()
        return prog is not None and is_pending(prog, dt_util.now())

    def notify(self, topic: str, message: str) -> None:
        persistent_notification.async_create(
            self.hass, message,
            title=f"{self.config_entry.title} {topic}",
            notification_id=f"{DOMAIN}_{topic}_{self._id}",
        )

    @_program_action
    async def async_rebuild(self) -> None:
        """Background catch-up and retries, only inside the target's dark window."""
        await self._async_rebuild_locked(explicit=False)

    def _plan_notice(self, defer: Defer) -> None:
        if self._last_plan_notice != defer.key:
            self._last_plan_notice = defer.key
            self.notify("schedule", defer.message)

    async def _async_rebuild_locked(self, *, explicit: bool) -> bool:
        """Caller owns _program_lock, so an older rebuild cannot clear a new program."""
        prog = self.program()
        if prog is None:
            return False
        cfg = self.hass.config
        step = plan(prog, dt_util.now(), cfg.latitude, cfg.longitude, explicit=explicit)
        if step is None:
            return False
        if isinstance(step, Defer):
            self._plan_notice(step)
            return False
        try:
            written = await self._write_plan(step, explicit=explicit)
        except PlanDeferred as err:
            self._plan_notice(err.defer)
            return False
        except ReadbackError as err:
            # Same key as the planner's later deferral, so this is one notification.
            self._plan_notice(Defer(f"{step.cycle}:written", str(err) if explicit else WRITTEN))
            raise
        except HomeAssistantError as err:
            self._plan_notice(Defer(f"{step.cycle}:failed", str(err) if explicit else FAILED))
            raise
        # Re-read: the write recorded its cycle in the program.
        prog, finished = confirmed(self.program(), step, dt_util.now())
        if prog is None:
            await self._store.async_clear(self._id)
        else:
            await self._store.async_set(self._id, prog)
        if finished:
            self.notify("acclimation", "Acclimation finished; the full schedule was confirmed on the lamp.")
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
        await self._set_program(replace(prog, target_manual=manual, target_schedule=schedule, pending=True))
        return await self._async_rebuild_locked(explicit=True)

    # --- Acclimation ---

    def acclimation_info(self) -> dict[str, Any] | None:
        prog = self.program()
        if prog is None or (acc := prog.acclimation) is None:
            return None
        today = dt_util.now().date()
        return {
            "start_percent": acc.start_pct,
            "days": acc.days,
            "started": acc.started.isoformat(),
            "ends": (acc.started + timedelta(days=acc.days)).isoformat(),
            "day": day_number(acc.started, today),
            "percent": percent_for(acc.start_pct, acc.days, acc.started, today),
            "applied_percent": prog.applied.percent if prog.applied else None,
            "applied_at": prog.applied.at if prog.applied else None,
            "target_manual": prog.target_manual,
            "target_schedule": prog.target_schedule,
        }

    @_program_action
    async def async_start_acclimation(
        self, manual: list[int], schedule: list[Row], start_pct: int, days: int,
        started: date | None = None,
    ) -> None:
        schedule = self._validate(manual, schedule)
        try:
            acc = Acclimation(start_pct, days, started or dt_util.now().date())
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        prog = self.program() or self._new_program()
        await self._set_program(replace(
            prog, target_manual=manual, target_schedule=schedule, acclimation=acc, pending=True,
        ))
        await self._async_rebuild_locked(explicit=True)
        if info := self.acclimation_info():
            self.notify("acclimation",
                f"Acclimation started: {start_pct}% rising to 100% over {days} days "
                f"(until {info['ends']}). Today: {info['percent']}%. "
                "The lamp gets the next step daily, shortly before lights-on.")

    @_program_action
    async def async_stop_acclimation(self, keep_current: bool) -> None:
        info = self.acclimation_info()
        if info is None:
            return
        prog = self.program()
        manual, schedule = prog.target_manual, prog.target_schedule
        if keep_current:
            if info["applied_percent"] is None:
                raise HomeAssistantError(
                    "No confirmed acclimation level is available yet; "
                    "apply the target first or choose the full schedule"
                )
            manual, schedule = scale(manual, schedule, info["applied_percent"])
        await self._set_program(replace(
            prog, target_manual=manual, target_schedule=schedule, acclimation=None, pending=True,
        ))
        await self._async_rebuild_locked(explicit=True)
        self.notify("acclimation", "Acclimation stopped; " + (
            "the lamp keeps today's reduced schedule." if keep_current
            else "the lamp is back on the full schedule."))

    # --- Moonlight ---

    def moon_config(self) -> dict[str, Any]:
        prog = self.program()
        return (prog.moon if prog else None) or {**DEFAULT_MOON, "enabled": False}

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
        await self._set_program(replace(prog, moon=cfg, pending=True))
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
        level = moon_level(utc, lat, lon, cfg["max_level"], cfg["follow_moon"])
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
