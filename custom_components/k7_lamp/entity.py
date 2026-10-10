"""Base entity for the K7 lamp."""
from __future__ import annotations

import re

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import K7Coordinator


def model_from_name(name: str) -> str:
    """'K7_Pro12345' -> 'K7 Pro' (the lamp reports a model prefix plus a serial)."""
    prefix = re.match(r"[A-Za-z0-9]+_[A-Za-z]+", name or "")
    return prefix.group(0).replace("_", " ") if prefix else "K7"


class K7Entity(CoordinatorEntity[K7Coordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: K7Coordinator, key: str) -> None:
        super().__init__(coordinator)
        uid = coordinator.config_entry.unique_id
        self._attr_unique_id = f"{uid}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, uid)},
            name=coordinator.config_entry.title,
            manufacturer="Noo-Psyche",
            model=model_from_name(coordinator.data.name if coordinator.data else ""),
            serial_number=coordinator.data.name if coordinator.data else None,
            configuration_url=None,
        )
