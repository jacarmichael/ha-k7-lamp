"""Named schedules saved in HA (.storage/k7_lamp.profiles)."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION
from .protocol import Row, sort_rows, validate_schedule

ORIGINAL = "Original (from lamp)"


class ProfileStore:
    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.profiles"
        )
        self.profiles: dict[str, dict[str, Any]] = {}
        self.originals: dict[str, dict[str, Any]] = {}
        self._save_lock = asyncio.Lock()

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.profiles = data.get("profiles", {})
        self.originals = data.get("originals", {})

    async def _save(self) -> None:
        async with self._save_lock:
            await self._store.async_save(deepcopy({"profiles": self.profiles, "originals": self.originals}))

    async def async_capture_original(
        self, entry_id: str, title: str, manual: list[int], schedule: list[Row],
    ) -> None:
        """Keep an immutable backup per lamp, separately from shared user profiles."""
        if entry_id in self.originals:
            return
        validate_schedule(manual, schedule)
        self.originals[entry_id] = {"name": f"Original ({title})", "manual": list(manual), "schedule": sort_rows(schedule)}
        await self._save()

    async def async_save(self, name: str, manual: list[int], schedule: list[Row]) -> None:
        name = name.strip()
        if not name:
            raise ValueError("profile name is empty")
        validate_schedule(manual, schedule)
        self.profiles[name] = {"manual": list(manual), "schedule": sort_rows(schedule)}
        await self._save()

    async def async_delete(self, name: str) -> None:
        if self.profiles.pop(name, None) is not None:
            await self._save()
