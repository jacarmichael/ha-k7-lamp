"""Sliders for the manual level of each channel (moving one puts the lamp in manual)."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import K7ConfigEntry, K7Coordinator
from .entity import K7Entity
from .protocol import CHANNELS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: K7ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        K7ManualLevelNumber(coordinator, idx, channel) for idx, channel in enumerate(CHANNELS)
    )


class K7ManualLevelNumber(K7Entity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:tune-vertical"

    def __init__(self, coordinator: K7Coordinator, idx: int, channel: str) -> None:
        super().__init__(coordinator, f"manual_level_{channel.lower().replace(' ', '_')}")
        self._idx = idx
        self._attr_name = f"{channel} manual level"

    @property
    def native_value(self) -> int:
        return self.coordinator.data.manual[self._idx]

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_manual_level(self._idx, int(value))
