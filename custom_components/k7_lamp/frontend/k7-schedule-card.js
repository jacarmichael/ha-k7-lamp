/*
 * K7 schedule editor card for the k7_lamp integration.
 *
 * Edits the lamp's 24-point schedule, imports/exports Noo-Psyche app QR codes
 * and applies the result with one ALL_SET. Editor design, QR format and the
 * bundled presets come from bitbarista/k7-led-controller (MIT); see NOTICE.md.
 */
const BASE = "/k7_lamp";
const VERSION = "0.5.2";
const COLORS = ["#f5f5f5", "#3b3bff", "#22c55e", "#9b30ff", "#00d5ff", "#ff3030"];

const loaded = {};
function loadScript(path, globalName) {
  if (window[globalName]) return Promise.resolve();
  if (!loaded[path]) {
    loaded[path] = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = `${BASE}/${path}?v=${VERSION}`;
      s.onload = resolve;
      s.onerror = () => reject(new Error(`Could not load ${path}`));
      document.head.appendChild(s);
    });
  }
  return loaded[path];
}
const loadChart = () =>
  loadScript("vendor/chart.umd.min.js", "Chart").then(() =>
    loadScript("vendor/chartjs-plugin-dragdata.min.js", "ChartJSdragDataPlugin"));

const clone = (x) => JSON.parse(JSON.stringify(x));
const clamp = (v) => Math.max(0, Math.min(100, Math.round(Number(v) || 0)));
const pad = (n) => String(n).padStart(2, "0");
const rowsKey = (d) => JSON.stringify([d.manual, d.schedule]);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Mirrors protocol.py day_start / photoperiod / shift_rows / align_day_start.
const lit = (r) => r.slice(2).some((v) => v > 0);
const sortRows = (s) => [...s].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
const activeRows = (s) => sortRows([...new Map(s.filter((r) => r.some((v) => v !== 0))
  .map((r) => [r[0] * 60 + r[1], r])).values()]);
const fmtHour = (h) => `${pad(Math.floor(h))}:${pad(Math.round((h % 1) * 60))}`;
function dayStart(schedule) {
  const rows = activeRows(schedule);
  for (let i = 0; i < rows.length; i++) {
    if (lit(rows[i]) && !lit(rows[(i - 1 + rows.length) % rows.length])) return rows[i][0] + rows[i][1] / 60;
  }
  return null;
}
function photoperiod(schedule) {
  const start = dayStart(schedule);
  if (start === null) return null;
  const rows = activeRows(schedule);
  const idx = rows.findIndex((r, i) => lit(r) && !lit(rows[(i - 1 + rows.length) % rows.length]));
  for (let k = 1; k <= rows.length; k++) {
    const r = rows[(idx + k) % rows.length];
    if (!lit(r)) return [start, r[0] + r[1] / 60];
  }
  return null;
}
function shiftRows(schedule, hours) {
  const delta = Math.round(hours * 60);
  const shiftedMinute = (r) => ((r[0] * 60 + r[1] + delta) % 1440 + 1440) % 1440;
  const active = activeRows(schedule);
  active.forEach((r, i) => {
    if (shiftedMinute(r) === 0 && !lit(r)
      && (lit(active[(i - 1 + active.length) % active.length]) || lit(active[(i + 1) % active.length]))) {
      throw new Error("Cannot shift a dark boundary to 00:00: K7 treats it as a disabled slot");
    }
  });
  return sortRows(schedule.map((r) => {
    if (!r.some((v) => v !== 0)) return [...r];
    const minute = shiftedMinute(r);
    return [Math.floor(minute / 60), minute % 60, ...r.slice(2)];
  }));
}
function alignDayStart(schedule, hour) {
  const cur = dayStart(schedule);
  return cur === null ? clone(schedule) : shiftRows(schedule, hour - cur);
}
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—");

