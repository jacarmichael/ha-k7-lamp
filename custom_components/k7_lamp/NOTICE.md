# Credits

## bitbarista/k7-led-controller (MIT, © 2026 Carl Leach)
https://github.com/bitbarista/k7-led-controller

This integration is an independent Python/JS implementation; no code was copied
from that project. It does depend on its work for:

- the K7 TCP protocol (command bytes, frame layout, ALL_SET/ALL_READ payloads),
  documented in `arduino/src/K7Lamp.cpp`
- the Noo-Psyche app QR schedule format (`appQrPayload()` in
  `website/community-presets.html`)
- the editor design (drag-chart schedule editor with a colour preview strip)
- `frontend/presets.json`, built from the K7 Pro entries of
  `community-presets/index.json` (built-in and reviewed community presets)

Full licence: `frontend/vendor/k7-led-controller.LICENSE.txt`.

## SunCalc (BSD-2-Clause, © 2014 Vladimir Agafonkin)
https://github.com/mourner/suncalc

`moon.py` is a Python port of SunCalc's moon position and illumination
functions (based on Jean Meeus's *Astronomical Algorithms*). The moonlight
level per channel follows bitbarista's lunar overlay (Royal Blue, Cyan at 70%).

## Bundled libraries (`frontend/vendor/`)

| File | Project | Licence |
|------|---------|---------|
| chart.umd.min.js | Chart.js 4.4.3 | MIT |
| chartjs-plugin-dragdata.min.js | chartjs-plugin-dragdata | MIT |
| jsQR.js | jsQR 1.4.0 (cozmo/jsQR) | Apache-2.0 |
| qrcode.min.js | QRCode.js (davidshimjs) | MIT, see qrcodejs.LICENSE.txt |
