"""WebSocket API used by the schedule editor card."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .profiles import ProfileStore
from .protocol import CHANNELS, parse_qr_payload, state_rows, to_qr_payload

LEVELS = vol.All([vol.All(vol.Coerce(int), vol.Range(0, 100))], vol.Length(6, 6))
ROWS = vol.All([vol.All([vol.Coerce(int)], vol.Length(8, 8))], vol.Length(24, 24))


def _profiles(hass: HomeAssistant) -> ProfileStore:
    return hass.data[DOMAIN]["profiles"]


def _program_info(coordinator) -> dict[str, Any]:
    if coordinator.data is None:
        return {}
    manual, schedule = coordinator.target()
    effective = coordinator.effective()
    period = coordinator.photoperiod()
    return {
        "program": coordinator.program() is not None,
        "target": {"manual": manual, "schedule": schedule},
        "effective": {"manual": effective[0], "schedule": effective[1]} if effective else None,
        "moon": coordinator.moon_info(),
        "day_start": coordinator.day_start_pref(),
        "photoperiod": list(period) if period else None,
        "rebuild_time": "%02d:%02d" % coordinator.rebuild_time(),
    }


def _lamps(hass: HomeAssistant) -> list[dict[str, Any]]:
    lamps = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator = entry.runtime_data
        state = coordinator.data
        lamps.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "available": coordinator.last_update_success,
                "auto": state.auto if state else None,
                "manual": state.manual if state else None,
                "schedule": state_rows(state) if state else None,
                "acclimation": coordinator.acclimation_info(),
                **_program_info(coordinator),
            }
        )
    return lamps


@callback
def async_register_websocket_commands(hass: HomeAssistant) -> None:
    for handler in (ws_get, ws_save_profile, ws_delete_profile, ws_apply,
                    ws_sync_clock, ws_parse_qr, ws_to_qr,
                    ws_start_acclimation, ws_stop_acclimation,
                    ws_set_moonlight, ws_set_day_start, ws_set_mode, ws_set_manual):
        websocket_api.async_register_command(hass, handler)


@websocket_api.websocket_command({vol.Required("type"): "k7_lamp/get"})
@callback
def ws_get(hass, connection, msg):
    connection.send_result(
        msg["id"],
        {"channels": CHANNELS, "lamps": _lamps(hass), "profiles": _profiles(hass).profiles},
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "k7_lamp/save_profile",
        vol.Required("name"): str,
        vol.Required("manual"): LEVELS,
        vol.Required("schedule"): ROWS,
    }
)
@websocket_api.async_response
async def ws_save_profile(hass, connection, msg):
    try:
        await _profiles(hass).async_save(msg["name"], msg["manual"], msg["schedule"])
    except ValueError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], {"profiles": _profiles(hass).profiles})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "k7_lamp/delete_profile", vol.Required("name"): str}
)
@websocket_api.async_response
async def ws_delete_profile(hass, connection, msg):
    await _profiles(hass).async_delete(msg["name"])
    connection.send_result(msg["id"], {"profiles": _profiles(hass).profiles})


def _coordinator(hass, connection, msg):
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if entry.entry_id == msg["entry_id"]:
            return entry.runtime_data
    connection.send_error(msg["id"], "not_found", "Lamp not loaded")
    return None


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "k7_lamp/apply",
        vol.Required("entry_id"): str,
        vol.Required("manual"): LEVELS,
        vol.Required("schedule"): ROWS,
    }
)
@websocket_api.async_response
async def ws_apply(hass, connection, msg):
    if (coordinator := _coordinator(hass, connection, msg)) is None:
        return
    try:
        written = await coordinator.async_set_target(msg["manual"], msg["schedule"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "lamp_error", str(err))
        return
    connection.send_result(msg["id"], {"written": written, "lamps": _lamps(hass)})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "k7_lamp/sync_clock", vol.Required("entry_id"): str}
)
@websocket_api.async_response
async def ws_sync_clock(hass, connection, msg):
    if (coordinator := _coordinator(hass, connection, msg)) is None:
        return
    try:
        await coordinator.async_sync_clock()
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "lamp_error", str(err))
        return
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command(
    {vol.Required("type"): "k7_lamp/parse_qr", vol.Required("payload"): str}
)
@callback
def ws_parse_qr(hass, connection, msg):
    try:
        manual, schedule = parse_qr_payload(msg["payload"])
    except ValueError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], {"manual": manual, "schedule": schedule})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "k7_lamp/to_qr",
        vol.Required("manual"): LEVELS,
        vol.Required("schedule"): ROWS,
    }
)
@callback
def ws_to_qr(hass, connection, msg):
    try:
        payload = to_qr_payload(msg["manual"], msg["schedule"])
    except ValueError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], {"payload": payload})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "k7_lamp/start_acclimation",
        vol.Required("entry_id"): str,
        vol.Required("manual"): LEVELS,
        vol.Required("schedule"): ROWS,
        vol.Required("start_percent"): vol.All(vol.Coerce(int), vol.Range(1, 99)),
        vol.Required("days"): vol.All(vol.Coerce(int), vol.Range(1, 120)),
    }
)
@websocket_api.async_response
async def ws_start_acclimation(hass, connection, msg):
    if (coordinator := _coordinator(hass, connection, msg)) is None:
        return
    try:
        await coordinator.async_start_acclimation(
            msg["manual"], msg["schedule"], msg["start_percent"], msg["days"]
        )
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "lamp_error", str(err))
        return
    connection.send_result(msg["id"], {"lamps": _lamps(hass)})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "k7_lamp/stop_acclimation",
        vol.Required("entry_id"): str,
        vol.Required("keep_current"): bool,
    }
)
@websocket_api.async_response
async def ws_stop_acclimation(hass, connection, msg):
    if (coordinator := _coordinator(hass, connection, msg)) is None:
        return
    try:
        await coordinator.async_stop_acclimation(msg["keep_current"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "lamp_error", str(err))
        return
    connection.send_result(msg["id"], {"lamps": _lamps(hass)})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "k7_lamp/set_moonlight",
        vol.Required("entry_id"): str,
        vol.Required("enabled"): bool,
        vol.Optional("max_level"): vol.All(vol.Coerce(int), vol.Range(1, 50)),
        vol.Optional("follow_moon"): bool,
        vol.Optional("channels"): {vol.Coerce(str): vol.All(vol.Coerce(int), vol.Range(0, 100))},
    }
)
@websocket_api.async_response
async def ws_set_moonlight(hass, connection, msg):
    if (coordinator := _coordinator(hass, connection, msg)) is None:
        return
    cfg = {k: msg[k] for k in ("enabled", "max_level", "follow_moon", "channels") if k in msg}
    try:
        await coordinator.async_set_moon(cfg)
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "lamp_error", str(err))
        return
    connection.send_result(msg["id"], {"lamps": _lamps(hass)})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "k7_lamp/set_day_start",
        vol.Required("entry_id"): str,
        vol.Required("hour"): vol.Any(None, vol.All(vol.Coerce(int), vol.Range(0, 23))),
    }
)
@websocket_api.async_response
async def ws_set_day_start(hass, connection, msg):
    if (coordinator := _coordinator(hass, connection, msg)) is None:
        return
    await coordinator.async_set_day_start_pref(msg["hour"])
    connection.send_result(msg["id"], {"lamps": _lamps(hass)})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "k7_lamp/set_mode",
        vol.Required("entry_id"): str,
        vol.Required("auto"): bool,
    }
)
@websocket_api.async_response
async def ws_set_mode(hass, connection, msg):
    if (coordinator := _coordinator(hass, connection, msg)) is None:
        return
    try:
        await coordinator.async_set_mode(msg["auto"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "lamp_error", str(err))
        return
    connection.send_result(msg["id"], {"lamps": _lamps(hass)})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "k7_lamp/set_manual",
        vol.Required("entry_id"): str,
        vol.Required("levels"): LEVELS,
    }
)
@websocket_api.async_response
async def ws_set_manual(hass, connection, msg):
    if (coordinator := _coordinator(hass, connection, msg)) is None:
        return
    try:
        await coordinator.async_set_manual_levels(msg["levels"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "lamp_error", str(err))
        return
    connection.send_result(msg["id"], {"lamps": _lamps(hass)})
