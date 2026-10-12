"""Per-lamp programs in HA storage (.storage/k7_lamp.program); the model is in model.py."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, STORAGE_VERSION
from .model import Program
from .planner import carried_over

# 1.2: applied/pending/written_cycle bookkeeping for the daily plan.
STORAGE_MINOR_VERSION = 2
LOAD_ERRORS = (ValueError, TypeError, KeyError, AttributeError)


def _carry_over(raw: dict[str, Any]) -> None:
    """Older versions wrote their daily plan themselves; don't rewrite or warn after upgrading."""
    if "applied" in raw:
        return
    try:
        applied = carried_over(Program.from_dict(raw), dt_util.now())
    except LOAD_ERRORS:
        return  # quarantined on load
    if applied is not None:
        raw["applied"] = {"cycle": applied.cycle, "percent": applied.percent, "at": None}


class _ProgramStorage(Store[dict[str, Any]]):
    async def _async_migrate_func(self, old_major_version, old_minor_version, old_data):
        if old_major_version != STORAGE_VERSION:
            raise NotImplementedError
        if old_minor_version < 2:
            for raw in old_data.get("programs", {}).values():
                _carry_over(raw)
        return old_data


class ProgramStore:
    """{"programs": {entry_id: program}, "prefs": {entry_id: {...}},
    "invalid_programs": {entry_id: {"program": raw, "error": message}}}"""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store = _ProgramStorage(
            hass, STORAGE_VERSION, f"{DOMAIN}.program", minor_version=STORAGE_MINOR_VERSION
        )
        self.programs: dict[str, Program] = {}
        self.prefs: dict[str, dict[str, Any]] = {}
        # Programs that failed validation: kept until a new program replaces them.
        self.invalid_programs: dict[str, dict[str, Any]] = {}
        self._save_lock = asyncio.Lock()

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data is None:
            data = {"programs": await self._migrate(), "prefs": {}}
            await self._store.async_save(data)
        self.prefs = data.get("prefs", {})
        self.invalid_programs = data.get("invalid_programs", {})
        quarantined = False
        for entry_id, raw in data.get("programs", {}).items():
            try:
                self.programs[entry_id] = Program.from_dict(raw)
            except LOAD_ERRORS as err:
                self.invalid_programs[entry_id] = {"program": raw, "error": str(err)}
                quarantined = True
        if quarantined:
            await self._save()

    async def _migrate(self) -> dict[str, dict[str, Any]]:
        """Bring over acclimation runs saved by v0.3.0."""
        old = await Store(self._hass, STORAGE_VERSION, f"{DOMAIN}.acclimation").async_load() or {}
        programs = {
            entry_id: {
                "target_manual": run["target_manual"],
                "target_schedule": run["target_schedule"],
                "acclimation": {k: run[k] for k in ("start_pct", "days", "started")},
                "moon": None,
            }
            for entry_id, run in old.get("runs", {}).items()
        }
        for raw in programs.values():
            _carry_over(raw)
        return programs

    async def _save(self) -> None:
        async with self._save_lock:
            await self._store.async_save({
                "programs": {k: p.to_dict() for k, p in self.programs.items()},
                "prefs": deepcopy(self.prefs),
                "invalid_programs": deepcopy(self.invalid_programs),
            })

    def get(self, entry_id: str) -> Program | None:
        return self.programs.get(entry_id)

    def invalid(self, entry_id: str) -> str | None:
        """Why a stored program was set aside, if it was."""
        bad = self.invalid_programs.get(entry_id)
        return bad["error"] if bad else None

    async def async_set(self, entry_id: str, program: Program) -> None:
        self.programs[entry_id] = program
        self.invalid_programs.pop(entry_id, None)  # superseded by the new program
        await self._save()

    async def async_clear(self, entry_id: str) -> None:
        if self.programs.pop(entry_id, None) is not None:
            await self._save()

    def pref(self, entry_id: str, key: str, default: Any = None) -> Any:
        return self.prefs.get(entry_id, {}).get(key, default)

    async def async_set_pref(self, entry_id: str, key: str, value: Any) -> None:
        self.prefs.setdefault(entry_id, {})[key] = value
        await self._save()
