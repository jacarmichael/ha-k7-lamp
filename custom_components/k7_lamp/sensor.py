"""Sensors: mode, estimated output and manual level per channel."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .coordinator import K7ConfigEntry, K7Coordinator
from .entity import K7Entity
from .protocol import CHANNELS


def _slug(channel: str) -> str:
    return channel.lower().replace(" ", "_")


def _now_minute() -> int:
    now = dt_util.now()
    return now.hour * 60 + now.minute


async def async_setup_entry(
    hass: HomeAssistant,
    entry: K7ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        K7ModeSensor(coordinator), K7AcclimationSensor(coordinator), K7MoonSensor(coordinator)
    ]
    for idx, channel in enumerate(CHANNELS):
        entities.append(K7OutputSensor(coordinator, idx, channel))
        entities.append(K7ManualLevelSensor(coordinator, idx, channel))
    async_add_entities(entities)


class K7ModeSensor(K7Entity, SensorEntity):
    """Auto/manual, with the full 24-slot schedule as an attribute."""

    _attr_name = "Mode"
    _attr_icon = "mdi:calendar-clock"
    _attr_device_class = "enum"
    _attr_options = ["auto", "manual"]

    def __init__(self, coordinator: K7Coordinator) -> None:
        super().__init__(coordinator, "mode")

    @property
    def native_value(self) -> str:
        return "auto" if self.coordinator.data.auto else "manual"

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        return {
            "schedule": [
                {"time": f"{m // 60:02d}:{m % 60:02d}", **dict(zip(CHANNELS, lv))}
                for m, lv in data.schedule
            ],
            "manual_levels": dict(zip(CHANNELS, data.manual)),
        }


class K7OutputSensor(K7Entity, SensorEntity):
    """Estimated live output: manual level, or schedule interpolated at HA local time."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:led-strip-variant"

    def __init__(self, coordinator: K7Coordinator, idx: int, channel: str) -> None:
        super().__init__(coordinator, f"output_{_slug(channel)}")
        self._idx = idx
        self._attr_name = f"{channel} output"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # The estimate moves along the schedule every minute; the lamp is polled far less often.
        self.async_on_remove(async_track_time_change(self.hass, self._async_minute, second=0))

    @callback
    def _async_minute(self, _now) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return self.coordinator.data.output_levels(_now_minute())[self._idx]

    @property
    def extra_state_attributes(self) -> dict:
        return {"source": "schedule" if self.coordinator.data.auto else "manual"}


class K7ManualLevelSensor(K7Entity, SensorEntity):
    """The stored manual level (used when the lamp is in manual mode)."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:tune-vertical"

    def __init__(self, coordinator: K7Coordinator, idx: int, channel: str) -> None:
        super().__init__(coordinator, f"manual_{_slug(channel)}")
        self._idx = idx
        self._attr_name = f"{channel} manual level"

    @property
    def native_value(self) -> int:
        return self.coordinator.data.manual[self._idx]


class K7AcclimationSensor(K7Entity, SensorEntity):
    """Last acclimation intensity confirmed on the lamp (100% without a program)."""

    _attr_name = "Acclimation intensity"
    _attr_icon = "mdi:sun-clock-outline"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: K7Coordinator) -> None:
        super().__init__(coordinator, "acclimation")

    @property
    def native_value(self) -> int | None:
        prog = self.coordinator.program()
        if prog is None:
            return 100
        # A failed stop/target update must not claim a percentage never confirmed.
        return prog.applied.percent if prog.applied else None

    @property
    def extra_state_attributes(self) -> dict:
        info = self.coordinator.acclimation_info()
        if not info:
            return {"active": False}
        return {
            "active": True,
            "day": info["day"],
            "days": info["days"],
            "start_percent": info["start_percent"],
            "started": info["started"],
            "ends": info["ends"],
            "planned_percent": info["percent"],
            "applied_at": info["applied_at"],
        }


class K7MoonSensor(K7Entity, SensorEntity):
    """Moonlight level the lamp should be adding right now (0 when off or the moon is down)."""

    _attr_name = "Moonlight"
    _attr_icon = "mdi:moon-waning-crescent"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: K7Coordinator) -> None:
        super().__init__(coordinator, "moonlight")

    @property
    def native_value(self) -> int:
        return self.coordinator.moon_info()["level_now"]

    @property
    def extra_state_attributes(self) -> dict:
        info = self.coordinator.moon_info()
        cfg = info["config"]
        return {
            "enabled": cfg["enabled"],
            "illumination": info["illumination"],
            "phase": info["phase_name"],
            "next_moonrise": info["next_rise"],
            "next_moonset": info["next_set"],
            "max_level": cfg["max_level"],
            "follow_moon": cfg.get("follow_moon", True),
        }
