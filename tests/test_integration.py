"""Real HA classes/storage/services with a fake lamp; no hardware or network."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("homeassistant")
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from helpers import hourly, sample_schedule
from custom_components import k7_lamp as integration
from custom_components.k7_lamp import binary_sensor, button, config_flow, coordinator, sensor, websocket
from custom_components.k7_lamp.effective import DEFAULT_MOON
from custom_components.k7_lamp.profiles import ProfileStore
from custom_components.k7_lamp.program import ProgramStore
from custom_components.k7_lamp.protocol import K7Error, K7State, state_rows


def entry(name="one"):
    return ConfigEntry(domain="k7_lamp", data={"host": name, "port": 8266},
                       title=name, entry_id=name, unique_id=name, source="user",
                       version=1, minor_version=1, options={}, discovery_keys={},
                       state=ConfigEntryState.SETUP_IN_PROGRESS)


def program(**changes):
    return {"target_manual": [50] * 6, "target_schedule": sample_schedule(),
            "acclimation": {"start_pct": 50, "days": 30, "started": "2026-10-26"},
            "moon": None, **changes}


def state(rows=None, *, auto=True, manual=None):
    rows = rows if rows is not None else sample_schedule()
    return K7State("K7_Pro12345", auto, manual or [50] * 6,
                   [(r[0] * 60 + r[1], list(r[2:])) for r in rows])


@pytest.fixture
def run(tmp_path, monkeypatch):
    def execute(test):
        async def scenario():
            hass = HomeAssistant(str(tmp_path))
            hass.config.latitude, hass.config.longitude = 8.98, -79.52
            clock = SimpleNamespace(now=datetime(2026, 10, 26, 7, 30, tzinfo=ZoneInfo("America/Panama")))
            monkeypatch.setattr(coordinator.dt_util, "now", lambda: clock.now)
            monkeypatch.setattr(coordinator, "ALL_SET_SETTLE", 0)
            store, profiles = ProgramStore(hass), ProfileStore(hass)
            await store.async_load()
            await profiles.async_load()
            hass.data["k7_lamp"] = {"program": store, "profiles": profiles}
            lamp = SimpleNamespace(state=state(), writes=[], read_errors=0, accept=True)

            async def read():
                if lamp.read_errors:
                    lamp.read_errors -= 1
                    raise K7Error("simulated disconnect")
                return deepcopy(lamp.state)

            async def write(host, port, manual, rows, auto, h, m, s):
                lamp.writes.append(deepcopy((manual, rows, auto)))
                if lamp.accept:
                    lamp.state = state(rows, manual=manual, auto=auto)
                return bytes.fromhex("abaaa5a1")

            monkeypatch.setattr(coordinator, "write_all_set", write)
            c = coordinator.K7Coordinator(hass, entry())
            c.data = deepcopy(lamp.state)
            c._read = read
            env = SimpleNamespace(hass=hass, clock=clock, c=c, store=store, profiles=profiles,
                                  lamp=lamp, read=read, path=tmp_path, monkeypatch=monkeypatch)
            try:
                await test(env)
            finally:
                await c.async_shutdown()
                await hass.async_stop(force=True)
        asyncio.run(scenario())
    return execute


@pytest.mark.parametrize("key", ["7", "-1", "x", "1.2"])
def test_bad_moon_request_does_not_persist_or_write(run, key):
    async def check(e):
        with pytest.raises(HomeAssistantError):
            await e.c.async_set_moon({"enabled": True, "channels": {key: 100}})
        assert e.store.get("one") is None
        assert not e.lamp.writes
    run(check)


@pytest.mark.parametrize("key", ["7", "-1", "x", 6, -1])
def test_service_schema_rejects_bad_channel(key):
    with pytest.raises(vol.Invalid):
        integration.SET_MOONLIGHT_SCHEMA({"enabled": True, "channels": {key: 100}})


def test_existing_invalid_program_is_quarantined_and_others_survive(run):
    async def check(e):
        bad = program(moon={**DEFAULT_MOON, "channels": {"7": 100}})
        await e.store._store.async_save({"programs": {"bad": bad, "good": program()}, "prefs": {}})
        loaded = ProgramStore(e.hass)
        await loaded.async_load()
        assert loaded.get("bad") is None
        assert loaded.get("good") is not None
        assert loaded.invalid_programs["bad"] == bad
        assert "bad" in loaded.errors
        again = ProgramStore(e.hass)
        await again.async_load()
        assert again.invalid_programs["bad"] == bad
    run(check)


def test_background_rebuild_respects_manual_and_preserves_manual_levels(run):
    async def check(e):
        await e.store.async_set("one", program())
        e.lamp.state.auto = False
        e.lamp.state.manual = [12] * 6
        await e.c.async_rebuild()
        assert not e.lamp.writes
        assert e.store.get("one").get("applied") is None
        e.lamp.state.auto = True
        e.clock.now += timedelta(minutes=5)
        await e.c.async_rebuild()
        assert len(e.lamp.writes) == 1
        assert e.lamp.writes[0][0] == [12] * 6
    run(check)


def test_explicit_apply_can_switch_manual_lamp_to_auto(run):
    async def check(e):
        e.lamp.state.auto = False
        await e.c.async_set_target([40] * 6, sample_schedule())
        assert e.lamp.state.auto
        assert e.lamp.state.manual == [40] * 6
    run(check)


def test_restart_and_repeated_ticks_do_not_rewrite_confirmed_cycle(run):
    async def check(e):
        await e.store.async_set("one", program(moon=deepcopy(DEFAULT_MOON)))
        await e.c.async_rebuild()
        assert len(e.lamp.writes) == 1
        e.clock.now += timedelta(minutes=5)
        loaded = ProgramStore(e.hass)
        await loaded.async_load()
        e.hass.data["k7_lamp"]["program"] = loaded
        await e.c.async_rebuild()
        e.clock.now = e.clock.now.replace(hour=14)
        await e.c.async_rebuild()
        assert len(e.lamp.writes) == 1
        assert e.c.effective()[1] == state_rows(e.lamp.state)
    run(check)


def test_missed_window_defers_until_next_safe_cycle(run):
    async def check(e):
        await e.store.async_set("one", program())
        e.clock.now = e.clock.now.replace(hour=14)
        await e.c.async_rebuild()
        assert not e.lamp.writes
        e.clock.now = (e.clock.now + timedelta(days=1)).replace(hour=7, minute=30)
        await e.c.async_rebuild()
        assert len(e.lamp.writes) == 1
    run(check)


def test_failed_update_retries_in_window_and_reports_only_applied_percent(run):
    async def check(e):
        await e.store.async_set("one", program())
        e.lamp.read_errors = 1
        with pytest.raises(HomeAssistantError):
            await e.c.async_rebuild()
        assert e.c.plan_pending()
        assert sensor.K7AcclimationSensor(e.c).native_value is None
        e.clock.now += timedelta(minutes=5)
        await e.c.async_rebuild()
        assert len(e.lamp.writes) == 1
        assert sensor.K7AcclimationSensor(e.c).native_value == 50
        assert not e.c.plan_pending()
        e.clock.now += timedelta(days=1)
        # A new planned percentage is not reported as applied until confirmed.
        assert e.c.acclimation_info()["percent"] == 52
        assert e.c.plan_pending()
        assert sensor.K7AcclimationSensor(e.c).native_value == 50
    run(check)


def test_write_does_not_start_after_read_crosses_end_of_window(run):
    async def check(e):
        await e.store.async_set("one", program())
        e.clock.now = e.clock.now.replace(minute=55)
        async def late_read():
            e.clock.now = e.clock.now.replace(hour=8, minute=0)
            return await e.read()
        e.c._read = late_read
        await e.c.async_rebuild()
        assert not e.lamp.writes
        assert e.c.plan_pending()
    run(check)


def test_acknowledged_write_with_lost_readback_is_not_written_twice(run):
    async def check(e):
        await e.store.async_set("one", program())
        calls = 0
        async def read_once_then_disconnect():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise K7Error("read-back lost")
            return await e.read()
        e.c._read = read_once_then_disconnect
        with pytest.raises(HomeAssistantError, match="read-back"):
            await e.c.async_rebuild()
        assert len(e.lamp.writes) == 1
        e.clock.now += timedelta(minutes=5)
        await e.c.async_rebuild()
        assert len(e.lamp.writes) == 1
        assert not e.c.plan_pending()
    run(check)


def test_domain_setup_registers_real_services_and_websocket_commands(run):
    async def check(e):
        e.hass.http = SimpleNamespace(async_register_static_paths=AsyncMock())
        e.monkeypatch.setattr(integration, "_register_card_resource", lambda hass: None)
        assert await integration.async_setup(e.hass, {})
        for name in ("set_schedule", "start_acclimation", "stop_acclimation", "set_moonlight", "sync_clock"):
            assert e.hass.services.has_service("k7_lamp", name)
        e.hass.http.async_register_static_paths.assert_awaited_once()
        with pytest.raises(vol.Invalid):
            await e.hass.services.async_call("k7_lamp", "set_moonlight", {"enabled": True, "channels": {"7": 100}}, blocking=True)
    run(check)


def test_readback_mismatch_does_not_mark_program_applied(run):
    async def check(e):
        await e.store.async_set("one", program())
        e.lamp.accept = False
        with pytest.raises(HomeAssistantError, match="read-back"):
            await e.c.async_rebuild()
        assert e.store.get("one").get("applied") is None
    run(check)


def test_failed_stop_does_not_report_full_intensity(run):
    async def check(e):
        await e.c.async_start_acclimation([50] * 6, sample_schedule(), 50, 30)
        e.lamp.accept = False
        with pytest.raises(HomeAssistantError):
            await e.c.async_stop_acclimation(False)
        assert sensor.K7AcclimationSensor(e.c).native_value == 50
        assert e.c.plan_pending()
    run(check)


def test_no_dark_interval_never_gets_an_automatic_write(run):
    async def check(e):
        await e.store.async_set("one", program(target_schedule=hourly({h: [40] * 6 for h in range(24)})))
        await e.c.async_rebuild()
        assert not e.lamp.writes
    run(check)


def test_new_program_survives_overlapping_stop_and_start(run):
    async def check(e):
        await e.c.async_start_acclimation([50] * 6, sample_schedule(), 50, 30)
        entered, release = asyncio.Event(), asyncio.Event()
        first = True
        async def delayed_read():
            nonlocal first
            if first:
                first = False
                entered.set()
                await release.wait()
            return await e.read()
        e.c._read = delayed_read
        stopping = asyncio.create_task(e.c.async_stop_acclimation(False))
        await entered.wait()
        starting = asyncio.create_task(e.c.async_set_moon({"enabled": True}))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(stopping, starting)
        assert e.store.get("one")["moon"]["enabled"]
        assert e.store.get("one")["acclimation"] is None
        assert e.store.get("one")["applied"]["schedule"] == state_rows(e.lamp.state)
    run(check)


def test_original_backups_are_independent_immutable_and_persisted(run):
    async def check(e):
        for key, level in [("one", 10), ("two", 80), ("one", 99)]:
            await e.profiles.async_capture_original(key, key, [level] * 6, sample_schedule())
        loaded = ProfileStore(e.hass)
        await loaded.async_load()
        assert loaded.originals["one"]["manual"] == [10] * 6
        assert loaded.originals["two"]["manual"] == [80] * 6
    run(check)


@pytest.mark.parametrize("name", ["K7M12345678", "k7m123", "K7 Mini"])
def test_mini_config_flow_aborts(run, name):
    async def check(e):
        e.lamp.state.name = name
        e.monkeypatch.setattr(config_flow, "read_all", AsyncMock(return_value=e.lamp.state))
        flow = config_flow.K7ConfigFlow()
        flow.hass = e.hass
        result = await flow.async_step_user({"host": "test", "port": 8266})
        assert result["type"] == "abort" and result["reason"] == "unsupported_model"
    run(check)


def test_real_setup_platforms_and_retry_tick_on_minimum_ha(run):
    async def check(e):
        entities, callbacks = [], []
        async def forward(ent, platforms):
            for module in (sensor, button, binary_sensor):
                await module.async_setup_entry(e.hass, ent, entities.extend)
        e.hass.config_entries = SimpleNamespace(async_forward_entry_setups=forward)
        e.monkeypatch.setattr(integration, "K7Coordinator", lambda hass, ent: e.c)
        e.monkeypatch.setattr(integration, "async_track_time_change",
                             lambda hass, fn, **kwargs: callbacks.append(fn) or (lambda: None))
        e.clock.now = e.clock.now.replace(hour=14)
        await integration.async_setup_entry(e.hass, e.c.config_entry)
        await e.hass.async_block_till_done()
        assert len(entities) == 17
        assert "one" in e.profiles.originals
        await e.store.async_set("one", program())
        e.clock.now = e.clock.now.replace(hour=7, minute=30)
        e.lamp.read_errors = 1
        await callbacks[0](e.clock.now)
        assert not e.lamp.writes
        e.clock.now += timedelta(minutes=5)
        e.c.async_sync_clock = AsyncMock()
        await callbacks[0](e.clock.now)
        assert len(e.lamp.writes) == 1
    run(check)


def test_websocket_listing_isolates_one_corrupt_program(run):
    async def check(e):
        e.c.config_entry.runtime_data = e.c
        other = coordinator.K7Coordinator(e.hass, entry("two"))
        other.data = state()
        other.config_entry.runtime_data = other
        e.hass.config_entries = SimpleNamespace(async_loaded_entries=lambda domain: [e.c.config_entry, other.config_entry])
        e.store.programs["one"] = {"bad": "data"}
        lamps = websocket._lamps(e.hass)
        assert lamps[0]["program_error"]
        assert lamps[1]["entry_id"] == "two" and "program_error" not in lamps[1]
        await other.async_shutdown()
    run(check)
