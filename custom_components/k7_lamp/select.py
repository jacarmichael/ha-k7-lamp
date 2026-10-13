"""Select to switch the lamp between auto (schedule) and manual (fixed levels)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import K7ConfigEntry, K7Coordinator
from .entity import K7Entity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: K7ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([K7ModeSelect(entry.runtime_data)])


class K7ModeSelect(K7Entity, SelectEntity):
    """Manual runs the lamp's stored manual levels; the daily plan waits until auto."""

    _attr_name = "Mode"
    _attr_icon = "mdi:calendar-clock"
    _attr_options = ["auto", "manual"]

    def __init__(self, coordinator: K7Coordinator) -> None:
        super().__init__(coordinator, "mode_select")

    @property
    def current_option(self) -> str:
        return "auto" if self.coordinator.data.auto else "manual"

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_mode(option == "auto")
