# Changelog

## 0.6.0b2 (beta)

- `tools/k7_test.py` now ships inside the integration, so HACS installs it and updates no longer remove it.
- Manual level sliders for each channel. Moving one puts the lamp in manual mode; the schedule is left alone, so switching back to auto returns to it.
- Removed the read-only "manual level" diagnostic sensors (replaced by the sliders); their old entities are cleaned up automatically.

## 0.6.0b1 (beta)

- Mode select (auto/manual). While the lamp is in manual mode the daily plan write is skipped; switching back to auto writes today's plan.
- Docs: master/slave groups work (pair the slave in the app, add only the master to Home Assistant).

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
