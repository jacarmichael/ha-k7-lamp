"""Polls the lamp with ALL_READ and serialises the few writes we make."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .acclimation import day_number, percent_for, scale
from .const import ALL_SET_SETTLE, DOMAIN, HAND_SETTLE, SCAN_INTERVAL
from .moon import moon_illumination, moon_level, phase_icon, phase_name, rise_set
from .effective import DEFAULT_MOON, build_effective, rebuild_time
from .program import ProgramStore
from .protocol import (
    K7Error,
    K7State,
    Row,
    acked,
    photoperiod,
    read_all,
    set_levels,
    set_mode,
    state_rows,
    sync_time,
    validate_schedule,
    write_all_set,
)

_LOGGER = logging.getLogger(__name__)


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
        self._busy_until = 0.0  # loop time until which the lamp ignores us (after HAND_LUMINANCE)

    async def _settle(self) -> None:
        """Wait until the lamp answers again; call with the lock held."""
        if (wait := self._busy_until - self.hass.loop.time()) > 0:
            await asyncio.sleep(wait)

    async def _read(self) -> K7State:
        """ALL_READ with one retry; the lamp copes badly with concurrent clients."""
        try:
            return await read_all(self.host, self.port)
        except K7Error:
            await asyncio.sleep(2)
            return await read_all(self.host, self.port)

    async def _async_update_data(self) -> K7State:
        async with self.lock:
            await self._settle()
            try:
                return await self._read()
            except K7Error as err:
                raise UpdateFailed(str(err)) from err

    async def async_apply_schedule(self, manual: list[int], schedule: list[Row]) -> bool:
        """Write the schedule with ALL_SET (auto mode, local time). Returns False if unchanged."""
        try:
            validate_schedule(manual, schedule)
        except ValueError as err:
            raise HomeAssistantError(f"Invalid schedule: {err}") from err
        schedule = sorted(list(r) for r in schedule)
        async with self.lock:
            await self._settle()
            try:
                current = await self._read()
            except K7Error as err:
                raise HomeAssistantError(f"Lamp not reachable: {err}") from err
            if current.auto and current.manual == manual and state_rows(current) == schedule:
                _LOGGER.debug("Schedule already on the lamp; skipping ALL_SET")
                return False
            now = dt_util.now()
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
                self.async_set_updated_data(await self._read())
            except K7Error:
                pass  # the next poll will catch up
        return True

    async def async_sync_clock(self) -> None:
        """Send HA's local time; the lamp runs its schedule on this clock."""
        async with self.lock:
            await self._settle()
            now = dt_util.now()
            try:
                reply = await sync_time(self.host, self.port, now.hour, now.minute, now.second)
            except K7Error as err:
                raise HomeAssistantError(f"Clock sync failed: {err}") from err
        if not acked(reply):
            raise HomeAssistantError("Lamp did not acknowledge the clock sync")
        _LOGGER.debug("Clock synced to %s on %s", now.strftime("%H:%M:%S"), self.host)

    async def async_set_mode(self, auto: bool) -> None:
        """Switch auto/manual; back in auto, today's plan is written if a program runs."""
        async with self.lock:
            await self._settle()
            try:
                reply = await set_mode(self.host, self.port, auto)
            except K7Error as err:
                raise HomeAssistantError(f"Mode change failed: {err}") from err
            if not acked(reply):
                raise HomeAssistantError("Lamp did not acknowledge the mode change")
            _LOGGER.info("%s switched to %s mode", self.host, "auto" if auto else "manual")
            try:
                self.async_set_updated_data(await self._read())
            except K7Error:
                pass  # the next poll will catch up
        if auto and self.program() is not None:
            await self.async_rebuild()

    async def async_set_manual_level(self, channel: int, value: int) -> None:
        """Set one channel's manual level, keeping the others."""
        await self._async_write_manual(channel, value)

    async def async_set_manual_levels(self, levels: list[int]) -> None:
        """Set all six manual levels."""
        if len(levels) != 6 or any(not 0 <= v <= 100 for v in levels):
            raise HomeAssistantError("Need 6 levels from 0 to 100")
        await self._async_write_manual(None, list(levels))

    async def _async_write_manual(self, channel: int | None, value: int | list[int]) -> None:
        """HAND_LUMINANCE, switching to manual first; the schedule is untouched.

        One channel is merged into the current levels inside the lock, so quick
        changes to different channels don't overwrite each other.
        """
        async with self.lock:
            await self._settle()
            if channel is None:
                levels = value
            else:
                levels = list(self.data.manual)
                levels[channel] = value
            try:
                if self.data.auto:
                    if not acked(await set_mode(self.host, self.port, False)):
                        raise HomeAssistantError("Lamp did not acknowledge the switch to manual")
                    await asyncio.sleep(0.5)
                reply = await set_levels(self.host, self.port, levels)
            except K7Error as err:
                raise HomeAssistantError(f"Setting levels failed: {err}") from err
            # The lamp doesn't answer for a few seconds after HAND_LUMINANCE, so record
            # the new levels here and let the next poll (which waits) confirm them.
            self._busy_until = self.hass.loop.time() + HAND_SETTLE
            if not acked(reply):
                raise HomeAssistantError("Lamp did not acknowledge the new levels")
            self.async_set_updated_data(replace(self.data, auto=False, manual=levels))
        # Keep the program's copy in step, so going back to auto doesn't rewrite the old levels.
        if (prog := self.program()) is not None:
            await self._store.async_set(self._id, {**prog, "target_manual": levels})

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
        return sorted(list(r) for r in schedule)

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
        prog = self.program()
        if prog is None:
            return None
        cfg = self.hass.config
        return build_effective(prog, dt_util.now(), cfg.latitude, cfg.longitude)

    def _notify(self, topic: str, message: str) -> None:
        persistent_notification.async_create(
            self.hass, message,
            title=f"{self.config_entry.title} {topic}",
            notification_id=f"{DOMAIN}_{topic}_{self._id}",
        )

    async def async_rebuild(self) -> None:
        """Write today's effective schedule (daily before lights-on, on startup and on changes)."""
        prog = self.program()
        if prog is None:
            return
        info = self.acclimation_info()
        if prog.get("acclimation") and info and info["percent"] >= 100:
            prog = {**prog, "acclimation": None}
            await self._store.async_set(self._id, prog)
            self._notify("acclimation", "Acclimation finished; the lamp is on the full schedule.")
            _LOGGER.info("Acclimation finished on %s", self.host)
        if not prog.get("acclimation") and not (prog.get("moon") or {}).get("enabled"):
            # Nothing left to layer on: write the plain target and go back to direct writes.
            await self.async_apply_schedule(prog["target_manual"], prog["target_schedule"])
            await self._store.async_clear(self._id)
        else:
            await self.async_apply_schedule(*self.effective())
        self.async_update_listeners()

    async def async_set_target(self, manual: list[int], schedule: list[Row]) -> bool:
        """Apply a schedule; with a program it becomes the new target instead."""
        schedule = self._validate(manual, schedule)
        prog = self.program()
        if prog is None:
            return await self.async_apply_schedule(manual, schedule)
        await self._store.async_set(
            self._id, {**prog, "target_manual": list(manual), "target_schedule": schedule}
        )
        await self.async_rebuild()
        return True

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
            "target_manual": prog["target_manual"],
            "target_schedule": prog["target_schedule"],
        }

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
        })
        await self.async_rebuild()
        if info := self.acclimation_info():
            self._notify("acclimation",
                f"Acclimation started: {start_pct}% rising to 100% over {days} days "
                f"(until {info['ends']}). Today: {info['percent']}%. "
                "The lamp gets the next step daily, shortly before lights-on.")

    async def async_stop_acclimation(self, keep_current: bool) -> None:
        info = self.acclimation_info()
        if info is None:
            return
        prog = self.program()
        target = (prog["target_manual"], prog["target_schedule"])
        if keep_current:  # today's reduced schedule becomes the new target
            target = scale(*target, info["percent"])
        await self._store.async_set(self._id, {
            **prog, "target_manual": target[0], "target_schedule": target[1], "acclimation": None,
        })
        await self.async_rebuild()
        self._notify("acclimation", "Acclimation stopped; " + (
            "the lamp keeps today's reduced schedule." if keep_current
            else "the lamp is back on the full schedule."))

    # --- Moonlight ---

    def moon_config(self) -> dict[str, Any]:
        prog = self.program()
        cfg = prog.get("moon") if prog else None
        return cfg or {**DEFAULT_MOON, "enabled": False}

    async def async_set_moon(self, config: dict[str, Any]) -> None:
        cfg = {**DEFAULT_MOON, **self.moon_config(), **config}
        cfg["channels"] = {str(k): int(v) for k, v in cfg["channels"].items() if int(v) > 0}
        if not 1 <= int(cfg["max_level"]) <= 50:
            raise HomeAssistantError("Maximum moon level must be 1-50%")
        prog = self.program()
        if prog is None:
            if not cfg["enabled"]:
                return
            prog = self._new_program()
        await self._store.async_set(self._id, {**prog, "moon": cfg})
        self._moon_cache = None  # before rebuild, which refreshes the sensors
        await self.async_rebuild()

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

    def photoperiod(self) -> tuple[int, int] | None:
        return photoperiod(self.target()[1])

    def rebuild_time(self) -> tuple[int, int]:
        """Daily plan write time: in the dark, shortly before the target's lights-on."""
        return rebuild_time(self.target()[1])


K7ConfigEntry = ConfigEntry[K7Coordinator]