function hexToRgb(h) {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function blend(levels) {
  // Additive mix of the channel colours, brightness set by the strongest channel.
  const rgb = [0, 0, 0];
  levels.forEach((v, i) => hexToRgb(COLORS[i]).forEach((c, k) => (rgb[k] += c * v)));
  const peak = Math.max(...rgb);
  if (!peak) return "rgb(0,0,0)";
  const scale = (255 * Math.max(...levels)) / 100 / peak;
  return `rgb(${rgb.map((c) => Math.round(c * scale)).join(",")})`;
}

class K7ScheduleCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
  }

  static getStubConfig() {
    return {};
  }

  getCardSize() {
    return 9;
  }

  getGridOptions() {
    return { columns: "full", min_columns: 6 };
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._started) {
      this._started = true;
      this._last = Date.now();
      this._init();
    } else if (this._draft && !this._panel && Date.now() - this._last > 60000) {
      // Pick up lamp changes (mode, availability, schedule edited in the app).
      this._last = Date.now();
      const before = JSON.stringify(this._lamp);
      this._refresh().then(() => {
        if (JSON.stringify(this._lamp) !== before && !this._panel) this._render();
      }).catch(() => {});
    }
  }

  async _init() {
    this.attachShadow({ mode: "open" });
    this._channels = ["White", "Royal Blue", "Green", "UV", "Cyan", "Red"];
    this._visible = this._channels.map(() => true);
    this._linked = false;
    this._slot = 12;
    this._cameraRequest = 0;
    this._panel = null; // "import" | "export" | "save" | "confirm"
    this._msg = null;
    this._render();
    try {
      await Promise.all([loadChart(), this._refresh(), this._loadPresets()]);
      this._draft = clone(this._baseline());
      this._render();
    } catch (err) {
      this._error(err);
    }
  }

  async _ws(msg) {
    return this._hass.callWS(msg);
  }

  async _refresh() {
    const data = await this._ws({ type: "k7_lamp/get" });
    this._channels = data.channels;
    this._profiles = data.profiles;
    const want = this._config.entry_id;
    this._lamp = data.lamps.find((l) => !want || l.entry_id === want) || null;
    if (!this._lamp) throw new Error("No K7 lamp is loaded");
  }

  async _loadPresets() {
    try {
      const r = await fetch(`${BASE}/presets.json?v=${VERSION}`);
      this._presets = (await r.json()).presets || [];
    } catch {
      this._presets = [];
    }
  }

  _lampData() {
    return { manual: this._lamp.manual, schedule: this._lamp.schedule };
  }

  _acc() {
    return this._lamp?.acclimation || null;
  }

  // What the draft is compared with: HA's target when a program (acclimation/moonlight) runs, else the lamp.
  _baseline() {
    return this._lamp?.program && this._lamp.target ? this._lamp.target : this._lampData();
  }

  _moonOn() {
    return !!this._lamp?.moon?.config?.enabled;
  }

  // Shift a freshly loaded schedule to the stored day start (unless turned off).
  _aligned(schedule) {
    const want = this._lamp?.day_start;
    if (want === null || want === undefined || this._keepTimes) return { schedule, note: "" };
    const cur = dayStart(schedule);
    if (cur === null || cur === want) return { schedule, note: "" };
    const diff = Math.round((((want - cur + 36) % 24) - 12) * 100) / 100;
    return { schedule: alignDayStart(schedule, want),
             note: ` Shifted ${diff > 0 ? "+" : ""}${diff} h to start at ${pad(want)}:00.` };
  }

  _dirty() {
    return this._draft && this._lamp && rowsKey(this._draft) !== rowsKey(this._baseline());
  }

  _updateLamp(res) {
    this._lamp = res.lamps.find((l) => l.entry_id === this._lamp.entry_id) || this._lamp;
  }

  _error(err) {
    this._msg = { kind: "error", text: err?.message || String(err) };
    this._render();
  }

  _info(text) {
    this._msg = { kind: "info", text };
    this._render();
  }

  _setPanel(panel) {
    if (this._panel === "import" && panel !== "import") this._stopCamera();
    this._panel = panel;
  }

  // ---------- rendering ----------

  _render() {
    const root = this.shadowRoot;
    if (!root) return;
    const ready = !!this._draft;
    const lamp = this._lamp;
    root.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="head">
          <div class="title">${esc(this._config.title || lamp?.title || "K7 schedule")}</div>
          ${lamp ? `<span class="chip ${lamp.available ? "" : "bad"}">${lamp.available ? (lamp.auto ? "Auto" : "Manual") : "Offline"}</span>` : ""}
          ${ready ? `<span class="chip ${this._dirty() ? "warn" : "ok"}">${this._dirty()
            ? (this._acc() ? "Draft differs from target" : "Draft differs from lamp")
            : (this._lamp?.program ? "Matches target" : "Matches lamp")}</span>` : ""}
        </div>
        ${this._msg ? `<div class="msg ${this._msg.kind}">${esc(this._msg.text)}<button class="x" data-act="dismiss">×</button></div>` : ""}
        ${lamp?.program_error ? `<div class="msg error">${esc(lamp.program_error)}</div>` : ""}
        ${lamp?.pending ? `<div class="msg">A plan update is pending. It will retry in the next safe window, or you can Apply now.</div>` : ""}
        ${ready && this._acc() ? this._accBanner() : ""}
        ${ready ? this._body() : `<div class="loading">Loading…</div>`}
      </ha-card>`;
    if (ready) {
      this._bind();
      this._drawChart();
    } else {
      root.querySelector("[data-act=dismiss]")?.addEventListener("click", () => {
        this._msg = null;
        this._render();
      });
    }
  }

  _accBanner() {
    const a = this._acc();
    const pct = Math.max(0, Math.min(100, a.percent));
    return `<div class="acc">
      <div class="acctext"><b>Acclimation</b> · day ${a.day} of ${a.days} · <b>${a.percent}%</b>
        <span class="muted">(${a.start_percent}% → 100%, ends ${esc(a.ends)})</span></div>
      <div class="bar"><div style="width:${pct}%"></div></div>
      <div class="muted small">The chart shows the full target. Planned today: ${a.percent}%. Last confirmed: ${a.applied_percent == null ? "unknown" : `${a.applied_percent}%`}.
      ${this._lamp.rebuild_time ? `Daily update window starts at ${this._lamp.rebuild_time}.` : "No safe dark interval; automatic updates are paused."}</div>
      <button data-act="acc-stop">Stop…</button></div>`;
  }

  _dayStartRow() {
    const period = photoperiod(this._draft.schedule);
    const pref = this._lamp.day_start;
    const cur = period ? period[0] : null;
    const opts = [`<option value="" ${pref == null ? "selected" : ""}>not set</option>`]
      .concat(Array.from({ length: 24 }, (_, h) =>
        `<option value="${h}" ${pref === h ? "selected" : ""}>${pad(h)}:00</option>`)).join("");
    const len = period ? ((period[1] - period[0] + 24) % 24 || 24) : 0;
    return `<div class="row daystart">
      <label>Day starts at <select data-act="daystart" ${cur === null ? "disabled" : ""}>${opts}</select></label>
      <span class="muted">${period ? `photoperiod ${fmtHour(period[0])}–${fmtHour(period[1])} (${Math.round(len * 100) / 100} h)` : "no dark period to shift"}</span>
      ${pref != null && cur !== null && cur !== pref ? `<button data-act="align">Shift to ${pad(pref)}:00</button>` : ""}
      <label class="tog"><input type="checkbox" data-act="keeptimes" ${this._keepTimes ? "" : "checked"}>Align loaded profiles</label>
      <span class="grow"></span>
      ${this._lamp.program ? `<label class="tog"><input type="checkbox" data-act="showeff" ${this._showEff ? "checked" : ""}>Show what the lamp runs</label>` : ""}
    </div>`;
  }

  _moonLine() {
    const m = this._lamp.moon;
    if (!m) return "";
    const cfg = m.config;
    const status = cfg.enabled
      ? (m.level_now > 0 ? `moonlight now ${m.level_now}%` : `moonlight on (up to ${cfg.max_level}%)`)
      : "moonlight off";
    return `<div class="moonline muted">${m.icon} ${esc(m.phase_name)} · ${m.illumination}% lit ·
      rises ${fmtTime(m.next_rise)} · sets ${fmtTime(m.next_set)} · <b>${status}</b></div>`;
  }

  _body() {
    const d = this._draft;
    const strip = d.schedule
      .map((r, i) => `<div class="cell ${i === this._slotIndex() ? "sel" : ""}" data-slot="${i}"
          style="background:${blend(r.slice(2))}" title="${pad(r[0])}:${pad(r[1])}"><span>${pad(r[0])}</span></div>`)
      .join("");
    const toggles = this._channels
      .map((c, i) => `<label class="tog"><input type="checkbox" data-ch="${i}" ${this._visible[i] ? "checked" : ""}>
          <i style="background:${COLORS[i]}"></i>${esc(c)}</label>`)
      .join("");
    const row = d.schedule[this._slotIndex()];
    const sliders = this._channels
      .map((c, i) => `<div class="sl"><span class="lbl"><i style="background:${COLORS[i]}"></i>${esc(c)}</span>
          <input type="range" min="0" max="100" value="${row[i + 2]}" data-lv="${i}">
          <input type="number" min="0" max="100" value="${row[i + 2]}" data-num="${i}"></div>`)
      .join("");
    const profileOpts = Object.keys(this._profiles || {})
      .map((n) => `<option value="p:${esc(n)}">${esc(n === "Original (from lamp)" ? "Original (legacy, lamp unknown)" : n)}</option>`)
      .join("");
    const presetOpts = (this._presets || [])
      .map((p, i) => `<option value="b:${i}">${esc(p.name)}${p.source === "community" ? " (community)" : ""}</option>`)
      .join("");
    return `
      <div class="chartwrap"><canvas></canvas></div>
      <div class="strip">${strip}</div>
      ${this._dayStartRow()}
      ${this._moonLine()}
      <div class="row toggles">${toggles}
        <label class="tog link"><input type="checkbox" data-act="link" ${this._linked ? "checked" : ""}>Move channels together</label>
      </div>
      <div class="slot">
        <div class="slothead">Slot ${pad(row[0])}:${pad(row[1])}
          <button data-act="prev">‹</button><button data-act="next">›</button></div>
        ${sliders}
      </div>
      <div class="row">
        <select data-act="pick">
          <option value="">Load a schedule…</option>
          <option value="lamp">Current lamp schedule</option>
          ${this._lamp.original ? `<option value="original">${esc(this._lamp.original.name)}</option>` : ""}
          ${profileOpts ? `<optgroup label="Saved profiles">${profileOpts}</optgroup>` : ""}
          ${presetOpts ? `<optgroup label="Presets (bitbarista)">${presetOpts}</optgroup>` : ""}
        </select>
        <button data-act="save">Save as…</button>
        <button data-act="delete" ${this._selectedProfile() ? "" : "disabled"}>Delete profile</button>
      </div>
      <div class="row">
        <button data-act="import">Import QR</button>
        <button data-act="export">Show QR</button>
        <button data-act="sync">Sync clock</button>
        <button data-act="acc" ${this._acc() ? "disabled" : ""}>Acclimate…</button>
        <button data-act="moon">Moonlight…</button>
        <span class="grow"></span>
        <button data-act="revert" ${this._dirty() ? "" : "disabled"}>Revert</button>
        <button class="primary" data-act="apply" ${(this._dirty() || this._lamp.pending) && this._lamp.available ? "" : "disabled"}>${this._lamp.program ? "Update target" : "Apply to lamp"}</button>
      </div>
      ${this._panelHtml()}`;
  }

  _panelHtml() {
    switch (this._panel) {
      case "confirm":
        return `<div class="panel">
          <p>${this._acc()
            ? `This becomes the new acclimation target. The lamp gets it right away at today's <b>${this._acc().percent}%</b>, then rises daily as planned.`
            : "This rewrites the lamp's stored schedule and puts it in <b>auto</b> mode with the current time."}
          The lamp pauses for a few seconds and its output resets.</p>
          <div class="row"><span class="grow"></span><button data-act="cancel">Cancel</button>
          <button class="primary" data-act="apply-yes">Write to lamp</button></div></div>`;
      case "acc":
        return `<div class="panel">
          <p>Ramp up linearly to the <b>schedule in the editor</b> (the full target). Today's step is written
          now; after that ${this._lamp.rebuild_time ? `the daily update window starts at ${this._lamp.rebuild_time}, in the target's dark interval` : "automatic updates require a dark interval in the target schedule"}.</p>
          <div class="row">
            <label>Start at <input type="number" min="1" max="99" value="50" data-act="acc-start"> %</label>
            <label>over <input type="number" min="1" max="120" value="30" data-act="acc-days"> days</label>
            <span class="grow"></span>
            <button data-act="cancel">Cancel</button><button class="primary" data-act="acc-go">Start acclimation</button>
          </div></div>`;
      case "moon": {
        const cfg = this._lamp.moon?.config || {};
        const w = cfg.channels || {};
        const chans = this._channels.map((c, i) => `<label class="mch"><i style="background:${COLORS[i]}"></i>${esc(c)}
            <input type="number" min="0" max="100" value="${w[i] ?? w[String(i)] ?? 0}" data-moonch="${i}">%</label>`).join("");
        return `<div class="panel">
          <p>Night-time light that follows the real moon over your home: brightness tracks the moon's
          illumination. ${this._lamp.rebuild_time ? `The daily update window starts at ${this._lamp.rebuild_time}, in the target's dark interval.` : "Automatic updates are paused until the target has a dark interval."} Daylight hours are never dimmed;
          the moon level is only used where it's brighter than your schedule.</p>
          <div class="row">
            <label class="tog"><input type="checkbox" data-moon="enabled" ${cfg.enabled ? "checked" : ""}>Moonlight on</label>
            <label>Level at full moon <input type="number" min="1" max="50" value="${cfg.max_level ?? 15}" data-moon="max_level"> %</label>
            <label class="tog"><input type="checkbox" data-moon="follow_moon" ${cfg.follow_moon !== false ? "checked" : ""}>Only while the moon is up</label>
          </div>
          <div class="muted small">Share of the moon level per channel (0 = off):</div>
          <div class="row mchs">${chans}</div>
          <div class="row"><span class="grow"></span><button data-act="cancel">Cancel</button>
          <button class="primary" data-act="moon-save">Save moonlight</button></div></div>`;
      }
      case "acc-stop":
        return `<div class="panel">
          <p>Stop the acclimation now?</p>
          <div class="row"><span class="grow"></span><button data-act="cancel">Cancel</button>
          <button data-act="acc-keep" ${this._acc()?.applied_percent == null ? "disabled" : ""}>Keep confirmed ${this._acc()?.applied_percent ?? "unknown"}%</button>
          <button class="primary" data-act="acc-full">Go to the full schedule</button></div></div>`;
      case "save":
        return `<div class="panel"><div class="row">
          <input type="text" data-act="name" placeholder="Profile name" value="${esc(this._selectedProfile() || "")}">
          <button data-act="cancel">Cancel</button><button class="primary" data-act="save-yes">Save</button></div></div>`;
      case "import":
        return `<div class="panel">
          <p>Paste the QR text, choose a screenshot or photo of the QR code, or scan it with the camera.</p>
          <textarea data-act="qrtext" rows="3" placeholder="323232323232#0000…"></textarea>
          <div class="row">
            <label class="btn">Image…<input type="file" accept="image/*" data-act="qrfile" hidden></label>
            <button data-act="camera">${this._stream ? "Stop camera" : "Camera"}</button>
            <span class="grow"></span>
            <button data-act="cancel">Close</button><button class="primary" data-act="qrload">Load text</button>
          </div>
          <video playsinline muted ${this._stream ? "" : "hidden"}></video></div>`;
      case "export":
        return `<div class="panel">
          <div class="qr"></div>
          <textarea readonly rows="3">${esc(this._qrPayload || "")}</textarea>
          <div class="row"><span class="grow"></span><button data-act="copy">Copy text</button>
          <button data-act="cancel">Close</button></div></div>`;
      default:
        return "";
    }
  }

  _slotIndex() {
    return Math.max(0, Math.min(this._slot ?? 0, this._draft.schedule.length - 1));
  }

  _selectedProfile() {
    return this._source?.startsWith("p:") ? this._source.slice(2) : null;
  }

  _drawChart() {
    const canvas = this.shadowRoot.querySelector("canvas");
    if (!canvas || !window.Chart) return;
    this._chart?.destroy();
    const grid = getComputedStyle(this).getPropertyValue("--divider-color") || "#8884";
    const text = getComputedStyle(this).getPropertyValue("--secondary-text-color") || "#888";
    this._chart = new window.Chart(canvas, {
      type: "line",
      data: {
        datasets: this._channels.map((c, ch) => ({
          label: c,
          hidden: !this._visible[ch],
          data: this._draft.schedule.map((r) => ({ x: r[0] + r[1] / 60, y: r.some((v) => v !== 0) ? r[ch + 2] : null })),
          spanGaps: true,
          borderColor: COLORS[ch],
          backgroundColor: COLORS[ch],
          borderWidth: 2,
          pointRadius: 4,
          pointHitRadius: 12,
          tension: 0,
        })).concat(this._showEff && this._lamp.effective ? this._channels.map((c, ch) => ({
          label: `${c} (lamp)`,
          hidden: !this._visible[ch],
          data: this._lamp.effective.schedule.map((r) => ({ x: r[0] + r[1] / 60, y: r.some((v) => v !== 0) ? r[ch + 2] : null })),
          spanGaps: true,
          borderColor: COLORS[ch],
          borderDash: [5, 4],
          borderWidth: 1.5,
          pointRadius: 0,
          dragData: false,
          tension: 0,
        })) : []),
      },
      options: {
        animation: false,
        maintainAspectRatio: false,
        interaction: { mode: "nearest", intersect: true },
        scales: {
          x: { type: "linear", min: 0, max: 24, ticks: { stepSize: 2, color: text, callback: (v) => pad(v) }, grid: { color: grid } },
          y: { min: 0, max: 100, ticks: { stepSize: 25, color: text, callback: (v) => `${v}%` }, grid: { color: grid } },
        },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { title: (it) => { const r = this._draft.schedule[it[0].dataIndex]; return `${pad(r[0])}:${pad(r[1])}`; } } },
          dragData: {
            round: 0,
            dragX: false,
            showTooltip: true,
            onDragStart: (e, ds, idx) => {
              this._dragFrom = this._draft.schedule[idx][ds + 2];
            },
            onDrag: (e, ds, idx, value) => {
              const v = clamp(value.y ?? value);
              if (this._linked) {
                const delta = v - this._dragFrom;
                this._chart.data.datasets.forEach((set, ch) => {
                  if (ch < this._channels.length && ch !== ds && !set.hidden) set.data[idx].y = clamp(this._draft.schedule[idx][ch + 2] + delta);
                });
                this._chart.update("none");
              }
            },
            onDragEnd: (e, ds, idx, value) => {
              const v = clamp(value.y ?? value);
              const row = this._draft.schedule[idx];
              const delta = v - row[ds + 2];
              row[ds + 2] = v;
              if (this._linked) {
                this._visible.forEach((vis, ch) => {
                  if (vis && ch !== ds) row[ch + 2] = clamp(row[ch + 2] + delta);
                });
              }
              this._slot = idx;
              this._render();
            },
          },
        },
        onClick: (e, els) => {
          if (els.length) {
            this._slot = els[0].index;
            this._render();
          }
        },
      },
    });
  }

  _bind() {
    const root = this.shadowRoot;
    const on = (sel, ev, fn) => root.querySelectorAll(sel).forEach((el) => el.addEventListener(ev, fn));
    on("[data-slot]", "click", (e) => {
      this._slot = +e.currentTarget.dataset.slot;
      this._render();
    });
    on("[data-ch]", "change", (e) => {
      this._visible[+e.target.dataset.ch] = e.target.checked;
      this._render();
    });
    on("[data-lv],[data-num]", "change", (e) => {
      const ch = +(e.target.dataset.lv ?? e.target.dataset.num);
      const row = this._draft.schedule[this._slotIndex()];
      const v = clamp(e.target.value);
      const delta = v - row[ch + 2];
      row[ch + 2] = v;
      if (this._linked) this._visible.forEach((vis, c) => { if (vis && c !== ch) row[c + 2] = clamp(row[c + 2] + delta); });
      this._render();
    });
    on("[data-lv]", "input", (e) => {
      const num = root.querySelector(`[data-num="${e.target.dataset.lv}"]`);
      if (num) num.value = e.target.value;
    });
    on("[data-act]", "click", (e) => this._action(e.currentTarget.dataset.act, e));
    on("[data-act=pick]", "change", (e) => this._pick(e.target.value));
    on("[data-act=link]", "change", (e) => (this._linked = e.target.checked));
    on("[data-act=keeptimes]", "change", (e) => { this._keepTimes = !e.target.checked; });
    on("[data-act=showeff]", "change", (e) => { this._showEff = e.target.checked; this._render(); });
    on("[data-act=daystart]", "change", async (e) => {
      const hour = e.target.value === "" ? null : +e.target.value;
      try {
        const res = await this._ws({ type: "k7_lamp/set_day_start", entry_id: this._lamp.entry_id, hour });
        this._updateLamp(res);
        if (hour === null) return this._info("Day start cleared.");
        const before = dayStart(this._draft.schedule);
        this._draft.schedule = alignDayStart(this._draft.schedule, hour);
        this._info(before === hour ? `Day starts at ${pad(hour)}:00.` :
          `Draft shifted to start at ${pad(hour)}:00. Apply to send it to the lamp.`);
      } catch (err) { this._error(err); }
    });
    on("[data-act=qrfile]", "change", (e) => this._qrFromFile(e.target.files?.[0]));
    if (this._panel === "export" && this._qrPayload) {
      loadScript("vendor/qrcode.min.js", "QRCode").then(() => {
        const box = root.querySelector(".qr");
        if (box) new window.QRCode(box, { text: this._qrPayload, width: 240, height: 240, correctLevel: window.QRCode.CorrectLevel.M });
      });
    }
    if (this._panel === "import" && this._stream) {
      const video = root.querySelector("video");
      video.srcObject = this._stream;
      video.play().catch(() => {});
      this._scanLoop(video);
    }
  }

  _pick(value) {
    try {
      if (!value) return;
      let src = null;
      if (value === "lamp") src = this._lampData();
      else if (value === "original") src = this._lamp.original;
      else if (value.startsWith("p:")) src = this._profiles[value.slice(2)];
      else if (value.startsWith("b:")) src = this._presets[+value.slice(2)];
      if (!src) return;
      const r = value === "lamp" || value === "original" ? { schedule: clone(src.schedule), note: "" } : this._aligned(clone(src.schedule));
      this._source = value;
      this._draft = { manual: clone(src.manual), schedule: r.schedule };
      this._info(value === "lamp" ? "Loaded the current schedule." : `Loaded into the draft.${r.note} Nothing changes on the lamp until you Apply.`);
    } catch (err) { this._error(err); }
  }

  async _action(act, e) {
    try {
      switch (act) {
        case "dismiss": this._msg = null; return this._render();
        case "prev":
        case "next": {
          const n = this._draft.schedule.length;
          const i = (this._slotIndex() + (act === "next" ? 1 : n - 1)) % n;
          this._slot = i;
          return this._render();
        }
        case "cancel": this._stopCamera(); this._setPanel(null); return this._render();
        case "apply": this._setPanel("confirm"); return this._render();
        case "apply-yes": {
          const acc = !!this._acc();
          this._setPanel(null);
          this._info("Writing schedule to the lamp…");
          const res = await this._ws({ type: "k7_lamp/apply", entry_id: this._lamp.entry_id, ...this._draft });
          this._updateLamp(res);
          if (acc) return this._info("Acclimation target updated.");
          return this._info(res.written ? "Schedule written. The lamp is in auto mode." : "The lamp already had this schedule; nothing written.");
        }
        case "revert":
          this._draft = clone(this._baseline());
          this._source = this._acc() ? null : "lamp";
          return this._info(this._acc() ? "Draft reset to the acclimation target." : "Draft reset to the lamp's schedule.");
        case "acc": this._setPanel("acc"); return this._render();
        case "moon": this._setPanel("moon"); return this._render();
        case "moon-save": {
          const root = this.shadowRoot;
          const val = (k) => root.querySelector(`[data-moon=${k}]`);
          const channels = {};
          root.querySelectorAll("[data-moonch]").forEach((el) => (channels[el.dataset.moonch] = clamp(el.value)));
          const max = +val("max_level").value;
          const enabled = val("enabled").checked;
          const follow_moon = val("follow_moon").checked;
          if (!(max >= 1 && max <= 50)) return this._error("Level at full moon must be 1–50%.");
          this._setPanel(null);
          this._info("Saving moonlight…");
          const res = await this._ws({ type: "k7_lamp/set_moonlight", entry_id: this._lamp.entry_id,
            enabled, max_level: max, follow_moon, channels });
          this._updateLamp(res);
          return this._info(this._moonOn() ? "Moonlight saved; the lamp has tonight's moon." : "Moonlight off.");
        }
        case "align": {
          const r = this._aligned(this._draft.schedule);
          this._draft.schedule = r.schedule;
          return this._info(`Draft shifted.${r.note} Apply to send it to the lamp.`);
        }
        case "acc-go": {
          const start = +this.shadowRoot.querySelector("[data-act=acc-start]").value;
          const days = +this.shadowRoot.querySelector("[data-act=acc-days]").value;
          if (!(start >= 1 && start <= 99 && days >= 1 && days <= 120)) return this._error("Start must be 1–99% and duration 1–120 days.");
          this._setPanel(null);
          this._info("Starting acclimation…");
          const res = await this._ws({ type: "k7_lamp/start_acclimation", entry_id: this._lamp.entry_id,
            ...this._draft, start_percent: start, days });
          this._updateLamp(res);
          return this._info(`Acclimation started at ${start}% over ${days} days.`);
        }
        case "acc-stop": this._setPanel("acc-stop"); return this._render();
        case "acc-keep":
        case "acc-full": {
          this._setPanel(null);
          const res = await this._ws({ type: "k7_lamp/stop_acclimation", entry_id: this._lamp.entry_id,
            keep_current: act === "acc-keep" });
          this._updateLamp(res);
          this._draft = clone(this._baseline());
          return this._info(act === "acc-keep" ? "Acclimation stopped; the lamp keeps the reduced schedule." : "Acclimation stopped; full schedule written.");
        }
        case "sync":
          await this._ws({ type: "k7_lamp/sync_clock", entry_id: this._lamp.entry_id });
          return this._info("Lamp clock set to Home Assistant's local time.");
        case "save": this._setPanel("save"); return this._render();
        case "save-yes": {
          const name = this.shadowRoot.querySelector("[data-act=name]").value.trim();
          if (!name) return;
          const res = await this._ws({ type: "k7_lamp/save_profile", name, ...this._draft });
          this._profiles = res.profiles;
          this._source = `p:${name}`;
          this._setPanel(null);
          return this._info(`Saved profile "${name}".`);
        }
        case "delete": {
          const name = this._selectedProfile();
          if (!name) return;
          if (e.currentTarget.dataset.armed !== "1") {
            e.currentTarget.dataset.armed = "1";
            e.currentTarget.textContent = `Really delete "${name}"?`;
            return;
          }
          const res = await this._ws({ type: "k7_lamp/delete_profile", name });
          this._profiles = res.profiles;
          this._source = null;
          return this._info(`Deleted profile "${name}".`);
        }
        case "import": this._setPanel("import"); this._msg = null; return this._render();
        case "qrload": return this._importPayload(this.shadowRoot.querySelector("[data-act=qrtext]").value);
        case "camera": return this._stream ? (this._stopCamera(), this._render()) : this._startCamera();
        case "export": {
          const res = await this._ws({ type: "k7_lamp/to_qr", ...this._draft });
          this._qrPayload = res.payload;
          this._setPanel("export");
          return this._render();
        }
        case "copy":
          await navigator.clipboard.writeText(this._qrPayload);
          return this._info("QR text copied.");
      }
    } catch (err) {
      this._error(err);
    }
  }

  // ---------- QR import ----------

  async _importPayload(text) {
    try {
      const res = await this._ws({ type: "k7_lamp/parse_qr", payload: String(text || "") });
      this._stopCamera();
      const r = this._aligned(res.schedule);
      this._draft = { manual: res.manual, schedule: r.schedule };
      this._source = null;
      this._setPanel(null);
      this._info(`QR code loaded into the draft.${r.note} Check it, then Apply to lamp.`);
    } catch (err) {
      this._error(err);
    }
  }

  async _decode(imageData) {
    await loadScript("vendor/jsQR.js", "jsQR");
    const code = window.jsQR(imageData.data, imageData.width, imageData.height, { inversionAttempts: "attemptBoth" });
    return code?.data || null;
  }

  async _qrFromFile(file) {
    if (!file) return;
    try {
      const bmp = await createImageBitmap(file);
      // Downscale big phone photos; jsQR is slow on 12 MP images.
      const scale = Math.min(1, 1600 / Math.max(bmp.width, bmp.height));
      const c = document.createElement("canvas");
      c.width = Math.round(bmp.width * scale);
      c.height = Math.round(bmp.height * scale);
      const ctx = c.getContext("2d");
      ctx.drawImage(bmp, 0, 0, c.width, c.height);
      const text = await this._decode(ctx.getImageData(0, 0, c.width, c.height));
      if (!text) return this._error("No QR code found in that image. Try a sharper or closer crop.");
      await this._importPayload(text);
    } catch (err) {
      this._error(err);
    }
  }

  async _startCamera() {
    this._lastBad = null;
    if (!navigator.mediaDevices?.getUserMedia) return this._error("Camera not available here (needs HTTPS). Use an image instead.");
    if (this._panel !== "import" || this._cameraPending) return;
    const request = (this._cameraRequest || 0) + 1;
    this._cameraRequest = request;
    this._cameraPending = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
      if (request !== this._cameraRequest || this._panel !== "import" || !this.isConnected) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      this._stream = stream;
      this._render();
    } catch (err) {
      if (request === this._cameraRequest) this._error(err);
    } finally {
      if (request === this._cameraRequest) this._cameraPending = false;
    }
  }

  _stopCamera() {
    this._cameraRequest = (this._cameraRequest || 0) + 1;
    this._cameraPending = false;
    this._stream?.getTracks().forEach((t) => t.stop());
    this._stream = null;
  }

  async _scanLoop(video) {
    await loadScript("vendor/jsQR.js", "jsQR");
    const c = document.createElement("canvas");
    const ctx = c.getContext("2d", { willReadFrequently: true });
    const tick = async () => {
      if (!this._stream || !video.isConnected) return;
      if (video.readyState >= 2 && video.videoWidth) {
        c.width = video.videoWidth;
        c.height = video.videoHeight;
        ctx.drawImage(video, 0, 0);
        const text = await this._decode(ctx.getImageData(0, 0, c.width, c.height));
        if (text && text !== this._lastBad) {
          this._lastBad = text;
          return this._importPayload(text);
        }
      }
      setTimeout(tick, 250);
    };
    tick();
  }

  disconnectedCallback() {
    this._stopCamera();
  }
}

const STYLE = `
  ha-card { padding: 12px 16px 16px; }
  .head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
  .title { font-size: 1.2em; font-weight: 500; margin-right: auto; }
  .chip { font-size: .8em; padding: 2px 8px; border-radius: 10px; background: var(--secondary-background-color); }
  .chip.ok { color: var(--success-color, #2e7d32); }
  .chip.warn { color: var(--warning-color, #e65100); }
  .chip.bad { color: var(--error-color, #c62828); }
  .msg { display: flex; align-items: center; padding: 6px 10px; border-radius: 6px; margin: 4px 0 8px; font-size: .9em;
         background: var(--secondary-background-color); }
  .msg.error { color: var(--error-color, #c62828); }
  .msg .x { margin-left: auto; border: none; background: none; font-size: 1.2em; padding: 0 4px; }
  .loading { padding: 24px; color: var(--secondary-text-color); }
  .chartwrap { position: relative; height: 240px; }
  .strip { display: grid; grid-template-columns: repeat(24, 1fr); gap: 2px; margin: 6px 0 10px; }
  .cell { height: 26px; border-radius: 3px; cursor: pointer; display: flex; align-items: flex-end; justify-content: center;
          border: 2px solid transparent; box-sizing: border-box; }
  .cell span { font-size: 9px; color: #888; mix-blend-mode: difference; }
  .cell.sel { border-color: var(--primary-color); }
  .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 8px 0; }
  .grow { flex: 1; }
  .toggles { font-size: .85em; gap: 4px 12px; }
  .tog { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; }
  .tog i, .lbl i { width: 10px; height: 10px; border-radius: 50%; display: inline-block; border: 1px solid #8886; }
  .tog.link { margin-left: auto; }
  .slot { border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px 10px; }
  .slothead { display: flex; align-items: center; gap: 6px; font-weight: 500; margin-bottom: 4px; }
  .slothead button { padding: 0 10px; }
  .sl { display: grid; grid-template-columns: 110px 1fr 56px; align-items: center; gap: 8px; font-size: .9em; }
  .lbl { display: inline-flex; align-items: center; gap: 6px; }
  .sl input[type=range] { width: 100%; }
  .sl input[type=number] { width: 52px; }
  button, .btn, select, input[type=text], textarea {
    font: inherit; color: var(--primary-text-color); background: var(--card-background-color);
    border: 1px solid var(--divider-color); border-radius: 6px; padding: 6px 10px; cursor: pointer; }
  button:disabled { opacity: .45; cursor: default; }
  button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
  select { max-width: 100%; }
  textarea { width: 100%; box-sizing: border-box; font-family: monospace; font-size: .8em; cursor: text; }
  input[type=text] { flex: 1; min-width: 140px; cursor: text; }
  .panel { border: 1px solid var(--divider-color); border-radius: 8px; padding: 10px; margin-top: 8px; }
  .panel p { margin: 0 0 8px; font-size: .9em; }
  .acc { border: 1px solid var(--primary-color); border-radius: 8px; padding: 8px 10px; margin-bottom: 8px;
         display: grid; grid-template-columns: 1fr auto; gap: 6px 10px; align-items: center; }
  .acc .bar, .acc .small { grid-column: 1; }
  .acc button { grid-column: 2; grid-row: 1 / span 3; }
  .bar { height: 6px; border-radius: 3px; background: var(--secondary-background-color); overflow: hidden; }
  .bar div { height: 100%; background: var(--primary-color); }
  .muted { color: var(--secondary-text-color); }
  .small { font-size: .85em; }
  .panel input[type=number] { width: 60px; font: inherit; }
  .daystart { font-size: .9em; gap: 6px 12px; }
  .daystart select { padding: 2px 6px; }
  .moonline { font-size: .9em; margin: 2px 0 6px; }
  .mchs { gap: 6px 14px; font-size: .9em; }
  .mch { display: inline-flex; align-items: center; gap: 4px; }
  .mch i { width: 10px; height: 10px; border-radius: 50%; display: inline-block; border: 1px solid #8886; }
  .mch input { width: 52px; }
  .qr { display: flex; justify-content: center; padding: 12px; background: #fff; border-radius: 6px; margin-bottom: 8px; }
  video { width: 100%; max-height: 320px; border-radius: 6px; margin-top: 8px; background: #000; }
  @media (max-width: 500px) { .sl { grid-template-columns: 90px 1fr 50px; } .cell span { display: none; } }
`;

customElements.define("k7-schedule-card", K7ScheduleCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "k7-schedule-card",
  name: "K7 schedule editor",
  description: "Edit, import (QR) and apply the Noo-Psyche K7 lamp schedule.",
});
