# Noo-Psyche K7 Lamp for Home Assistant

Local control of a **Noo-Psyche K7 Pro** reef light from Home Assistant, over your own network. You don't need the cloud, an ESP32 or a Raspberry Pi bridge.

> **Unofficial.** This project is not affiliated with or endorsed by Noo-Psyche. It is built on the protocol work of [bitbarista/k7-led-controller](https://github.com/bitbarista/k7-led-controller).

<!-- Screenshot: add docs/card.png and uncomment
![Schedule editor card](docs/card.png)
-->

## What it does

- **Sensors:**
  - the lamp's mode (auto/manual)
  - estimated output per channel
  - acclimation intensity
  - moonlight level
  - a connectivity check
- **Schedule editor card:**
  - drag the 24-hour curve for each channel, or fine-tune one hour with sliders
  - a colour preview strip shows the blended colour for each hour
  - save profiles, or load built-in and community presets
- **QR codes:**
  - import a schedule shared as a Noo-Psyche app QR code by pasting its text, choosing a screenshot, or scanning it with the camera
  - export your schedule as a QR code the app can read
- **Day starts at:** set the hour your photoperiod should start. Any preset or QR code you load is shifted to match.
- **Acclimation:** ramps the whole schedule linearly from a start % (e.g. 50%) to 100% over a number of days you choose.
- **Moonlight that follows the real moon:**
  - brightness follows the moon's phase
  - optionally, it's only on while the moon is actually above *your* horizon, from moonrise to moonset, worked out from your Home Assistant location
- **Actions** for automations: `set_schedule`, `start_acclimation`, `stop_acclimation`, `set_moonlight`, `sync_clock`.

## How it works

The lamp keeps running its own 24-point schedule in **auto** mode. Home Assistant edits that schedule and keeps the lamp's clock right:

- Once a day, during a dark interval in your target schedule shortly before lights-on, Home Assistant writes the day's plan to the lamp. The plan is your schedule, scaled for acclimation if one is running, plus tonight's moon. Moonlight may still be on during that target dark interval.
- If Home Assistant is down, the lamp carries on with the last plan it received. Your tank never depends on HA being up.
- A full schedule write makes the lamp pause for a few seconds and resets its output. That's why it happens once a day in the dark, and otherwise only when you press **Apply**.

Automatic updates respect **manual mode** and preserve the lamp's current manual levels. Explicit Apply, acclimation and moonlight changes can switch the lamp to auto mode. The integration confirms the schedule by reading it back before recording the update as applied.

Failed automatic updates retry every five minutes inside a window of at most 30 minutes. A write the lamp has acknowledged is never repeated automatically in the same night, even if reading it back doesn't confirm it; press **Apply** to write again. A restart resumes a pending update only inside that window; it does not rewrite an already confirmed plan or catch up during daylight. If the window is missed, a notification explains the pending update. If your target never has a dark interval between two zero points, automatic updates pause until you add one or apply changes explicitly. A single zero point between ramps is not a dark interval.

Home Assistant reads the lamp every five minutes and right after each write. In between, the output sensors follow the schedule minute by minute. A single failed read is ignored; after two in a row the lamp shows as unavailable.

## Requirements

- A **K7 Pro**. Only the K7 Pro IV has been tested. The K7 Mini is not supported yet.
- The lamp in **LAN mode**: in the Noo-Psyche app, join the lamp to your Wi-Fi.
- Home Assistant 2025.2 or newer, able to reach the lamp on **TCP port 8266**. If the lamp is on a separate IoT VLAN, allow HA → lamp on 8266.
- A DHCP reservation for the lamp is strongly recommended.

## Installation

### HACS (recommended)
1. In HACS, open ⋮ → **Custom repositories** and add `https://github.com/jacarmichael/ha-k7-lamp` with category **Integration**.
2. Install **Noo-Psyche K7 Lamp** and restart Home Assistant.

### Manual
Copy `custom_components/k7_lamp` into your Home Assistant `config/custom_components/` folder, then restart.

## Setup

1. Go to **Settings → Devices & services → Add integration → Noo-Psyche K7 Lamp**.
2. Enter the lamp's IP address. The port defaults to 8266.

Initial setup reads the lamp and saves its current schedule as an immutable **Original (lamp name)** backup for that lamp. The card lists this separately from shared profiles and loads it with its original times. Older shared **Original (from lamp)** profiles are retained and labelled **Original (legacy, lamp unknown)** because the old format did not record which lamp they belonged to. On upgrading, each new per-lamp backup captures the schedule seen at the first setup with this version.

Invalid saved programs are paused and preserved in `.storage/k7_lamp.program` under `invalid_programs`, and a repair issue (**Settings → Repairs**) explains why. Starting acclimation or moonlight again replaces the old program and resolves the issue. Other lamps remain usable.

### The card
The card registers itself as a dashboard resource. Add it to any dashboard:

```yaml
type: custom:k7-schedule-card
# entry_id: ...   # only needed if you have several K7 lamps
```

It works best full width, for example in a sections view with `grid_options: {columns: full}`.

If your dashboards are in **YAML mode**, add the resource yourself:
```yaml
lovelace:
  resources:
    - url: /k7_lamp/k7-schedule-card.js?v=0.5.2
      type: module
```

## Using it

- **Edit and Apply:** change the curve, then press **Apply to lamp**. If you're using acclimation or moonlight, the button says **Update target**.
- **Load…:** choose a saved profile, a preset or the current schedule. Loaded schedules are shifted to your *Day starts at* hour unless you untick "Align loaded profiles".
- **Import QR / Show QR:** exchange schedules with the Noo-Psyche app and other reefers.
- **Acclimate…:** set a start % and a number of days. The banner distinguishes today's planned percentage from the last confirmed percentage; the intensity sensor reports the confirmed value (unknown until the first confirmation). When you stop, you can keep the confirmed level or go straight to the full schedule.
- **Moonlight…:**
  - **Level at full moon:** 15% by default.
  - **Channels:** Royal Blue 100% and Cyan 70% by default.
  - **Only while the moon is up:** limits moonlight to moonrise–moonset.
  - The line under the chart shows tonight's moon. Tick **Show what the lamp runs** to see the actual plan as dashed lines.

## Limitations

- Only one client at a time talks to the lamp reliably. The integration serialises its own access and retries, but the Noo-Psyche app polling at the same moment can cause the odd failed read.
- The lamp has 24 hourly schedule points, so moonrise and moonset are accurate to the hour on the lamp. The calculation itself is within about 15 minutes of the US Naval Observatory.
- Light levels are whole percentages, so a thin crescent moon rounds to 0–1%.
- Master/slave lamp groups have not been tested. The integration controls the lamp you configure.
- "Output" sensors are estimates calculated from the schedule. The lamp doesn't report what it's actually emitting.
- QR schedules retain all 24 slots, including duplicate times and disabled all-zero midnight slots. Unused slots do not participate in interpolation or moonlight. Day-start alignment shifts actual timestamps, including minutes. The K7 format cannot represent an active zero-output boundary at exactly 00:00; a shift that would create one is refused instead of changing the curve silently. Choose a different day start in that case.

## Development checks

Use Python 3.13 and Node.js 18 or newer:

```sh
python -m pip install pytest ruff homeassistant==2025.2.0
ruff check .
python -m pytest -q
node --test tests/test_frontend.cjs
```

The HA tests use real Home Assistant classes, platform setup and storage with a simulated lamp. The frontend tests use the original card methods with DOM/media fakes. No test connects to aquarium hardware.

The daily plan rules live in `planner.py` without Home Assistant imports and are tested in `tests/test_planner.py`. The card repeats a few schedule calculations from `protocol.py`; both copies are tested against `tests/schedule_vectors.json`.

## Troubleshooting

- **Cannot connect:** check the IP, that the lamp is in LAN mode, and your firewall/VLAN rules for TCP 8266 from the HA host.
- **Card doesn't show:** hard-refresh the browser. In the companion app, use Settings → Companion app → Debugging → Reset frontend cache.
- **Testing by hand:** `tools/k7_test.py --host <lamp-ip> status` reads the lamp from any computer with Python 3.9+. Other commands write to the lamp, so only run them while you can watch the tank.
- **Checking firmware assumptions:** `allset-same` writes the lamp's own schedule back and lists every slot it reads back differently; the integration needs these to match. `midnight-test` shows whether the lamp skips all-zero 00:00 slots, as the app and the integration assume, and then restores your schedule. Each writes flash, so run them once, not routinely.

## Credits

- **[bitbarista/k7-led-controller](https://github.com/bitbarista/k7-led-controller)** (MIT): the K7 protocol, the app QR format, the presets and the editor design.
- **[SunCalc](https://github.com/mourner/suncalc)** (BSD-2): the moon position and illumination algorithm, ported to Python.
- **Bundled libraries:** Chart.js and chartjs-plugin-dragdata (MIT), jsQR (Apache-2.0), QRCode.js (MIT). See [NOTICE.md](NOTICE.md).

## License

MIT. See [LICENSE](LICENSE).
