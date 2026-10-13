"""Named schedules saved in HA (.storage/k7_lamp.profiles)."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION
from .protocol import Row, validate_schedule

ORIGINAL = "Original (from lamp)"  # pre-0.6 shared backup; now kept per lamp in `originals`


class ProfileStore:
    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.profiles"
        )
        self.profiles: dict[str, dict[str, Any]] = {}
        # entry_id -> {"name", "manual", "schedule"}: each lamp's schedule at first setup
        self.originals: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.profiles = data.get("profiles", {})
        self.originals = data.get("originals", {})

    async def _save(self) -> None:
        await self._store.async_save({"profiles": self.profiles, "originals": self.originals})

    def by_name(self, name: str) -> dict[str, Any] | None:
        """A saved profile, or a lamp's original backup by its display name."""
        if name in self.profiles:
            return self.profiles[name]
        return next((o for o in self.originals.values() if o["name"] == name), None)

    async def async_capture_original(
        self, entry_id: str, title: str, manual: list[int], schedule: list[Row], only_lamp: bool
    ) -> None:
        """Keep a backup per lamp. A single lamp adopts the pre-0.6 shared backup."""
        if entry_id in self.originals:
            return
        legacy = self.profiles.pop(ORIGINAL, None) if only_lamp else None
        if legacy is not None:
            manual, schedule = legacy["manual"], legacy["schedule"]
        validate_schedule(manual, schedule)
        self.originals[entry_id] = {
            "name": f"Original ({title})",
            "manual": list(manual),
            "schedule": sorted(list(r) for r in schedule),
        }
        await self._save()

    async def async_remove_original(self, entry_id: str) -> None:
        if self.originals.pop(entry_id, None) is not None:
            await self._save()

    async def async_save(self, name: str, manual: list[int], schedule: list[Row]) -> None:
        name = name.strip()
        if not name:
            raise ValueError("profile name is empty")
        validate_schedule(manual, schedule)
        self.profiles[name] = {"manual": list(manual), "schedule": sorted(list(r) for r in schedule)}
        await self._save()

    async def async_delete(self, name: str) -> None:
        if self.profiles.pop(name, None) is not None:
            await self._save()

