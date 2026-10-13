# Changelog

## 0.6.0b6 (beta)

- The card logs its version to the browser console, to check which version a page is running.
- Schedule card: the "Matches / Draft differs" label now follows changes made to the lamp (manual levels, mode, the app) instead of needing a reload, and Apply can no longer write old manual levels back.
- Schedule card: **Manual…** panel with live manual level sliders, and a **Back to schedule** button while the lamp is in manual. Slider moves are batched and sent once you pause.
- The integration waits out the few seconds the lamp is busy after a level change before talking to it again, so quick successive changes and the regular poll no longer fail.

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
