# Changelog

## 0.5.2

- Preserve duplicate schedule slots and app padding through reads, QR exchange, profiles and writes; exclude disabled slots from interpolation and moonlight.
- Validate moon channel indices before storage and quarantine previously invalid programs without blocking other lamps.
- Restore Home Assistant 2025.2 compatibility and reject K7 Mini model prefixes.
- Serialize complete program changes; confirm writes by read-back and persist applied cycle/percentage.
- Respect manual mode during automatic updates, avoid daytime restart writes, and retry failures within an actual dark interval of at most 30 minutes.
- Shift real timestamps (including minutes), select individual editor slots, and refuse unrepresentable midnight dark boundaries.
- Keep original backups per lamp and display actual read-back schedules and pending updates.
- Fix moonlight form submission and stop QR camera streams on panel changes, including delayed permission responses.
- Add schedule, Home Assistant integration and frontend regression coverage to CI.

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
