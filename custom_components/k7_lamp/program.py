"""Per-lamp program: the target schedule plus acclimation and moonlight settings.

When a lamp has a program, its target schedule lives in HA and the lamp gets a
daily-built "effective" schedule (target -> acclimation scaling -> moon overlay).
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION
from .effective import validate_moon
from .protocol import validate_schedule


def validate_program(program: dict[str, Any]) -> dict[str, Any]:
    """Check persisted input before it can drive a lamp or the dashboard."""
    prog = deepcopy(program)
    validate_schedule(prog["target_manual"], prog["target_schedule"])
    if (moon := prog.get("moon")) is not None:
        prog["moon"] = validate_moon(moon)
    if acc := prog.get("acclimation"):
        if type(acc["start_pct"]) is not int or not 1 <= acc["start_pct"] <= 99:
            raise ValueError("Invalid acclimation start percent")
        if type(acc["days"]) is not int or not 1 <= acc["days"] <= 120:
            raise ValueError("Invalid acclimation duration")
        date.fromisoformat(acc["started"])
    if applied := prog.get("applied"):
        validate_schedule(applied["manual"], applied["schedule"])
        if type(applied["percent"]) is not int or not 1 <= applied["percent"] <= 100:
            raise ValueError("Invalid applied percent")
        for key in ("at", "cycle"):
            if applied.get(key) is not None and datetime.fromisoformat(applied[key]).tzinfo is None:
                raise ValueError("Applied plan time must include a timezone")
    return prog


class ProgramStore:
    """.storage/k7_lamp.program: {"programs": {entry_id: {...}}, "prefs": {entry_id: {...}}}"""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.program")
        self.programs: dict[str, dict[str, Any]] = {}
        self.prefs: dict[str, dict[str, Any]] = {}
        self.invalid_programs: dict[str, Any] = {}
        self.errors: dict[str, str] = {}
        self._save_lock = asyncio.Lock()

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data is None:
            data = {"programs": await self._migrate(), "prefs": {}}
            await self._store.async_save(data)
        self.prefs = data.get("prefs", {})
        self.invalid_programs = data.get("invalid_programs", {})
        for entry_id, program in data.get("programs", {}).items():
            try:
                self.programs[entry_id] = validate_program(program)
            except (ValueError, TypeError, KeyError, AttributeError) as err:
                self.invalid_programs[entry_id] = program
                self.errors[entry_id] = str(err)
        for entry_id in self.invalid_programs:
            if entry_id not in self.programs:
                self.errors.setdefault(entry_id, "Previously invalid program remains paused")
        if self.errors:
            await self._save()

    async def _migrate(self) -> dict[str, dict[str, Any]]:
        """Bring over acclimation runs saved by v0.3.0."""
        old = await Store(self._hass, STORAGE_VERSION, f"{DOMAIN}.acclimation").async_load() or {}
        return {
            entry_id: {
                "target_manual": run["target_manual"],
                "target_schedule": run["target_schedule"],
                "acclimation": {k: run[k] for k in ("start_pct", "days", "started")},
                "moon": None,
            }
            for entry_id, run in old.get("runs", {}).items()
        }

    async def _save(self) -> None:
        async with self._save_lock:
            await self._store.async_save(deepcopy({
                "programs": self.programs, "prefs": self.prefs,
                "invalid_programs": self.invalid_programs,
            }))

    def get(self, entry_id: str) -> dict[str, Any] | None:
        return self.programs.get(entry_id)

    async def async_set(self, entry_id: str, program: dict[str, Any]) -> None:
        self.programs[entry_id] = validate_program(program)
        self.errors.pop(entry_id, None)
        await self._save()

    async def async_clear(self, entry_id: str) -> None:
        if self.programs.pop(entry_id, None) is not None:
            await self._save()

    def pref(self, entry_id: str, key: str, default: Any = None) -> Any:
        return self.prefs.get(entry_id, {}).get(key, default)

    async def async_set_pref(self, entry_id: str, key: str, value: Any) -> None:
        self.prefs.setdefault(entry_id, {})[key] = value
        await self._save()
