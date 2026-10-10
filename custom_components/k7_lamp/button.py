"""Button to send HA's local time to the lamp."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import K7ConfigEntry, K7Coordinator
from .entity import K7Entity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: K7ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([K7SyncClockButton(entry.runtime_data)])


class K7SyncClockButton(K7Entity, ButtonEntity):
    _attr_name = "Sync clock"
    _attr_icon = "mdi:clock-check-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: K7Coordinator) -> None:
        super().__init__(coordinator, "sync_clock")

    async def async_press(self) -> None:
        await self.coordinator.async_sync_clock()
