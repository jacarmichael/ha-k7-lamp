# CLAUDE.md

Home Assistant custom integration (HACS) for the **Noo-Psyche K7 Pro** reef light, which it controls locally over TCP port 8266. Domain: `k7_lamp`. There are no runtime requirements beyond Home Assistant itself (2025.2+). The protocol, QR format, presets and editor design come from [bitbarista/k7-led-controller](https://github.com/bitbarista/k7-led-controller).

## Commands

```sh
pytest -q                                     # unit tests (only needs pytest, not Home Assistant)
python3 custom_components/k7_lamp/tools/k7_test.py --host <ip> status   # read a real lamp (safe)
```

`k7_test.py` subcommands other than `status` **write to real hardware**. Never run them unless the user is watching the tank. The script lives inside the integration so HACS installs it (HACS replaces the whole `k7_lamp` folder on update, so anything copied in by hand is lost); it imports `protocol.py` from the folder above it.

CI (`.github/workflows/`) runs `pytest` on Python 3.13, plus **hassfest** and the **HACS action**. There is no lint step.

## Architecture

The lamp runs its own 24-point schedule in **auto** mode. HA polls it every 30 s with `ALL_READ`. HA writes to the lamp rarely, because a full write (`ALL_SET`, `10 07`) pauses the lamp for about 4 s and resets its output. Writes happen once a day, in the dark, shortly before lights-on, or when the user explicitly presses Apply. Don't add code paths that write more often.

### Pure modules (no Home Assistant imports, so they're unit-testable)
- `protocol.py`: the TCP client and frame encode/decode (`AA A5 <cmd> … BB` out, `AB AA …` in), `ALL_SET` payload building (203 bytes), `acked()`, the QR share format (`12 hex # 384 hex`), and the photoperiod helpers (`day_start`, `photoperiod`, `shift_rows`, `align_day_start`).
- `acclimation.py`: the linear ramp `percent_for()` and `scale()`. They match the reference firmware's rounding (round half up, and lit channels stay ≥1%).
- `moon.py`: a SunCalc port (illumination, altitude, rise/set by scanning) and `moon_level()`.
- `effective.py`: `build_effective()`, which computes target → acclimation scaling → moon overlay (max per channel) for the next 24 h, and `rebuild_time()`, the daily write time.

Keep these modules free of `homeassistant` imports. `tests/conftest.py` builds a fake `k7_lamp` package so tests can `from k7_lamp.protocol import …` without HA installed. Tests only cover these modules; there is no HA test harness.

### HA modules
- `__init__.py`: `async_setup` creates the domain-wide stores and registers the static path `/k7_lamp` → `frontend/`, the Lovelace resource, the websocket commands and the 5 actions (voluptuous schemas live here). `async_setup_entry` creates the coordinator, saves the lamp's schedule as profile `"Original (from lamp)"` on first setup, and registers a 5-minute `_tick` that runs `async_rebuild()` at `rebuild_time()` and a clock sync `CLOCK_SYNC_DELAY` (5 min) later. It also runs a catch-up rebuild on startup.
- `coordinator.py` (`K7Coordinator`): one per lamp. An `asyncio.Lock` serialises all lamp I/O, because the lamp copes badly with concurrent clients, and `_read()` retries once. `async_apply_schedule` re-reads first and **skips the write if the lamp already has that schedule**. The coordinator also holds the program logic (`async_set_target`, acclimation, moon, day-start pref).
- `program.py` / `profiles.py`: `Store` wrappers for `.storage/k7_lamp.program` (`{"programs": {entry_id: …}, "prefs": {entry_id: {"day_start": h}}}`) and `.storage/k7_lamp.profiles`. `ProgramStore._migrate` imports the old v0.3.0 `k7_lamp.acclimation` store.
- `websocket.py`: the `k7_lamp/*` commands used by the card. Every mutating command uses `@require_admin`. `_lamps()` builds the payload the card renders from.
- `sensor.py`, `binary_sensor.py`, `button.py`, `select.py`, `number.py`, `entity.py`, `config_flow.py`: entities and the config flow. The Mode select calls `async_set_mode()` (`CHANGE_MODE`, `10 04`). The per-channel manual level numbers call `async_set_manual_level()` (`HAND_LUMINANCE`, `10 05`), which switches to manual first and also updates the program's `target_manual` so returning to auto doesn't trigger an `ALL_SET` just for the manual levels. The config flow only reads from the lamp, uses the lamp's reported name (e.g. `K7_Pro12345`) as its unique id, and aborts for the Mini.

### The "program" model
- **No program** (the default): Apply writes the schedule straight to the lamp.
- **Program exists** (acclimation running or moonlight enabled): HA owns the *target* schedule, and the lamp receives the daily-built *effective* schedule. Apply becomes "Update target".
- While the lamp is in **manual** mode, the background writes (daily tick and startup catch-up in `_acclimation_step`) are skipped, so they don't flip it back to auto. User-initiated writes (Apply, actions) still write in auto mode. Switching the select to auto runs `async_rebuild()` if a program exists.
- When acclimation finishes or is stopped and moonlight is off, `async_rebuild` writes the plain target and **clears the program**, which returns the lamp to direct writes.

### Frontend (`frontend/k7-schedule-card.js`)
The card is a vanilla custom element (`custom:k7-schedule-card`) with no build step. It talks to HA only through `hass.callWS` with the `k7_lamp/*` types. It lazy-loads the vendored Chart.js, dragdata, jsQR and QRCode.js from `/k7_lamp/vendor/`, and fetches `presets.json` (from bitbarista's community presets). It contains JS copies of `day_start` / `photoperiod` / `shift_rows` / `align_day_start`; **keep them in sync with `protocol.py`**. Don't edit the files in `frontend/vendor/`.

## Data conventions
- Channel order (index 0–5): White, Royal Blue, Green, UV, Cyan, Red (`protocol.CHANNELS`). The card's `COLORS` follows the same order.
- A schedule is always **24 rows of `[hour, minute, c0..c5]`**, with levels as integers 0–100. `manual` is 6 levels. Use `validate_schedule()`.
- Moon `channels` are keyed by **string** channel index, e.g. `{"1": 100, "4": 70}`, because they are stored as JSON.
- Times sent to the lamp are HA local time (`dt_util.now()`).
- `rebuild_time()` always returns `hh:00` or `hh:30`, so the 5-minute `_tick` lands on it exactly.

## Gotchas when changing things
- **Versions:** `manifest.json` `version` is the release version, and each release gets a `CHANGELOG.md` entry. The card's cache-buster is separate. When the card or vendor files change, bump `FRONTEND_VERSION` in `const.py`, `VERSION` in `k7-schedule-card.js`, and the `?v=` in the README's YAML-mode resource URL, all together. The card resource is re-registered automatically when the URL changes.
- **Translations:** `strings.json` and `translations/en.json` are identical, so edit both.
- **hassfest:** `manifest.json` keys must be ordered `domain`, `name`, then alphabetical. Don't put `{}` braces in action/field descriptions, because HA treats them as placeholders.
- New actions need a schema in `__init__.py`, an entry in `services.yaml`, and strings in both translation files.
- `NOTICE.md` exists both at the repo root and in `custom_components/k7_lamp/`, which ships with HACS installs. Keep both copies identical.
- Some comments in `protocol.py` are stale: the module docstring says "Read-only", and the section comments say write commands are unused and `ALL_SET` isn't implemented. In fact the integration uses `sync_time` and `write_all_set`.
