"""Noo-Psyche K7 reef lamp (LAN mode): sensors, schedule editor card and QR import."""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import voluptuous as vol

from homeassistant.components.http import StaticPathConfig
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType

from .const import CARD_FILE, CLOCK_SYNC_DELAY, DOMAIN, FRONTEND_VERSION, URL_BASE
from .coordinator import K7ConfigEntry, K7Coordinator
from .profiles import ORIGINAL, ProfileStore
from .program import ProgramStore
from .protocol import align_day_start, parse_qr_payload, state_rows
from .websocket import async_register_websocket_commands

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SELECT, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
CARD_URL = f"{URL_BASE}/{CARD_FILE}?v={FRONTEND_VERSION}"

ROW = vol.All([vol.All(vol.Coerce(int), vol.Range(0, 100))], vol.Length(8, 8))
SET_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): cv.string,
        vol.Exclusive("qr_payload", "source"): cv.string,
        vol.Exclusive("schedule", "source"): vol.All([ROW], vol.Length(24, 24)),
        vol.Optional("manual"): vol.All(
            [vol.All(vol.Coerce(int), vol.Range(0, 100))], vol.Length(6, 6)
        ),
        vol.Optional("align_day_start", default=False): cv.boolean,
    }
)
SYNC_CLOCK_SCHEMA = vol.Schema({vol.Optional("config_entry_id"): cv.string})
START_ACCLIMATION_SCHEMA = SET_SCHEDULE_SCHEMA.extend(
    {
        vol.Required("start_percent"): vol.All(vol.Coerce(int), vol.Range(1, 99)),
        vol.Optional("days", default=30): vol.All(vol.Coerce(int), vol.Range(1, 120)),
        vol.Exclusive("profile", "source"): cv.string,
        vol.Optional("started"): cv.date,  # backdating, for testing the ramp
    }
)
STOP_ACCLIMATION_SCHEMA = SYNC_CLOCK_SCHEMA.extend(
    {vol.Optional("keep_current", default=False): cv.boolean}
)
CHANNEL_WEIGHTS = vol.Schema({vol.Coerce(int): vol.All(vol.Coerce(int), vol.Range(0, 100))})
SET_MOONLIGHT_SCHEMA = SYNC_CLOCK_SCHEMA.extend(
    {
        vol.Required("enabled"): cv.boolean,
        vol.Optional("max_level"): vol.All(vol.Coerce(int), vol.Range(1, 50)),
        vol.Optional("follow_moon"): cv.boolean,
        vol.Optional("channels"): CHANNEL_WEIGHTS,
    }
)


