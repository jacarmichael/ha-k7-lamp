"""Per-lamp program: the target schedule plus acclimation and moonlight settings.

When a lamp has a program, its target schedule lives in HA and the lamp gets a
daily-built "effective" schedule (target -> acclimation scaling -> moon overlay).
"""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION
from .effective import DEFAULT_MOON, build_effective  # noqa: F401 - re-exported


class ProgramStore:
    """.storage/k7_lamp.program: {"programs": {entry_id: {...}}, "prefs": {entry_id: {...}}}"""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.program")
        self.programs: dict[str, dict[str, Any]] = {}
        self.prefs: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data is None:
            data = {"programs": await self._migrate(), "prefs": {}}
            await self._store.async_save(data)
        self.programs = data.get("programs", {})
        self.prefs = data.get("prefs", {})

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
        await self._store.async_save({"programs": self.programs, "prefs": self.prefs})

    def get(self, entry_id: str) -> dict[str, Any] | None:
        return self.programs.get(entry_id)

    async def async_set(self, entry_id: str, program: dict[str, Any]) -> None:
        self.programs[entry_id] = program
        await self._save()

    async def async_clear(self, entry_id: str) -> None:
        if self.programs.pop(entry_id, None) is not None:
            await self._save()

    def pref(self, entry_id: str, key: str, default: Any = None) -> Any:
        return self.prefs.get(entry_id, {}).get(key, default)

    async def async_set_pref(self, entry_id: str, key: str, value: Any) -> None:
        self.prefs.setdefault(entry_id, {})[key] = value
        await self._save()
