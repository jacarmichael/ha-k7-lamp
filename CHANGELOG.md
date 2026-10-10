# Changelog

## 0.5.1

- Pass Home Assistant's hassfest validation (manifest key order, no braces in action descriptions).

## 0.5.0 (first public release)

- Local control of a Noo-Psyche K7 Pro over the LAN (TCP 8266); no cloud, no extra hardware.
- Sensors: mode, estimated output per channel, acclimation intensity, moonlight level, connectivity.
- Schedule editor card: drag-chart editor, colour preview, saved profiles, bundled presets,
  QR import (paste / image / camera) and export in the Noo-Psyche app format.
- "Day starts at": shifts loaded profiles so the photoperiod starts at your hour.
- Acclimation: linear ramp from a start % to 100% over N days.
- Moonlight that follows the real moon (phase, moonrise/moonset) at your HA location.
- The daily plan is written once, in the dark shortly before lights-on; the lamp runs it on its own.
