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
- **Mode selector and manual levels:** switch the lamp between auto (runs the schedule) and manual (fixed levels). Moving a channel's manual level slider puts the lamp in manual; switching back to auto returns it to its schedule, which is never changed by manual control.
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

- Once a day, while the tank is still dark shortly before lights-on, Home Assistant writes the day's plan to the lamp. The plan is your schedule, scaled for acclimation if one is running, plus tonight's moon.
- If Home Assistant is down, the lamp carries on with the last plan it received. Your tank never depends on HA being up.
- While the lamp is in **manual** mode, the daily write is skipped so it stays in manual. Switching back to auto writes today's plan straight away. Pressing **Apply** puts the lamp back in auto.
- A full schedule write makes the lamp pause for a few seconds and resets its output. That's why it happens once a day in the dark, and otherwise only when you press **Apply**.

## Requirements

- A **K7 Pro**. Only the K7 Pro IV has been tested. The K7 Mini is not supported yet.
- The lamp in **LAN mode**: in the Noo-Psyche app, join the lamp to your Wi-Fi.
- Home Assistant 2025.2 or newer, able to reach the lamp on **TCP port 8266**. If the lamp is on a separate IoT VLAN, allow HA → lamp on 8266.
- A DHCP reservation for the lamp is strongly recommended.
- Master/slave lamp groups work. In the Noo-Psyche app, pair the slave to the master, then join the master to your Wi-Fi. Add only the master to Home Assistant; the slave follows it.

## Installation

### HACS (recommended)
1. In HACS, open ⋮ → **Custom repositories** and add `https://github.com/jacarmichael/ha-k7-lamp` with category **Integration**.
2. Install **Noo-Psyche K7 Lamp** and restart Home Assistant.

### Manual
Copy `custom_components/k7_lamp` into your Home Assistant `config/custom_components/` folder, then restart.

## Setup

1. Go to **Settings → Devices & services → Add integration → Noo-Psyche K7 Lamp**.
2. Enter the lamp's IP address. The port defaults to 8266.

Setup only reads from the lamp. The integration saves the lamp's current schedule as the profile **"Original (from lamp)"**.

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
    - url: /k7_lamp/k7-schedule-card.js?v=0.5.0
      type: module
```

## Using it

- **Edit and Apply:** change the curve, then press **Apply to lamp**. If you're using acclimation or moonlight, the button says **Update target**.
- **Load…:** choose a saved profile, a preset or the current schedule. Loaded schedules are shifted to your *Day starts at* hour unless you untick "Align loaded profiles".
- **Import QR / Show QR:** exchange schedules with the Noo-Psyche app and other reefers.
- **Acclimate…:** set a start % and a number of days. A banner shows the current day and %, with a Stop button. When you stop, you can keep the current level or go straight to the full schedule.
- **Moonlight…:**
  - **Level at full moon:** 15% by default.
  - **Channels:** Royal Blue 100% and Cyan 70% by default.
  - **Only while the moon is up:** limits moonlight to moonrise–moonset.
  - The line under the chart shows tonight's moon. Tick **Show what the lamp runs** to see the actual plan as dashed lines.

## Limitations

- Only one client at a time talks to the lamp reliably. The integration serialises its own access and retries, but the Noo-Psyche app polling at the same moment can cause the odd failed read.
- The lamp has 24 hourly schedule points, so moonrise and moonset are accurate to the hour on the lamp. The calculation itself is within about 15 minutes of the US Naval Observatory.
- Light levels are whole percentages, so a thin crescent moon rounds to 0–1%.
- "Output" sensors are estimates calculated from the schedule. The lamp doesn't report what it's actually emitting.

## Troubleshooting

- **Cannot connect:** check the IP, that the lamp is in LAN mode, and your firewall/VLAN rules for TCP 8266 from the HA host.
- **Card doesn't show:** hard-refresh the browser. In the companion app, use Settings → Companion app → Debugging → Reset frontend cache.
- **Testing by hand:** from the `custom_components/k7_lamp` folder (installed, or in a copy of this repo), `python3 tools/k7_test.py --host <lamp-ip> status` reads the lamp. It needs only Python 3.9+, so it also runs on a laptop. Other commands write to the lamp, so only run them while you can watch the tank.

## Credits

- **[bitbarista/k7-led-controller](https://github.com/bitbarista/k7-led-controller)** (MIT): the K7 protocol, the app QR format, the presets and the editor design.
- **[SunCalc](https://github.com/mourner/suncalc)** (BSD-2): the moon position and illumination algorithm, ported to Python.
- **Bundled libraries:** Chart.js and chartjs-plugin-dragdata (MIT), jsQR (Apache-2.0), QRCode.js (MIT). See [NOTICE.md](NOTICE.md).

## License

MIT. See [LICENSE](LICENSE).