def get_coordinator(hass: HomeAssistant, entry_id: str | None) -> K7Coordinator:
    entries = [
        e for e in hass.config_entries.async_loaded_entries(DOMAIN)
        if entry_id in (None, e.entry_id)
    ]
    if not entries:
        raise ServiceValidationError("No loaded K7 lamp matches")
    if len(entries) > 1:
        raise ServiceValidationError("Several K7 lamps are set up; pass config_entry_id")
    return entries[0].runtime_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Domain-wide pieces: profile store, card files, websocket API, actions."""
    profiles = ProfileStore(hass)
    await profiles.async_load()
    program = ProgramStore(hass)
    await program.async_load()
    hass.data[DOMAIN] = {"profiles": profiles, "program": program}

    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_BASE, str(Path(__file__).parent / "frontend"), False)]
    )
    _register_card_resource(hass)
    async_register_websocket_commands(hass)

    def schedule_from_call(call: ServiceCall, coordinator: K7Coordinator):
        if "qr_payload" in call.data:
            try:
                manual, schedule = parse_qr_payload(call.data["qr_payload"])
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err
        elif "schedule" in call.data:
            manual, schedule = coordinator.data.manual, call.data["schedule"]
        elif "profile" in call.data:
            if (prof := profiles.profiles.get(call.data["profile"])) is None:
                raise ServiceValidationError(f"No profile named {call.data['profile']!r}")
            manual, schedule = prof["manual"], prof["schedule"]
        else:
            return None
        if call.data.get("align_day_start") and (start := coordinator.day_start_pref()) is not None:
            schedule = align_day_start(schedule, start)
        return call.data.get("manual", manual), schedule

    async def set_schedule(call: ServiceCall) -> None:
        coordinator = get_coordinator(hass, call.data.get("config_entry_id"))
        if (src := schedule_from_call(call, coordinator)) is None:
            raise ServiceValidationError("Give either qr_payload or schedule")
        await coordinator.async_set_target(*src)

    async def start_acclimation(call: ServiceCall) -> None:
        coordinator = get_coordinator(hass, call.data.get("config_entry_id"))
        src = schedule_from_call(call, coordinator)
        if src is None:  # default: whatever is on the lamp now
            src = coordinator.data.manual, state_rows(coordinator.data)
        started: date | None = call.data.get("started")
        await coordinator.async_start_acclimation(
            *src, call.data["start_percent"], call.data["days"], started
        )

    async def stop_acclimation(call: ServiceCall) -> None:
        coordinator = get_coordinator(hass, call.data.get("config_entry_id"))
        await coordinator.async_stop_acclimation(call.data["keep_current"])

    async def set_moonlight(call: ServiceCall) -> None:
        coordinator = get_coordinator(hass, call.data.get("config_entry_id"))
        cfg = {k: call.data[k] for k in ("enabled", "max_level", "follow_moon", "channels") if k in call.data}
        await coordinator.async_set_moon(cfg)

    async def sync_clock(call: ServiceCall) -> None:
        await get_coordinator(hass, call.data.get("config_entry_id")).async_sync_clock()

    hass.services.async_register(DOMAIN, "set_schedule", set_schedule, SET_SCHEDULE_SCHEMA)
    hass.services.async_register(DOMAIN, "sync_clock", sync_clock, SYNC_CLOCK_SCHEMA)
    hass.services.async_register(
        DOMAIN, "start_acclimation", start_acclimation, START_ACCLIMATION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "stop_acclimation", stop_acclimation, STOP_ACCLIMATION_SCHEMA
    )
    hass.services.async_register(DOMAIN, "set_moonlight", set_moonlight, SET_MOONLIGHT_SCHEMA)
    return True


def _register_card_resource(hass: HomeAssistant) -> None:
    """Add/update the card as a Lovelace resource (same approach as wine_cellar)."""

    async def _add(*_args) -> None:
        lovelace = hass.data.get("lovelace")
        resources = getattr(lovelace, "resources", None) or hass.data.get("lovelace_resources")
        if resources is None:
            _LOGGER.info("Add the card manually as a dashboard resource: %s", CARD_URL)
            return
        try:
            if not getattr(resources, "loaded", True):
                await resources.async_load()
            existing = next(
                (r for r in resources.async_items() if r.get("url", "").startswith(f"{URL_BASE}/")),
                None,
            )
            if existing is None:
                await resources.async_create_item({"res_type": "module", "url": CARD_URL})
            elif existing["url"] != CARD_URL:
                await resources.async_update_item(existing["id"], {"url": CARD_URL})
        except Exception as err:  # noqa: BLE001 - never block setup on the card
            _LOGGER.warning("Could not register card resource %s: %s", CARD_URL, err)

    if hass.is_running:
        hass.async_create_task(_add())
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _add)


async def async_setup_entry(hass: HomeAssistant, entry: K7ConfigEntry) -> bool:
    coordinator = K7Coordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    profiles: ProfileStore = hass.data[DOMAIN]["profiles"]
    if ORIGINAL not in profiles.profiles:
        state = coordinator.data
        await profiles.async_save(ORIGINAL, state.manual, state_rows(state))
        _LOGGER.info("Saved the lamp's current schedule as profile %r", ORIGINAL)

    async def _daily_sync(_now) -> None:
        try:
            await coordinator.async_sync_clock()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Daily clock sync failed: %s", err)

    async def _acclimation_step(_now=None) -> None:
        if coordinator.data is not None and not coordinator.data.auto:
            _LOGGER.debug("%s is in manual mode; skipping the daily plan", entry.title)
            return
        try:
            await coordinator.async_rebuild()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Daily schedule rebuild failed (will retry next run): %s", err)

    async def _tick(now: datetime) -> None:
        """Every 5 min: rebuild at the lamp's rebuild time, sync its clock a little later."""
        h, m = coordinator.rebuild_time()
        rebuild = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if (now.hour, now.minute) == (h, m):
            await _acclimation_step()
        elif now.replace(second=0, microsecond=0) == rebuild + CLOCK_SYNC_DELAY:
            await _daily_sync(now)

    entry.async_on_unload(
        async_track_time_change(hass, _tick, minute=list(range(0, 60, 5)), second=0)
    )
    _LOGGER.info("%s: daily plan at %02d:%02d", entry.title, *coordinator.rebuild_time())

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Catch up if HA was down when a daily rebuild was due.
    entry.async_create_background_task(hass, _acclimation_step(), f"{DOMAIN} rebuild catch-up")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: K7ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
