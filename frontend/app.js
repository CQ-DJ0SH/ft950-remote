/* FT-950 Remote — single-page client (vanilla JS, no build step).
   Look and behaviour of panels, title bar, frequency spinner and settings follow
   TM-V71 Remote. */
"use strict";

const $ = (id) => document.getElementById(id);
// installed PWA (own window) or a browser tab — some elements only show in the PWA
const pwaMq = window.matchMedia("(display-mode: standalone)");
const markPwa = () => document.documentElement.classList.toggle("pwa", pwaMq.matches || navigator.standalone === true);
markPwa(); pwaMq.addEventListener?.("change", markPwa);

const store = {
  get(k, d) { try { const v = localStorage.getItem("ft950." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("ft950." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// ---------------------------------------------------------------- helpers
let toastTimer = null;
function toast(text, err = false) {
  const t = $("toast");
  t.textContent = text;
  t.classList.toggle("err", err);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, err ? 6000 : 4000);
}

async function request(method, path, body, raw = false) {
  const opt = { method };
  if (body !== undefined) {
    opt.body = raw ? body : JSON.stringify(body);
    if (!raw) opt.headers = { "content-type": "application/json" };
  }
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = typeof data.detail === "string" ? data.detail
      : Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : `HTTP ${r.status}`;
    toast(msg, true);
    throw new Error(msg);
  }
  return data;
}
const api = (path, body) => request(body === undefined ? "GET" : "POST", path, body);

// Reconnecting socket. Returns a handle whose close() ends it for good (the
// HackRF stream is only wanted while its panel is visible and switched on).
function ws(path, onMsg, onState) {
  let delay = 500, closed = false, sock = null;
  const open = () => {
    if (closed) return;
    sock = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + path);
    sock.onopen = () => { delay = 500; if (onState) onState(true); };
    sock.onmessage = (e) => onMsg(JSON.parse(e.data));
    sock.onclose = () => {
      if (onState) onState(false);
      if (closed) return;
      setTimeout(open, delay);
      delay = Math.min(delay * 2, 5000);
    };
  };
  open();
  return { close() { closed = true; try { sock?.close(); } catch { /* already gone */ } } };
}

function seg(el, items, onPick) {
  el.innerHTML = "";
  for (const [value, label] of items) {
    const b = document.createElement("button");
    b.className = "sg";
    b.dataset.v = value;
    b.textContent = label;
    b.onclick = () => {
      // light the choice at once; the radio's status confirms it (or, after
      // 2.5 s without confirmation, the real state shows again)
      el.dataset.pending = value;
      el.dataset.pendingUntil = Date.now() + 2500;
      for (const x of el.children) { x.classList.toggle("on", x === b); x.classList.toggle("pending", x === b); }
      Promise.resolve(onPick(value)).catch(() => { el.dataset.pendingUntil = 0; });
    };
    el.appendChild(b);
  }
}
function segSet(el, value) {
  if (el.dataset.pending) {
    if (String(value) === el.dataset.pending || Date.now() > +el.dataset.pendingUntil) {
      delete el.dataset.pending;
      for (const b of el.children) b.classList.remove("pending");
    } else return;                                  // radio not there yet: keep the click's highlight
  }
  for (const b of el.children) b.classList.toggle("on", b.dataset.v === String(value));
}

// Sliders the user is moving must not be overwritten by the live status.
const touched = new Map();
function slider(id, fmt, send) {
  const el = $(id), out = $(id + "-val");
  let timer;
  el.addEventListener("input", () => {
    touched.set(id, Date.now());
    out.textContent = fmt(+el.value);
    clearTimeout(timer);
    timer = setTimeout(() => send(+el.value).catch(() => {}), 180);
  });
  return (v) => {
    if (Date.now() - (touched.get(id) || 0) < 1500 || v === undefined || v === null) return;
    el.value = v;
    out.textContent = fmt(v);
  };
}

// ---------------------------------------------------------------- chrome: theme, screws, collapsing
(function theme() {
  const root = document.documentElement;
  const apply = (t) => { root.dataset.theme = t; $("theme").textContent = t === "dark" ? "☾" : "☀"; };
  apply(store.get("theme", "dark"));
  $("theme").onclick = () => {
    const t = root.dataset.theme === "dark" ? "light" : "dark";
    store.set("theme", t);
    apply(t);
    drawSysGraph();
  };
})();

// decorative instrument-panel corner screws, one set of four per panel (V71)
for (const p of document.querySelectorAll(".panel")) {
  for (const pos of ["tl", "tr", "bl", "br"]) {
    const s = document.createElement("span");
    s.className = "screw screw-" + pos;
    s.setAttribute("aria-hidden", "true");
    p.appendChild(s);
  }
}

// every panel collapses to its head; the state is remembered per panel
for (const p of document.querySelectorAll(".panel")) {
  const btn = p.querySelector(".panel-toggle");
  if (!btn) continue;
  const set = (collapsed) => {
    p.classList.toggle("collapsed", collapsed);
    btn.setAttribute("aria-expanded", String(!collapsed));
    store.set("collapsed." + p.id, collapsed);
    if (!collapsed && p.id === "digi") requestAnimationFrame(buildAxis);
    if (p.id === "hackrf") requestAnimationFrame(hrfPanelToggled);   // size canvases, start/stop
  };
  set(store.get("collapsed." + p.id, false));
  btn.onclick = () => set(!p.classList.contains("collapsed"));
}

(function chainToggle() {
  const btn = $("chain-toggle"), chain = $("chain");
  const set = (open) => {
    chain.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
    store.set("chainOpen", open);
  };
  set(store.get("chainOpen", true));
  btn.onclick = () => set(chain.hidden);
})();

// ---------------------------------------------------------------- state
let meta = null;
let radio = { connected: false };
let rxBoxOn = store.get("rxBox", false);       // green RX window in the waterfall (Settings ▸ FT8), default off
let digiMode = "FT8";
let digiDepthCfg = "2";                 // decode depth setting: "1".."3" or "AUTO"
let myCall = "";
let myGrid = "";                       // own locator (map popup)
const PERIOD = { FT8: 15, FT4: 7.5 };
const SIGNAL_BW = { FT8: 50, FT4: 83 };
const BAND_EDGES = {
  "1.8": [1.8e6, 2.0e6], "3.5": [3.5e6, 4.0e6], "5": [5.25e6, 5.45e6], "7": [7.0e6, 7.3e6],
  "10": [10.1e6, 10.15e6], "14": [14.0e6, 14.35e6], "18": [18.068e6, 18.168e6],
  "21": [21.0e6, 21.45e6], "24.5": [24.89e6, 24.99e6], "28": [28.0e6, 29.7e6], "50": [50e6, 54e6],
};
// S-meter scale: SM0 raw value for each S-point, after hamlib's FT-950
// calibration table (approximate — S9 ≈ 130, +60 dB = 255), not yet checked
// against a signal generator.
const S_POINTS = [[12, "S1"], [40, "3"], [65, "5"], [95, "7"], [130, "9"], [172, "+20"], [220, "+40"], [255, "+60"]];

// always with the S: "S5", "S9", "S9+20"
const sLbl = (l) => (/^\d$/.test(l) ? "S" + l : l.startsWith("+") ? "S9" + l : l);
function sUnits(raw) {
  if (raw <= S_POINTS[0][0]) return "S0";
  for (let i = 1; i < S_POINTS.length; i++) {
    const [r1, l1] = S_POINTS[i];
    if (raw <= r1) {
      const [r0] = S_POINTS[i - 1];
      return sLbl(raw - r0 < r1 - raw ? S_POINTS[i - 1][1] : l1);
    }
  }
  return "S9+60";
}
// ---------------------------------------------------------------- analog meter (as the FT-950's)
// The needle shows exactly the meter position the radio reports over CAT
// (SM0 in receive, RM5 PO in transmit, 0-255 = full deflection). The S scale
// follows the approximate FT-950 calibration above; PO is labelled in percent
// of full scale until it is checked against the radio's own meter.
// Analog meter after the FT-2000 / FT-950 LCD: blue-white concentric scales.
// Raw 0-255 CAT readings map linearly onto the arc; the W/A/dB/SWR labels are
// placed at the radio's nominal scale positions (not calibrated against this rig).
// Raw RM5/RM6 readings → watts / SWR: hamlib's FT-950 calibration points.
// Approximate — not yet checked against this rig's own meter.
const PO_CAL = [[0, 0], [10, 0.8], [50, 8], [100, 26], [150, 54], [200, 92], [250, 140]];
const SWR_CAL = [[0, 1], [12, 1], [39, 1.35], [65, 1.5], [89, 2], [242, 5]];
function calInterp(tab, x, inv = false) {
  const t = inv ? tab.map(([a, b]) => [b, a]) : tab;
  if (x <= t[0][0]) return t[0][1];
  for (let i = 1; i < t.length; i++) {
    if (x <= t[i][0]) {
      const [x0, y0] = t[i - 1], [x1, y1] = t[i];
      return y0 + (y1 - y0) * (x - x0) / (x1 - x0);
    }
  }
  const [x0, y0] = t[t.length - 2], [x1, y1] = t[t.length - 1];
  return y0 + (y1 - y0) * (x - x0) / (x1 - x0);           // extrapolate
}
const poWatts = (raw) => calInterp(PO_CAL, raw);
const swrOf = (raw) => calInterp(SWR_CAL, raw);
const rawFrac = (tab, v) => Math.min(1, calInterp(tab, v, true) / 255);
const AM = { cx: 170, cy: 330, a0: -27, a1: 27 };
const amAng = (f) => AM.a0 + (AM.a1 - AM.a0) * f;
const amAngle = (f) => amAng(Math.max(0, Math.min(1, f)));
const amPt = (f, r) => {
  const a = amAng(f) * Math.PI / 180;
  return [AM.cx + r * Math.sin(a), AM.cy - r * Math.cos(a)];
};
(function buildMeter() {
  const ns = "http://www.w3.org/2000/svg", g = $("am-scales");
  const el = (tag, attrs, text) => {
    const e = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text != null) e.textContent = text;
    g.appendChild(e);
    return e;
  };
  const arc = (f0, f1, r, cls) => {
    const [x0, y0] = amPt(f0, r), [x1, y1] = amPt(f1, r);
    el("path", { d: `M${x0} ${y0} A${r} ${r} 0 0 1 ${x1} ${y1}`, class: cls });
  };
  const tick = (f, r0, r1, cls = "am-tick") => {
    const [x0, y0] = amPt(f, r0), [x1, y1] = amPt(f, r1);
    el("line", { x1: x0, y1: y0, x2: x1, y2: y1, class: cls });
  };
  const lbl = (f, r, text, cls = "am-lbl") => {
    const [x, y] = amPt(f, r);
    el("text", { x, y, class: cls }, text);      // upright, not turned with the arc
  };
  const side = (f, r, text, cls = "am-side") => {   // scale name left of the arc start
    const [x, y] = amPt(f - 0.02, r);
    el("text", { x: x - 4, y, class: cls }, text);
  };
  // Scales after the FT-950's own meter: white lines and figures on black, a steel-blue
  // band above S9 (and a short one at the start of the S scale) and for ALC.
  // S scale
  const s9 = 130 / 255, s1 = 12 / 255;
  arc(0, s1, 290, "am-arc blue");
  arc(s1, s9, 290, "am-arc");
  arc(s9, 1, 290, "am-arc blue");
  for (let k = 1; k < 24; k++) tick(k / 24, 290, 295, "am-tick minor");
  for (const [raw, label] of S_POINTS) {
    const f = raw / 255;
    tick(f, 290, 300);
    lbl(f, 312, label.replace("S1", "1"), raw > 130 ? "am-lbl" : "am-lbl big");
  }
  lbl(-0.06, 316, "S", "am-lbl big");
  lbl(1.08, 304, "dB");
  // PO (W): 0 … 150
  arc(0, 1, 278, "am-arc");
  for (let k = 1; k < 30; k++) tick(k / 30, 278, 273, "am-tick minor");
  for (const w of [10, 50, 100, 150]) { const f = rawFrac(PO_CAL, w); tick(f, 278, 270); lbl(f, 261, w); }
  lbl(0.005, 263, "0");
  lbl(1.1, 263, "W", "am-lbl big");
  side(0, 278, "PO");
  // COMP (dB): 0 · 10 · 20
  arc(0.06, 0.94, 236, "am-arc");
  for (let k = 1; k < 20; k++) tick(0.06 + k * 0.041, 236, 240, "am-tick minor");
  for (const [f, d] of [[0.06, "0"], [0.47, "10"], [0.88, "20"]]) { tick(f, 236, 244); lbl(f, 251, d); }
  lbl(1.02, 234, "dB");
  side(0.06, 236, "COMP");
  // ID (A): 0 · 5 · 10 · 15
  arc(0.02, 0.96, 214, "am-arc");
  for (const [f, a] of [[0.02, "0"], [0.33, "5"], [0.62, "10"], [0.9, "15"]]) { tick(f, 214, 207); lbl(f, 200, a); }
  lbl(1.04, 212, "A", "am-lbl big");
  side(0.02, 214, "ID");
  // SWR: 1 · 1.5 · 2 · 3 · 5 · ∞
  arc(0.14, 1, 176, "am-arc");
  for (const [f, v] of [[0.14, "1"], ...[1.5, 2, 3, 5].map((x) => [Math.max(0.2, rawFrac(SWR_CAL, x)), String(x)]), [1.03, "∞"]]) {
    if (f <= 1) tick(f, 176, 182);
    lbl(f, 189, v);
  }
  side(0.14, 176, "SWR");
  // ALC (blue zone) and VDD with its 13.8 V mark
  arc(0.16, 0.52, 158, "am-arc blue");
  arc(0.52, 0.92, 158, "am-arc");
  tick(0.76, 158, 163); lbl(0.76, 168, "13.8", "am-lbl small");
  lbl(0.96, 156, "V", "am-lbl big");
  {                                  // "VDD" right after the V, upright (as on the FT-950)
    const [vx, vy] = amPt(0.96, 156);
    el("text", { x: vx + 19, y: vy + 3, class: "am-lbl small" }, "VDD");
  }
  side(0.16, 158, "ALC");
})();
const amNeedle = $("am-needle");
// Needle ballistics like a moving-coil meter: critically damped (no overshoot),
// rising in ~0.3 s, falling back a little slower.
const NEEDLE_W_UP = 11, NEEDLE_W_DOWN = 6;          // rad/s
const needle = { pos: 0, vel: 0, target: 0, raf: 0, t: 0 };
function needleStep(t) {
  const dt = Math.min(0.05, (t - (needle.t || t)) / 1000);
  needle.t = t;
  const w = needle.target >= needle.pos ? NEEDLE_W_UP : NEEDLE_W_DOWN;
  needle.vel += (w * w * (needle.target - needle.pos) - 2 * w * needle.vel) * dt;
  needle.pos += needle.vel * dt;
  amNeedle.setAttribute("transform", `rotate(${amAngle(Math.max(0, Math.min(1, needle.pos)))} ${AM.cx} ${AM.cy})`);
  if (Math.abs(needle.target - needle.pos) < 0.001 && Math.abs(needle.vel) < 0.005) {
    needle.pos = needle.target; needle.vel = 0; needle.raf = 0; needle.t = 0; return;   // at rest
  }
  needle.raf = requestAnimationFrame(needleStep);
}
// digital S meter under VFO-B: 40 segments over the meter's raw 0 … 255, blue to S9
// (raw 130), red above; the scale marks sit where the needle's S points are
const DSM_N = 40, DSM_S9 = 130;
$("dsm-bar").innerHTML = Array.from({ length: DSM_N }, (_, i) =>
  `<i${(i + 0.5) / DSM_N * 255 > DSM_S9 ? ' class="hot"' : ""}></i>`).join("");
$("dsm-scale").innerHTML = `<span class="dsm-s">S</span>` + S_POINTS.map(([raw, l]) =>
  `<span class="${raw > DSM_S9 ? "hot" : ""}" style="left:${raw / 255 * 100}%">${l.replace("S1", "1")}</span>`).join("");
function dsmSet(raw) {
  const lit = Math.round(raw / 255 * DSM_N);
  [...$("dsm-bar").children].forEach((seg, i) => seg.classList.toggle("on", i < lit));
}
function meterSet(raw, label) {
  needle.target = raw / 255;
  if (uiCovered) return;                                // behind Settings: no animation, set on close
  if (!needle.raf) needle.raf = requestAnimationFrame(needleStep);
}

// number in the 7-segment font, unit in the UI font (DSEG7 has no real W, A or %)
// clarifier scale in the LCD over ±1 kHz (5 … 95 % of the box), as on the FT-950:
// tall ticks at the ends, small ones every 200 Hz hanging below the line
$("tune-mark").insertAdjacentHTML("beforebegin", Array.from({ length: 11 }, (_, k) =>
  `<b class="ts-t${k === 0 || k === 10 ? " end" : ""}" style="left:${5 + k * 9}%"></b>`).join(""));
// every digit place of a 7-segment readout gets a dimmed "8" behind it (unlit segments,
// as on the FT-950's LCD); "!" is DSEG's blank digit, "-" takes a digit place too
function seg8(str) { return String(str).replace(/[0-9!\-]/g, (d) => `<span class="s8">${d}</span>`); }
function lcdVal(id, num, unit, prefix = "") {
  $(id).innerHTML = (prefix ? `<small class="u">${prefix}</small>` : "") + seg8(esc(String(num))) +
    (unit ? `<small class="u uu">${unit}</small>` : "");
}

// PO / SWR / ALC as segmented bars; segments past the warn mark light red
// (SWR from 2:1 by the calibration table, ALC beyond the meter's ALC zone)
const TXSEG = 24;
const TX_WARN = { "m-po": (TXSEG - 3) / TXSEG, "m-swr": rawFrac(SWR_CAL, 2), "m-alc": 0.52, "m-id": (TXSEG - 3) / TXSEG,   // PO, ID: the last three segments
  "m-vdd": (TXSEG - 3) / TXSEG };   // VDD: the last three = from 14.5 V (its own scale, see vddShow)
// ID (final-stage current, RM7): linear along the meter's 5/10/15 A marks — approximate
const idAmps = (raw) => raw / 255 * 16.5;
// …and from below: the first three VDD segments = under 10 V
const TX_WARN_LOW = { "m-vdd": 3 / TXSEG };
for (const id of Object.keys(TX_WARN)) {
  $(id).innerHTML = Array.from({ length: TXSEG }, (_, i) => {
    const f = (i + 0.5) / TXSEG;
    return `<i${f >= TX_WARN[id] || f < (TX_WARN_LOW[id] ?? 0) ? ' class="hot"' : ""}></i>`;
  }).join("");
}
// peakRaw: the held peak segment (shown while transmitting, when above the live bar)
function txBar(id, raw, peakRaw = null) {
  const lit = Math.round(raw / 255 * TXSEG);
  const p = peakRaw == null ? -1 : Math.round(peakRaw / 255 * TXSEG) - 1;
  [...$(id).children].forEach((seg, i) => {
    seg.classList.toggle("on", i < lit);
    seg.classList.toggle("pk", i === p && p >= lit - 1);
  });
}
// Peak hold: the highest reading of each meter during one transmission. After TX the
// bars and values show these peaks (PEAK LED lit) until the next TX resets them.
// SWR only counts with some power out (keying edges read nonsense at zero PO).
const PK_SWR_MIN_PO = 13;                     // raw ≈ 5 % of the PO scale
const pk = { tx: false, held: false, po: 0, swr: 0, alc: 0, id: 0, vdd: null };
// CLEAR: drop the held peaks now (back to zero bars and values, VDD back to the supply)
$("pk-clr").onclick = () => {
  if (radio.tx) return;
  Object.assign(pk, { held: false, po: 0, swr: 0, alc: 0, id: 0, vdd: null });
  txVals(0, 0, 0, 0);
  renderRadio(radio);
};
// Settings ▸ Appearance can switch peak hold off: then no held segment, no PEAK HOLD,
// and the bars drop to zero after a transmission (the last values stay readable)
let peakOn = store.get("peakHold", true);
const pkv = (v) => (peakOn ? v : null);
function txVals(po, swr, alc, id) {
  const w = poWatts(po), r = swrOf(swr);
  // fixed digit counts with leading zeros, so the unit behind never jumps
  // no leading zeros: the empty places are DSEG's blank digit "!" (only the dimmed 8
  // shows), so the number keeps its place and the unit does not jump
  lcdVal("v-id", idAmps(id).toFixed(1).padStart(4, "!"), "A");
  lcdVal("v-po", String(Math.round(w)).padStart(3, "!"), "W");
  lcdVal("v-alc", String(Math.round(alc / 255 * 100)).padStart(3, "!"), "%");
  // "≥" always takes its place (hidden below 5:1) for the same reason
  $("v-swr").innerHTML = `<small class="u"${r >= 4.95 ? "" : ' style="visibility:hidden"'}>≥</small>${seg8(Math.min(r, 5).toFixed(1))}<small class="u uu">:1</small>`;   // ratio as the unit, like W / % / A / dB
}
// VDD (RM8, supply voltage): proportional, 13.8 V at the meter's 13.8 mark (76 % of
// the scale, raw ≈ 194) — approximate away from 13.8 V. The bar is the raw reading.
const VDD_138_RAW = 0.76 * 255;
const vddVolts = (raw) => raw / VDD_138_RAW * 13.8;
const VDD_BAR_LO = 9.25, VDD_BAR_HI = 15.25;
let vddRx = null;                              // last reading on receive (slow poll)
function vddShow(raw, held = false) {
  if (raw == null) { txBar("m-vdd", 0); lcdVal("v-vdd", "!!.!", "V"); return; }
  const v = vddVolts(raw);
  // the bar spans 9.25 … 15.25 V, 0.25 V per segment: the first three are under 10 V,
  // the last three from 14.5 V, 13.8 V lights 18 of 24
  const bar = Math.max(0, Math.min(1, (v - VDD_BAR_LO) / (VDD_BAR_HI - VDD_BAR_LO))) * 255;
  txBar("m-vdd", bar, held ? bar : null);        // held: its last segment lit as the peak, like PO … ID
  lcdVal("v-vdd", v.toFixed(1).padStart(4, "!"), "V");
}

// DSP bar graphs (CONTOUR · NOTCH · WIDTH · SHIFT) as on the LCD: 21 segments
const DSEG = 21, DMID = 10;
for (const id of ["bar-contour", "bar-notch", "bar-width", "bar-shift"]) {
  $(id).innerHTML = "<i></i>".repeat(DSEG);
}
// CONTOUR and NOTCH show two rows of dashes, as on the FT-950's LCD
for (const id of ["bar-contour", "bar-notch"]) $(id).classList.add("dual");
// lit(i): the (upper) row; lit2(i): the lower row of a two-row bar
function setBar(id, lit, lit2 = lit) {
  [...$(id).children].forEach((seg, i) => { seg.classList.toggle("on", lit(i)); seg.classList.toggle("on2", lit2(i)); });
}

// ---------------------------------------------------------------- title bar
async function loadBranding() {
  try {
    const b = await api("/api/branding");
    const logo = $("brand-logo"), prev = $("logo-preview");
    prev.innerHTML = "";
    if (b.has_logo) {
      const src = "/api/branding/logo?v=" + b.version;
      logo.src = src;
      logo.hidden = false;
      const pi = document.createElement("img");
      pi.src = src;
      pi.alt = "Logo";
      prev.appendChild(pi);
    } else {
      logo.hidden = true;
      logo.removeAttribute("src");
    }
  } catch { /* backend down */ }
}

function setStat(id, ok) {
  const el = $(id);
  el.classList.toggle("online", ok === true);
  el.classList.toggle("fault", ok === false);
}

// ---------------------------------------------------------------- VFO: big display (live tuning)
const freqEl = $("freq");
let liveHz = null, liveUntil = 0, sendTimer = null;

function fmtHzGroups(hz) {
  const s = String(Math.max(0, hz)).padStart(8, "0");
  return [s.slice(0, 2), s.slice(2, 5), s.slice(5, 8)];
}
// 10 MHz … 1 Hz ("14.195.000", as VFO-B); a leading zero stays
// an unlit digit ("!" is DSEG's all-segments-off glyph).
function renderFreq(hz) {
  const s = String(Math.max(0, hz)).padStart(8, "0");
  let html = "";
  for (let i = 0; i < 8; i++) {
    if (i === 2 || i === 5) html += '<span class="sep">.</span>';
    const lead = i === 0 && hz < 10e6;
    html += `<span class="dg s8${lead ? " lead" : ""}" data-w="${10 ** (7 - i)}">${lead ? "!" : s[i]}</span>`;
  }
  freqEl.innerHTML = html;
  if (!alignVfoBLine.done) { alignVfoBLine.done = true; requestAnimationFrame(alignVfoBLine); }   // first real digits
}

// The divider over VFO-B starts under the first digit of VFO A: the big frequency is
// wider than its column and overhangs on the left, so VFO-B (which carries the line)
// reaches out by the same amount. Measured again whenever the column resizes.
function alignVfoBLine() {
  const col = freqEl.closest(".lcd-freq"), d = freqEl.querySelector(".dg"), vb = col?.querySelector(".vfo-b");
  if (!d || !vb) return;
  const c = col.getBoundingClientRect(), scale = c.width / col.offsetWidth || 1;   // .console zoom
  vb.style.marginLeft = Math.min(0, (d.getBoundingClientRect().left - c.left) / scale) + "px";
}
new ResizeObserver(alignVfoBLine).observe(freqEl.closest(".lcd-freq"));
document.fonts?.ready.then(alignVfoBLine);

function tuneLive(hz) {
  hz = Math.max(30000, Math.min(56000000, Math.round(hz)));
  liveHz = hz;
  liveUntil = Date.now() + 900;
  renderFreq(hz);
  clearTimeout(sendTimer);
  sendTimer = setTimeout(() => { api("/api/frequency", { hz, vfo: "A" }).catch(() => { liveUntil = 0; }); }, 120);
}
// Frequency shuttle next to BAND: offset from the centre = direction and step size
{
  const sh = $("shuttle"), knob = $("sh-knob"), lbl = $("sh-lbl");
  const STEPS = [[0.12, 0], [0.38, 10], [0.62, 100], [0.86, 1000], [1.01, 10000]];
  let held = false, frac = 0, timer = null;
  const fmtStep = (s) => (s >= 1000 ? s / 1000 + " kHz" : s + " Hz");
  const stepOf = (f) => (STEPS.find(([lim]) => Math.abs(f) < lim) || STEPS[STEPS.length - 1])[1];
  const place = (f) => {
    knob.style.left = `${50 + f * 50}%`;
    const s = stepOf(f);
    lbl.textContent = held && s ? (f < 0 ? `◀ DOWN ${fmtStep(s)}` : `UP ${fmtStep(s)} ▶`) : "TUNE";
    sh.classList.toggle("down", held && f < 0 && !!s);
    sh.classList.toggle("up", held && f > 0 && !!s);
  };
  const at = (e) => {
    const r = sh.getBoundingClientRect();
    frac = Math.max(-1, Math.min(1, (e.clientX - (r.left + r.width / 2)) / (r.width / 2)));
    place(frac);
  };
  const tick = () => {
    const s = stepOf(frac);
    if (!s) return;
    const base = Date.now() < liveUntil && liveHz ? liveHz : radio.vfo?.freq_hz;
    if (!base) return;
    const dir = frac < 0 ? -1 : 1;
    // next point of the step grid in the tuning direction (7.074 −10 kHz → 7.070)
    tuneLive(dir < 0 ? Math.ceil(base / s - 1) * s : Math.floor(base / s + 1) * s);
  };
  sh.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    held = true;
    sh.setPointerCapture(e.pointerId);
    at(e); tick();
    clearInterval(timer);
    timer = setInterval(tick, 150);                    // > tuneLive's 120 ms send debounce
  });
  sh.addEventListener("pointermove", (e) => { if (held) at(e); });
  const release = () => { held = false; clearInterval(timer); frac = 0; place(0); };
  for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) sh.addEventListener(ev, release);
}
freqEl.addEventListener("wheel", (e) => {
  const d = e.target.closest(".dg");
  if (!d) return;
  e.preventDefault();
  tuneLive((liveHz ?? radio.vfo?.freq_hz ?? 0) + (e.deltaY < 0 ? 1 : -1) * +d.dataset.w);
}, { passive: false });
let drag = null;
freqEl.addEventListener("pointerdown", (e) => {
  const d = e.target.closest(".dg");
  if (!d) return;
  drag = { y: e.clientY, w: +d.dataset.w, base: liveHz ?? radio.vfo?.freq_hz ?? 0 };
  freqEl.setPointerCapture(e.pointerId);
});
freqEl.addEventListener("pointermove", (e) => {
  if (!drag) return;
  const steps = Math.trunc((drag.y - e.clientY) / 12);
  if (steps) tuneLive(drag.base + steps * drag.w);
});
freqEl.addEventListener("pointerup", () => { drag = null; });

// ---------------------------------------------------------------- VFO: frequency spinner (V71)
// 8 digits, 10 MHz … 1 Hz: ▲ bar / digit / ▼ bar per column. Edits stay
// pending (highlighted frame) until ✓ or Enter, as in TM-V71 Remote.
const PLACES = [1e7, 1e6, 1e5, 1e4, 1e3, 100, 10, 1];
const spin = $("spin");
let editHz = null;

function spinDigits() { return [...spin.querySelectorAll(".dig")]; }
function setSpinner(hz) {
  const s = String(Math.max(0, hz)).padStart(8, "0");
  spinDigits().forEach((inp, i) => { if (document.activeElement !== inp) inp.value = s[i]; });
}
function readSpinner() {
  return parseInt(spinDigits().map((i) => (i.value.replace(/\D/g, "") || "0").slice(-1)).join(""), 10);
}
function markDirty(on) {
  spin.classList.toggle("pending", on);
  if (!on) editHz = null;
}
function bump(idx, dir) {
  if (editHz === null) editHz = radio.vfo?.freq_hz || 0;
  editHz = Math.max(0, Math.min(56000000, editHz + dir * PLACES[idx]));
  markDirty(true);
  setSpinner(editHz);
}
async function applySpinner() {
  const hz = editHz ?? readSpinner();
  if (hz < 30000 || hz > 56000000) { toast("Frequency outside 0.03 … 56 MHz", true); return; }
  try {
    await api("/api/frequency", { hz, vfo: "A" });
    markDirty(false);
    liveHz = hz; liveUntil = Date.now() + 900; renderFreq(hz);
    for (const i of spinDigits()) i.blur();
  } catch { /* toast shown */ }
}
(function buildSpinner() {
  PLACES.forEach((_, idx) => {
    if (idx === 2 || idx === 5) {
      const dot = document.createElement("span");
      dot.className = "fdot";
      dot.textContent = ".";
      spin.appendChild(dot);
    }
    const cell = document.createElement("div");
    cell.className = "dcell";
    const up = document.createElement("span");
    up.className = "dbar";
    up.setAttribute("aria-label", "Digit up");
    up.onclick = () => bump(idx, +1);
    const dig = document.createElement("input");
    dig.className = "dig";
    dig.type = "text";
    dig.inputMode = "numeric";
    dig.maxLength = 1;
    dig.addEventListener("beforeinput", (e) => { if (e.data && /\D/.test(e.data)) e.preventDefault(); });
    dig.addEventListener("focus", () => dig.select());
    dig.addEventListener("input", () => {
      dig.value = dig.value.replace(/\D/g, "").slice(-1);
      editHz = readSpinner();
      markDirty(true);
      const next = spinDigits()[idx + 1];
      if (dig.value && next) { next.focus(); next.select(); }
    });
    dig.addEventListener("keydown", (e) => {
      if (e.key === "Enter") applySpinner();
      else if (e.key === "Escape") { markDirty(false); setSpinner(radio.vfo?.freq_hz || 0); dig.blur(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); bump(idx, +1); }
      else if (e.key === "ArrowDown") { e.preventDefault(); bump(idx, -1); }
      else if (e.key === "Backspace" && !dig.value) {
        const prev = spinDigits()[idx - 1];
        if (prev) { prev.focus(); prev.select(); }
      }
    });
    const dn = document.createElement("span");
    dn.className = "dbar";
    dn.setAttribute("aria-label", "Digit down");
    dn.onclick = () => bump(idx, -1);
    cell.append(up, dig, dn);
    spin.appendChild(cell);
  });
  const unit = document.createElement("span");
  unit.className = "spin-unit";
  unit.textContent = "MHz";
  const apply = document.createElement("button");
  apply.type = "button";
  apply.className = "spin-apply";
  apply.textContent = "✓";
  apply.title = "Apply frequency (Enter)";
  apply.onclick = applySpinner;
  spin.append(unit, apply);
})();

$("up").onclick = () => api("/api/updown", { on: true });
$("dn").onclick = () => api("/api/updown", { on: false });
$("swap").onclick = () => api("/api/vfo/swap", {});
$("split").onclick = () => api("/api/split", { on: !$("split").classList.contains("on") });
$("btn-mon").onclick = () => api("/api/monitor", { on: !$("btn-mon").classList.contains("on") })
  .then(() => $("btn-mon").classList.toggle("on")).catch(() => {});
function showAntTuner(ant, tuner) {
  $("ant-1").classList.toggle("on", ant === "1");
  $("ant-2").classList.toggle("on", ant === "2");
  $("tuner").classList.toggle("on", !!tuner && tuner !== "0");
  $("tuner-start").classList.toggle("on", tuner === "2");
}
for (const n of ["1", "2"]) {
  $("ant-" + n).onclick = () => api("/api/antenna", { ant: +n })
    .then(() => showAntTuner(n, radio.tuner)).catch((e) => toast(e.message, true));
}
$("tuner").onclick = () => {
  const on = !$("tuner").classList.contains("on");
  api("/api/tuner", { on }).then(() => showAntTuner(radio.ant, on ? "1" : "0")).catch((e) => toast(e.message, true));
};
$("tuner-start").onclick = async () => {
  if (!(await ask("The radio transmits a carrier until the tuner has matched.",
                  { title: "Start an ATU tuning cycle?", yes: "START" }))) return;
  api("/api/tuner", { start: true }).then(() => toast("ATU tuning started")).catch((e) => toast(e.message, true));
};
$("a2b").onclick = () => api("/api/vfo/a-to-b", {});

// ---------------------------------------------------------------- VFO: chain controls
const setWidthUI = (() => {
  const el = $("width"), out = $("width-val");
  let options = [], timer;
  el.addEventListener("input", () => {
    touched.set("width", Date.now());
    const hz = options[+el.value];
    out.textContent = hz ? hz + " Hz" : "—";
    clearTimeout(timer);
    timer = setTimeout(() => { if (hz) api("/api/width", { hz }).catch(() => {}); }, 180);
  });
  return (column, hz) => {
    if (Date.now() - (touched.get("width") || 0) < 1500) return;
    const tbl = column && meta?.widths[column];
    if (!tbl) { el.disabled = true; out.textContent = "—"; return; }
    options = [...new Set(Object.entries(tbl).filter(([i]) => i !== "0").map(([, w]) => w))].sort((a, b) => a - b);
    el.disabled = false;
    el.max = options.length - 1;
    el.value = Math.max(0, options.indexOf(hz));
    out.textContent = hz ? hz + " Hz" : "—";
  };
})();
const setShift = slider("ifshift", (v) => (v > 0 ? "+" : "") + v + " Hz", (v) => api("/api/ifshift", { hz: v }));
// CLAR slider: the offset is set absolutely (the backend clears, moves once and
// reads it back) and RX CLAR goes on with it - turning the knob means wanting
// to hear the offset. The label shows what the radio holds, also beyond the
// slider's +-2.5 kHz.
const fmtClar = (v) => (v > 0 ? "+" : "") + v + " Hz";
const clarLit = (on) => {                 // the CLAR switch at once, ahead of the next poll
  touched.set("clar-rx", Date.now());
  $("clar-rx").classList.toggle("on", on);
};
const setClarOff = slider("clar-off", fmtClar, (v) => { clarLit(true); return api("/api/clar/offset", { hz: v, rx_on: true }); });
const setRf = slider("rfgain", (v) => Math.round(v / 255 * 100) + " %", (v) => api("/api/level", { cmd: "RG0", value: v }));   // the radio counts 0-255; shown in %
const setSqlRaw = slider("squelch", String, (v) => api("/api/level", { cmd: "SQ0", value: v }));
const sqlFill = () => { const el = $("squelch"); el.style.setProperty("--sqlpct", (el.value / 255 * 100) + "%"); };
$("squelch").addEventListener("input", sqlFill);
const setSql = (v) => { setSqlRaw(v); sqlFill(); };
const setPwrBig = slider("pwr-big", (v) => String(v), (v) => api("/api/power", { watts: v }));   // dominant control by RX · FILTER · LEVEL
const setPwr = (v) => { setPwrBig(v); fillPct($("pwr-big")); };   // TX POWER is the one power control now
$("pwr-big").addEventListener("input", (e) => fillPct(e.target));

// DSP switches (checkboxes): the poll must not flip a switch back in the
// moment before the radio has taken it, hence the touched stamp
for (const b of document.querySelectorAll("[data-toggle]")) {
  b.onchange = () => {
    touched.set(b.id, Date.now());
    api("/api/toggle", { cmd: b.dataset.toggle, on: b.checked }).catch(() => touched.delete(b.id));
  };
}
const setDsp = (id, on) => { if (Date.now() - (touched.get(id) || 0) > 1500) $(id).checked = on; };
// Manual notch (BP) and contour (CO): a switch and a slider each. The slider only
// sets the frequency / position; on and off is the switch alone.
const setNotchHz = slider("notch-hz", (v) => v + " Hz", (v) => api("/api/notch", { hz: v }));
const setContourPos = slider("contour-pos", (v) => String(v), (v) => api("/api/contour", { pos: v }));
for (const [id, url] of [["t-notch", "/api/notch"], ["t-contour", "/api/contour"]]) {
  $(id).onchange = (e) => {
    touched.set(id, Date.now());
    api(url, { on: e.target.checked }).catch(() => { touched.delete(id); });
  };
}
$("narrow").onclick = () => api("/api/narrow", { on: !$("narrow").classList.contains("on") });
// LOCK in the title bar: shows the radio's dial lock and switches it (LK0/LK1,
// verified by reading LK back). Lit at once; the poll takes over after 1.5 s.
(function dialLock() {
  const el = $("lamp-lock");
  const flip = () => {
    const on = !el.classList.contains("on");
    touched.set("lamp-lock", Date.now());
    el.classList.toggle("on", on);
    el.setAttribute("aria-pressed", String(on));
    api("/api/toggle", { cmd: "LK", on })
      .then(() => toast(on ? "Dial locked" : "Dial unlocked"))
      .catch(() => touched.delete("lamp-lock"));
  };
  el.onclick = flip;
  el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); flip(); } };
})();

async function runProfile(name, path) {
  const { report } = await api(path, {});
  const changed = report.filter((r) => r.result === "changed");
  const failed = report.filter((r) => r.result === "failed");
  toast(failed.length
    ? `${name} profile: ` + failed.map((r) => `${r.set}: ${r.error}`).join("\n")
    : changed.length
      ? `${name} profile set:\n` + changed.map((r) => `${r.before} → ${r.set}`).join("\n")
      : `${name} profile: everything was already set correctly.`, failed.length > 0);
}
$("voice-profile").onclick = () => runProfile("Voice", "/api/profile/voice").catch(() => {});
$("ft8-profile").onclick = async () => {
  const { report } = await api("/api/profile/ft8", {});
  const changed = report.filter((r) => r.result === "changed");
  const failed = report.filter((r) => r.result === "failed");
  toast(failed.length
    ? "FT8 profile: " + failed.map((r) => `${r.set}: ${r.error}`).join("\n")
    : changed.length
      ? "FT8 profile set:\n" + changed.map((r) => `${r.before} → ${r.set}`).join("\n")
      : "FT8 profile: everything was already set correctly.", failed.length > 0);
};

async function loadMeta() {
  meta = await api("/api/meta");
  seg($("bands"), meta.bands.map((b) => [b, b]), (b) => api("/api/band", { band: b }));
  seg($("modes"), meta.modes.map((m) => [m, m]), (m) => api("/api/mode", { mode: m }));
  seg($("preamp"), Object.entries(meta.preamp), (v) => api("/api/preamp", { level: +v }));
  seg($("att"), Object.entries(meta.att).map(([k, v]) => [k, v === "OFF" ? "0dB" : v]), (v) => api("/api/att", { level: +v }));
  seg($("agc"), meta.agc.map((a) => [a, a]), (a) => api("/api/agc", { name: a }));
}

// Switching the radio on is not the moment it can answer: the Shelly gives the
// FT-950 its mains and the rig then needs seconds to boot, while this page has
// been loaded for a long time. Anything read once over CAT therefore has to be
// read again the moment the link comes up — the memory keys used to keep the
// "—" they were built with, because their one read had happened while the rig
// was still dark, and nothing ever asked a second time.
let catUp = false;          // was the radio answering on the last update?
let powerOnPending = false; // set by the power switch, cleared when CAT answers
function onRadioUp() {
  loadMemories();
  paintRadioPlate();                   // the booting plate has served its turn
  if (powerOnPending) { powerOnPending = false; toast("FT-950 ready"); }
}

// ---------------------------------------------------------------- VFO: live status
function bandOf(hz) {
  for (const [b, [lo, hi]] of Object.entries(BAND_EDGES)) if (hz >= lo && hz <= hi) return b;
  return "GEN";
}

function renderRadio(s) {
  radio = s;
  setStat("stat-cat", s.connected ? true : (s.error ? false : null));
  $("stat-cat").title = s.connected ? "CAT: FT-950 connected" : "CAT: " + (s.error || "disconnected");
  if (!s.connected) { if (catUp) { catUp = false; paintRadioPlate(); } return; }
  if (!catUp) { catUp = true; onRadioUp(); }

  const v = s.vfo;
  if (Date.now() > liveUntil) { liveHz = null; renderFreq(v.freq_hz); }
  if (editHz === null) setSpinner(v.freq_hz);
  showModeLamps(v.mode);
  $("memmode").textContent = v.mem_mode + (v.mem_mode !== "VFO" ? " " + String(v.channel).padStart(3, "0") : "");
  const clarOn = v.rx_clar || v.tx_clar;
  $("ind-clar").classList.toggle("on", clarOn);
  $("clar-lbl").textContent = v.tx_clar && !v.rx_clar ? "TX" : v.tx_clar ? "R/T" : "RX";
  const [ci, cf] = (Math.abs(v.clar_hz) / 1000).toFixed(3).split(".");   // clear decimal point (DSEG's is tiny)
  $("clar-val").innerHTML = `<span class="csign">${v.clar_hz > 0 ? "+" : v.clar_hz < 0 ? "−" : ""}</span>${seg8(ci)}<i class="cdot"></i>${seg8(cf)}`;
  $("tune-mark").style.left = (50 + Math.max(-1000, Math.min(1000, v.clar_hz)) * 0.045) + "%";   // ±1 kHz on 5 … 95 %
  setClarOff(v.clar_hz);
  if (Date.now() - (touched.get("clar-rx") || 0) > 2000) $("clar-rx").classList.toggle("on", !!v.rx_clar);
  segSet($("modes"), v.mode);
  segSet($("bands"), bandOf(v.freq_hz));

  $("lamp-busy").classList.toggle("on", !!s.busy);
  $("lamp-tx").classList.toggle("on", !!s.tx);
  document.body.classList.toggle("transmitting", !!s.tx);     // red frame glow (as TM-V71 Remote)
  $("l-ptt").classList.toggle("on", !!s.tx);
  $("l-ptt").classList.toggle("bad", !!s.tx);
  const m = s.tx ? s.meters : null;
  if (m) meterSet(m.po, `PO ${Math.round(poWatts(m.po))} W`);
  else meterSet(s.smeter, sUnits(s.smeter));
  dsmSet(m ? 0 : s.smeter);                        // receive only: dark while transmitting
  $("ameter").setAttribute("title", m ? `PO raw ${m.po} · SWR raw ${m.swr} · ALC raw ${m.alc} · ID raw ${m.id ?? "—"}` : `S meter ${sUnits(s.smeter)} (raw ${s.smeter})`);
  $("tx-meters").classList.toggle("on", !!m);
  $("tx-read").classList.toggle("on", !!m);
  if (s.tx && !pk.tx) Object.assign(pk, { held: false, po: 0, swr: 0, alc: 0, id: 0, vdd: null });   // new TX: reset
  if (!s.tx && pk.tx) pk.held = peakOn;
  pk.tx = !!s.tx;
  if (m) {
    const id = m.id ?? 0;
    pk.po = Math.max(pk.po, m.po); pk.alc = Math.max(pk.alc, m.alc); pk.id = Math.max(pk.id, id);
    if (m.po >= PK_SWR_MIN_PO) pk.swr = Math.max(pk.swr, m.swr);
    txBar("m-po", m.po, pkv(pk.po));
    txBar("m-alc", m.alc, pkv(pk.alc));
    txBar("m-swr", m.swr, pkv(pk.swr));
    txBar("m-id", id, pkv(pk.id));
    txVals(m.po, m.swr, m.alc, id);
    if (m.vdd != null) pk.vdd = Math.min(pk.vdd ?? 255, m.vdd);   // VDD: the lowest is what counts
    vddShow(m.vdd ?? vddRx);
  } else if (!s.tx && pk.held) {               // receive: the peaks of the last TX
    txBar("m-po", pk.po, pk.po); txBar("m-alc", pk.alc, pk.alc); txBar("m-swr", pk.swr, pk.swr); txBar("m-id", pk.id, pk.id);   // up to the peak (dimmed by CSS)
    txVals(pk.po, pk.swr, pk.alc, pk.id);
  } else if (!s.tx) {
    for (const id of ["m-po", "m-alc", "m-swr", "m-id"]) txBar(id, 0);
  }
  $("tx-meters").classList.toggle("peak", !s.tx && pk.held);   // value box + PEAK HOLD
  $("pk-clr").disabled = !(!s.tx && pk.held);
  $("pk-clr").hidden = !peakOn;               // no peak hold, nothing to clear
  $("tx-meters").classList.toggle("no-peak", !peakOn);
  if (s.vdd_raw != null) vddRx = s.vdd_raw;
  if (!s.tx) { const h = pk.held && pk.vdd != null; vddShow(h ? pk.vdd : vddRx, h); }   // receive: the supply, or the lowest of the last TX

  if (s.other) {                                   // slow tier present
    const [m, k, h] = fmtHzGroups(s.other.freq_hz);
    $("freq-b").innerHTML = `${seg8(m.replace(/^0/, "!"))}<span class="sep">.</span>${seg8(k)}<span class="sep">.</span>${seg8(h)}`;
    $("mode-b").textContent = s.other.mode;
    $("rxtx").textContent = `RX ${s.rx_vfo} · TX ${s.tx_vfo}`;
    $("split").classList.toggle("on", s.rx_vfo !== s.tx_vfo);
    $("ptt-band").textContent = `PTT → VFO ${s.tx_vfo}`;
    $("width-tag").textContent = s.width?.hz ? s.width.hz + " Hz" : "—";
    setWidthUI(s.width?.column, s.width?.hz);
    $("narrow").classList.toggle("on", !!s.narrow);
    $("btn-mon").classList.toggle("on", !!s.monitor);
    showCtcss(s.ctcss, s.ctcss_tone);
    setShift(s.ifshift);
    setRf(s.rf_gain);
    setSql(s.squelch);
    setPwr(s.power);
    segSet($("preamp"), Object.keys(meta?.preamp || {}).find((k2) => meta.preamp[k2] === s.preamp));
    segSet($("att"), Object.keys(meta?.att || {}).find((k2) => meta.att[k2] === s.att));
    segSet($("agc"), (s.agc || "").replace(/^AUTO-.*/, "AUTO"));
    setDsp("t-dnf", !!s.dnf);
    setDsp("t-nr", !!s.nr);
    setDsp("t-nb", !!s.nb && s.nb !== "0");
    setDsp("t-notch", !!s.notch);
    setDsp("t-contour", !!s.contour);
    if (s.notch_hz) setNotchHz(s.notch_hz);
    setContourPos(s.contour_pos);
    if (Date.now() - (touched.get("lamp-lock") || 0) > 1500) {
      $("lamp-lock").classList.toggle("on", !!s.lock);
      $("lamp-lock").setAttribute("aria-pressed", String(!!s.lock));
    }
    // LCD: signal path, indicators, DSP bars
    litCells("pv-ant", [s.ant]);
    showAntTuner(s.ant, s.tuner);
    litCells("pv-att", [(s.att || "").replace("dB", "")]);
    litCells("pv-flt", s.vrf ? ["VRF"] : []);
    showHeadQuick(s);
    litCells("pv-ipo", [{ IPO: "IPO", AMP1: "A1", AMP2: "A2" }[s.preamp]]);
    litCells("pv-rflt", [(s.roofing || "").replace("AUTO-", ""), ...((s.roofing || "").startsWith("AUTO") ? ["A"] : [])]);
    litCells("pv-agc", [{ FAST: "F", MID: "M", SLOW: "S" }[(s.agc || "").replace("AUTO-", "")], ...((s.agc || "").startsWith("AUTO") ? ["A"] : [])]);
    $("ind-moni").classList.toggle("on", !!s.monitor);
    $("ind-nb").classList.toggle("on", !!s.nb && s.nb !== "0");
    $("ind-tuner").classList.toggle("on", !!s.tuner && s.tuner !== "0");
    $("ind-miceq").classList.toggle("on", s.proc === "2");
    $("ind-dnr").classList.toggle("on", !!s.nr);
    $("ind-dnf").classList.toggle("on", !!s.dnf);
    const wN = Math.round(Math.min(3000, s.width?.hz || 0) / 3000 * DSEG);
    const sh = DMID + Math.round((s.ifshift || 0) / 1000 * DMID);
    setBar("bar-width", (i) => Math.abs(i - sh) < wN / 2);
    setBar("bar-shift", (i) => i === sh);
    const np = Math.round(Math.min(3000, s.notch_hz || 0) / 3000 * (DSEG - 1));
    $("dl-notch").classList.toggle("off", !s.notch);       // label dark while the function is off
    $("dl-contour").classList.toggle("off", !s.contour);
    setBar("bar-notch", (i) => !!s.notch && Math.abs(i - np) === 1);   // a dash each side of the notch, both rows
    const cp = Math.round(((s.contour_pos || 1) - 1) / 29 * (DSEG - 1));
    setBar("bar-contour", (i) => !!s.contour && Math.abs(i - cp) > 2, (i) => !!s.contour && Math.abs(i - cp) > 1);   // a V-shaped gap at the position
    highlightMem(v);
    $("lamp-pll").classList.toggle("on", !!s.pll_unlock);
    if (s.power) $("f-pwr").textContent = s.power + " W";
    if (s.power != null) $("tx-pwr").innerHTML = seg8(String(s.power).padStart(3, "!"));
  }
  $("f-dial").textContent = (v.freq_hz / 1000).toFixed(2) + " kHz";
}

// ---------------------------------------------------------------- DIGI: decodes
const decodes = [];
let rxFilter = "all";
const MAX_ROWS = 1500;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtT = (u) => (u || "").slice(0, 8);
const isMine = (d) => !!myCall && (d.to === myCall || d.from === myCall);
const newDx = (d) => (d.worked && d.dxcc && !d.worked.dxcc ? '<span class="newdx" title="DXCC entity never worked">NEW</span>' : "");

const doneCalls = new Set();                  // stations of finished sessions: red from then on
let huntBlocked = new Set();                   // Hunt's no-reply list: struck through
const partnerOf = (d) => ((d.from || "").toUpperCase() === myCall ? d.to : d.from || "").toUpperCase();
function rowClass(d) {
  const c = [];
  if (doneCalls.has(partnerOf(d))) c.push("qso-done");
  else if (isMine(d)) c.push("me");
  else if (d.kind === "CQ") c.push("cq", ...(d.worked?.call_band ? ["worked"] : []));
  if (huntBlocked.has((d.from || "").toUpperCase()) && !isMine(d)) c.push("hunt-blocked");
  if (d.worked?.call && !isMine(d)) c.push("worked-call");
  if (d.kind === "RR73" || d.kind === "73") c.push("end");
  if (d.ap) c.push("ap");
  return c;
}
function passes(d) {
  switch (rxFilter) {
    case "cq": return d.kind === "CQ";
    case "rr73": return d.kind === "RR73" || d.kind === "RRR";
    case "73": return d.kind === "73";
    default: return true;
  }
}
let lastCycleShown = null;
function appendRx(d) {
  if (!passes(d)) return;
  const tr = document.createElement("tr");
  const first = d.period_utc !== lastCycleShown;
  lastCycleShown = d.period_utc;
  tr.className = [...rowClass(d), first ? "cycle-start" : ""].join(" ").trim();
  if (d.from && !isMine(d)) tr.dataset.from = d.from.toUpperCase();    // Hunt marks it later
  tr.innerHTML = `<td>${first ? fmtT(d.period_utc) : ""}</td><td class="n">${d.snr}</td>` +
    `<td class="n">${d.dt >= 0 ? "+" : ""}${d.dt.toFixed(1)}</td><td class="n">${d.df}</td>` +
    `<td class="msg">${esc(d.msg)}</td><td class="dx">${esc(d.dxcc || "")}${newDx(d)}</td>`;
  tr.onclick = () => {
    for (const r of $("rx-body").querySelectorAll(".sel")) r.classList.remove("sel");
    tr.classList.add("sel");
    selectedDecode = d;
    setBox("rx", d.df);
    if ($("af").classList.contains("on")) setBox("tx", d.df);   // adopt frequency of caller
    if (d.from) fillQso(d);
    if (d.from && !isMine(d)) {                                  // DX Call fields, as WSJT-X
      $("dx-call").value = d.from.toUpperCase();
      $("dx-grid").value = (d.grid || "").toUpperCase();
    }
  };
  tr.ondblclick = () => { tr.onclick(); answer(d); };          // iDigi: double-click answers
  $("rx-body").appendChild(tr);
}
function renderRx() {
  $("rx-body").innerHTML = "";
  lastCycleShown = null;
  for (const d of decodes) appendRx(d);
  followRx();
}
function followRx() {
  if (!$("rx-follow").checked) return;
  const w = $("rx-wrap");
  w.scrollTop = w.scrollHeight;
}
function appendMine(d) {
  if (!isMine(d)) return;
  const tr = document.createElement("tr");
  tr.className = doneCalls.has(partnerOf(d)) ? "qso-done" : "me";
  tr.dataset.call = partnerOf(d);
  tr.innerHTML = `<td>${fmtT(d.period_utc)}</td><td class="arrow-rx">◀ RX</td><td class="n">${d.snr}</td>` +
    `<td class="n">${d.dt.toFixed(1)}</td><td class="n">${d.df}</td><td class="msg">${esc(d.msg)}</td>` +
    `<td class="dx">${esc(d.dxcc || "")}</td>`;
  tr.title = d.from ? `Double-click: answer ${d.from}` : "";
  tr.ondblclick = () => {                                      // as in Received Messages
    if (!d.from) return;
    setBox("rx", d.df);
    if ($("af").classList.contains("on")) setBox("tx", d.df);
    fillQso(d);
    answer(d);
  };
  $("my-body").appendChild(tr);
  followMine();
}
// My Messages / Sessions follow (as Received): newest row stays in view
function followMine() {
  if ($("my-follow").checked) { const w = $("my-body").closest(".tbl"); w.scrollTop = w.scrollHeight; }
}
function followSess() {
  if ($("sess-follow").checked) { const w = $("sess-body").closest(".tbl"); w.scrollTop = w.scrollHeight; }   // newest at the bottom
}
function followSessMsg() {                                    // oldest first: newest at the bottom
  if ($("sessmsg-follow").checked) { const w = $("sessmsg-body").closest(".tbl"); w.scrollTop = w.scrollHeight; }
}
$("sessmsg-follow").onchange = followSessMsg;
for (const b of $("rx-filter").children) {
  b.onclick = () => {
    rxFilter = b.dataset.f;
    for (const x of $("rx-filter").children) x.classList.toggle("on", x === b);
    renderRx();
  };
}
$("rx-clear").onclick = () => { decodes.length = 0; renderRx(); };
$("my-clear").onclick = () => { $("my-body").innerHTML = ""; };
$("sess-clear").onclick = async () => {
  try { selSession = null; renderSeq(await api("/api/seq/sessions/clear", {})); } catch { /* toast */ }
};

function onDigi(m) {
  if (m.type === "hello") { applyDigiStatus(m); if (m.seq) renderSeq(m.seq); return; }
  if (m.type === "seq") { renderSeq(m); return; }
  if (m.type !== "cycle") return;
  const c = m.cycle;
  $("dec-n").textContent = c.count;
  $("dec-ms").textContent = c.decode_ms != null ? c.decode_ms + " ms" : "— ms";
  $("dec-ms").title = c.skipped ? "skipped: " + c.skipped : "jt9 run time of the last cycle";
  if (c.depth && digiDepthCfg === "AUTO") showAutoDepth(c.depth);
  $("digi-head-stat").textContent = c.skipped
    ? `${c.utc} · skipped: ${c.skipped}` : `${c.utc} · ${c.mode} · ${c.count} Decodes`;
  for (const d of m.decodes) {
    decodes.push(d);
    appendRx(d);
    appendMine(d);
  }
  if (decodes.length > MAX_ROWS) { decodes.splice(0, decodes.length - MAX_ROWS); renderRx(); }
  else followRx();
}

// the decoder that runs, on the WSJT-X plate in the DIGI title bar ("2.7 fast", "3.3 MT")
function showDecoderTag(tag) {
  const el = $("db-ver");
  if (el && tag && el.textContent !== tag) el.textContent = tag;
}
// Settings ▸ FT8 ▸ PSK Reporter: map links for my call and the counts since the service start
function pskStats(p) {
  const call = encodeURIComponent(myCall || "");
  const map = (dir) => `https://pskreporter.info/pskmap.html?preset&callsign=${call}&txrx=${dir}&timerange=3600`;
  $("psk-map-rx").href = map("rx");
  $("psk-map-tx").href = map("tx");
  if (!p) return;
  const hm = (t) => new Date(t * 1000).toISOString().slice(11, 16) + " UTC";
  const day = (t) => new Date(t * 1000).toISOString().slice(0, 10) + " " + hm(t);
  const set = (id, v) => { if ($(id).textContent !== v) $(id).textContent = v; };
  set("pss-since", p.since ? day(p.since) + (p.enabled ? "" : " · off") : "—");
  set("pss-up", `${p.uploads ?? 0} (${p.datagrams ?? 0} datagrams${p.errors ? `, ${p.errors} failed` : ""})`);
  set("pss-spots", String(p.total ?? 0));
  set("pss-calls", String(p.stations ?? 0));
  set("pss-wait", String(p.pending ?? 0));
  set("pss-last", p.last_t ? `${hm(p.last_t)} · ${p.error ? "failed: " + p.error : (p.last_spots ?? 0) + " spots"}` : "—");
  const b = Object.entries(p.bands || {});
  set("pss-bands", b.length ? b.map(([k, v]) => `${k} ${v}`).join(" · ") : "—");
}

// PSK Reporter lamp in the DIGI strip (after NTP): hidden when off; green after
// a good upload, red when the last one failed, dark until the first
function showPsk(p) {
  pskStats(p);
  const el = $("l-psk");
  el.hidden = !p?.enabled;
  if (!p?.enabled) return;
  const hm = (t) => new Date(t * 1000).toISOString().slice(11, 16);
  el.classList.toggle("online", !!p.last_t && !p.error);
  el.classList.toggle("fault", !!p.error);
  el.title = "PSK Reporter\n" + (p.error ? `Last upload failed: ${p.error}\n` : "") +
    (p.last_t ? `Last upload ${hm(p.last_t)} UTC: ${p.last_spots} stations\n` : "No upload yet (every 5 minutes)\n") +
    `Waiting for the next upload: ${p.pending} · reported since start: ${p.total}`;
}

function applyDigiStatus(s) {
  showDecoderTag(s.decoder);
  digiMode = s.mode || digiMode;
  myCall = (s.config?.my_call || "").toUpperCase();
  showPsk(s.psk);                      // after myCall: the map links use it
  $("my-empty").textContent = myCall ? `No messages to ${myCall} yet.`
    : "No callsign set (Settings ▸ General).";
  segSet($("digi-mode"), s.config?.mode);
  // AUTO shows the mode it found ("AUTO · FT8") - the footer no longer does
  const autoBtn = $("digi-mode").querySelector('[data-v="AUTO"]');
  const autoTxt = s.config?.mode === "AUTO" && s.mode ? `AUTO · ${s.mode}` : "AUTO";
  if (autoBtn && autoBtn.textContent !== autoTxt) autoBtn.textContent = autoTxt;
  $("digi-on").classList.toggle("on", !!s.config?.enabled);
  $("f-grid").textContent = s.config?.my_grid || "—";
  myGrid = s.config?.my_grid || "";
  $("f-mode").textContent = digiMode;
  const cap = s.capture || {};
  setStat("stat-audio", cap.running ? true : (cap.error ? false : null));
  $("stat-audio").title = cap.running ? `USB Audio: ${cap.rms_dbfs} dBFS` : "USB Audio: " + (cap.error || "no capture");
  const rms = cap.rms_dbfs;
  $("lvl-val").textContent = rms != null ? rms.toFixed(1) + " dBFS" : (cap.error || "no audio");
  $("lvl-bar").style.setProperty("--p", rms != null ? Math.max(0, Math.min(100, (rms + 60) / 60 * 100)) + "%" : "0%");
  const hot = cap.peak_dbfs != null && cap.peak_dbfs > -1;
  $("l-ovl").classList.toggle("on", hot);
  $("l-ovl").classList.toggle("bad", hot);
  if (s.config?.depth != null) { digiDepthCfg = String(s.config.depth); segSet($("digi-depth"), digiDepthCfg); }
  if (s.depth && s.depth[digiMode]) showAutoDepth(s.depth[digiMode]);
  if (s.clock_synced !== undefined) {
    // LED as CAT / USB AUDIO: green synced, red not
    $("l-ntp").classList.toggle("online", !!s.clock_synced);
    $("l-ntp").classList.toggle("fault", !s.clock_synced);
  }
  placeBoxes();
}
// ---------------------------------------------------------------- auto-sequencer
let selectedDecode = null, seqState = null, selSession = null, lastTxShown = null;
let lastSessStarted;                          // the running session last seen (new one: select it)
let jumpToCall = null;                        // after a double-click: select that station's session once it shows
const prompted = new Set();
const sessErrSeen = new Set();                // "started|error" already toasted
let sessErrInit = false;
const markedWorked = new Set();               // sessions whose rows were turned "worked"
async function answer(d) {
  if (!d?.from) return;
  jumpToCall = d.from.toUpperCase();
  try { renderSeq(await api("/api/seq/answer", { decode: d, tx_hz: boxes.tx })); toast(`Calling ${d.from} …`); } catch { jumpToCall = null; /* toast */ }
}
// Marked session: QRZ.com lookup and world map, each in its own popup window
const baseCall = (c) => (c || "").toUpperCase().split("/").filter((p) => /\d/.test(p) && /[A-Z]/.test(p))
  .sort((a, b) => b.length - a.length)[0] || (c || "").toUpperCase();
function popup(url, name, w, h) {
  const win = window.open(url, name, `popup,width=${w},height=${h}`);
  if (!win) toast("Popup blocked — allow popups for this page", true);
  else win.focus();
}
function openQrz(call) { popup("https://www.qrz.com/db/" + encodeURIComponent(baseCall(call)), "qrz", 1100, 850); }
function openMap(x) {
  const q = new URLSearchParams({ call: x.call, grid: x.grid || "", my: myGrid });
  popup("map.html?" + q, "dxmap", 960, 640);
}
// Sessions ▸ Auto-log: same setting as Settings ▸ Log ▸ Auto-log
$("sess-autolog").onclick = async () => {
  const on = !$("sess-autolog").classList.contains("on");
  try {
    const c = await api("/api/log/config", { auto_log: on });
    $("sess-autolog").classList.toggle("on", !!c.auto_log);
    if (seqState) seqState.auto_log = !!c.auto_log;
    loadLogStatus();
    toast(c.auto_log ? "Auto-log on — completed QSOs are logged automatically" : "Auto-log off — completed QSOs are offered in the log form");
  } catch { /* toast */ }
};
// Answer = auto-answer switch (manual answer: double-click a decode)
$("seq-answer").onclick = async () => {
  const on = !$("seq-answer").classList.contains("on");
  try {
    renderSeq(await api("/api/seq/auto", { on, tx_hz: boxes.tx, adopt: afOn() }));
    toast(on ? "Auto answer on — stations calling you are answered automatically" : "Auto answer off");
  } catch { /* toast */ }
};
let huntEnd = null;                             // when Hunt switches itself off (ms), null: never / off
function huntLabel(on = seqState?.hunt) {
  let t = "∞";
  if (huntEnd != null) {
    const left = Math.max(0, Math.round((huntEnd - Date.now()) / 1000));
    t = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  }
  const ico = '<svg class="hunt-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">'
    + '<circle cx="12" cy="12" r="7"></circle><circle cx="12" cy="12" r="2.2" fill="currentColor" stroke="none"></circle><path d="M12 2v4M12 18v4M2 12h4M18 12h4"></path></svg>';
  const html = on ? `Hunt<small class="hunt-left">${t}</small>${ico}` : "Auto Hunt" + ico;
  if ($("seq-hunt").innerHTML !== html) $("seq-hunt").innerHTML = html;
}
setInterval(() => { if (seqState?.hunt) huntLabel(true); }, 1000);
// DX Call: call a station typed in by hand (My Messages head). No Enter-to-call:
// a key press in the field must not key the transmitter.
$("dx-go").onclick = async () => {
  const call = $("dx-call").value.trim().toUpperCase();
  if (!/^[A-Z0-9/]{3,12}$/.test(call)) { toast("Enter a DX call", true); $("dx-call").focus(); return; }
  if (call === myCall) { toast(`${call} is your own callsign`, true); $("dx-call").focus(); return; }
  // the grid is optional and only shown: a 6-character one is cut to 4, anything
  // else is left out - it never stops the call
  let grid = $("dx-grid").value.replace(/\s/g, "").toUpperCase().slice(0, 4);
  if (grid && !/^[A-R]{2}[0-9]{2}$/.test(grid)) { toast(`Grid "${grid}" left out (4 characters, e.g. JO62)`); grid = ""; }
  try {
    renderSeq(await api("/api/seq/call", { call, grid, tx_hz: boxes.tx, parity: $("dx-par").value }));
    toast(`Calling ${call} …`);
  } catch { /* toast */ }
};
for (const id of ["dx-call", "dx-grid"]) $(id).addEventListener("input", (e) => { e.target.value = e.target.value.toUpperCase(); });
$("dx-grid").maxLength = 6;                         // a full locator may be pasted; it is cut to 4
// directed CQ: the choice is kept per browser; changed while calling CQ, it takes
// effect with the next CQ (restarts the CQ sequence)
$("cq-to").value = store.get("cqTo", "");
$("cq-to").onchange = async () => {
  store.set("cqTo", $("cq-to").value);
  if (seqState?.kind === "cq" && !seqState?.session) {
    try { renderSeq(await api("/api/seq/cq", { tx_hz: boxes.tx, cq_to: $("cq-to").value })); } catch { /* toast */ }
  }
};
// Hunt = auto-clicker for new stations' CQs (settings: Settings ▸ FT8 ▸ Hunt)
$("seq-hunt").onclick = async () => {
  const on = !$("seq-hunt").classList.contains("on");
  try {
    const s = await api("/api/seq/hunt", { on, tx_hz: boxes.tx, adopt: afOn() });
    renderSeq(s);
    toast(!on ? "Auto Hunt off" : s.hunt_left_s == null ? "Auto Hunt on (no switch-off) — new stations' CQs are called automatically"
      : `Auto Hunt on for ${Math.ceil(s.hunt_left_s / 60)} min — new stations' CQs are called automatically`);
  } catch { /* toast */ }
};
$("seq-cq").onclick = async () => {
  try {
    renderSeq(seqState?.kind === "cq" ? await api("/api/seq/halt", {})
      : await api("/api/seq/cq", { tx_hz: boxes.tx, cq_to: $("cq-to").value }));
  } catch { /* toast */ }
};
const STATE_CLS = { "done": "sess-ok", "running": "sess-run" };
function renderSeq(s) {
  seqState = s;
  const follow = s.kind && s.kind !== "cq" && s.tx_hz ? s.tx_hz : null;   // a QSO on its own frequency
  if (follow !== seqTxHz) { seqTxHz = follow; placeBoxes(); }
  $("seq-cq").classList.toggle("on", s.kind === "cq");
  $("cq-to").classList.toggle("on", s.kind === "cq");          // lit with CQ: what is being called
  $("seq-answer").classList.toggle("on", !!s.auto_answer);
  $("seq-hunt").classList.toggle("on", !!s.hunt);
  // off: "Auto Hunt"; on: "Hunt" and the time left as m:ss, counted down here
  // every second (∞: no switch-off)
  huntEnd = s.hunt && s.hunt_left_s != null ? Date.now() + s.hunt_left_s * 1000 : null;
  huntLabel(s.hunt);
  const hb = new Set(s.hunt_blocked || []);
  if (hb.size !== huntBlocked.size || [...hb].some((c) => !huntBlocked.has(c))) {
    huntBlocked = hb;
    for (const tr of document.querySelectorAll("#rx-body tr[data-from]"))
      tr.classList.toggle("hunt-blocked", huntBlocked.has(tr.dataset.from));
  }
  showHalt();
  $("sess-autolog").classList.toggle("on", !!s.auto_log);
  const chip = $("seq-tx");
  chip.textContent = s.kind ? `TX ▸ ${s.next || "—"}` : "TX —";
  chip.title = s.kind ? `${s.kind === "cq" ? "CQ" : "Reply"} · ${s.parity} · ${s.tx_hz} Hz · try ${s.tries}` : "Sequencer off";
  chip.classList.toggle("live", !!s.kind);
  // My Messages: own transmissions
  if (s.last_tx && JSON.stringify(s.last_tx) !== lastTxShown) {
    lastTxShown = JSON.stringify(s.last_tx);
    const tr = document.createElement("tr");
    tr.className = "tx-row";
    tr.innerHTML = `<td>${esc(s.last_tx.utc)}</td><td class="arrow-tx">TX ▶</td><td class="n"></td><td class="n">${s.last_tx.start_offset ?? ""}</td>` +
      `<td class="n">${Math.round(s.last_tx.df)}</td><td class="msg">${esc(s.last_tx.msg)}</td><td></td>`;
    $("my-body").appendChild(tr);
    followMine();
  }
  // Sessions
  const fmtDur = (x) => `${String(Math.floor(x / 60)).padStart(2, "0")}:${String(x % 60).padStart(2, "0")}`;
  // oldest first, newest at the bottom (as the other lists); data-i stays the index in s.sessions
  // "error: <reason>" shows as just "error"; the reason comes as a toast (once when it
  // appears, and again on a click on the row) and as the cell's tooltip
  const errOf = (x) => (x.logged !== true && x.logged !== "running" && x.state.startsWith("error") ? x.state : null);
  for (const x of s.sessions) {
    const e = errOf(x), key = `${x.started}|${e}`;
    if (e && !sessErrSeen.has(key)) {
      if (sessErrInit) toast(`${x.call}: ${e}`, true);   // not for old errors on page load
      sessErrSeen.add(key);
    }
  }
  sessErrInit = true;
  $("sess-body").innerHTML = s.sessions.map((x, i) => [x, i]).reverse().map(([x, i]) => {
    const err = errOf(x);
    const st = x.logged === true ? "logged" : x.logged === "running" ? "logging …" : err ? "error" : x.state;
    const cls = x.logged === true ? "sess-ok" : (STATE_CLS[x.state] || (x.state.startsWith("error") || x.state === "no reply" ? "sess-bad" : ""));
    return `<tr data-i="${i}"${selSession === x.started ? ' class="sel"' : ""}><td class="msg">${esc(x.call)}</td>` +
      `<td>${new Date(x.started * 1000).toISOString().slice(11, 19)}</td><td>${fmtDur(x.duration_s)}</td>` +
      `<td class="n">${x.df ?? ""}</td><td>${esc(x.band || "")}</td><td class="n">${x.tries}</td>` +
      `<td class="${cls}"${err ? ` title="${esc(err)}"` : ""}>${esc(st)}</td><td class="dx">${esc(x.dxcc || "")}</td></tr>`;
  }).join("") +
  // CQ callers waiting for their turn (answered after the running QSO, front first);
  // a click puts one at the front
  (s.queue || []).map((q, k) => {
    const wait = Math.max(0, Math.round(Date.now() / 1000 - q.first));
    return `<tr class="sess-wait" data-q="${esc(q.call)}" title="${esc(q.msg || "")} · ${q.snr ?? ""} dB · heard ${q.age_s} s ago — click: answer next">` +
      `<td class="msg">${esc(q.call)}</td><td>${new Date(q.first * 1000).toISOString().slice(11, 19)}</td><td>${fmtDur(wait)}</td>` +
      `<td class="n">${q.df ?? ""}</td><td></td><td class="n"></td>` +
      `<td class="sess-run">${k === 0 ? "next" : `waiting ${k + 1}`}</td><td class="dx">${esc(q.dxcc || "")}</td></tr>`;
  }).join("");
  followSess();
  for (const tr of $("sess-body").querySelectorAll("tr.sess-wait")) tr.onclick = async () => {
    try { renderSeq(await api("/api/seq/queue/front", { call: tr.dataset.q })); toast(`${tr.dataset.q} is next`); } catch { /* toast */ }
  };
  for (const tr of $("sess-body").querySelectorAll("tr[data-i]")) tr.onclick = () => {
    const x = s.sessions[+tr.dataset.i], e = errOf(x);
    selSession = x.started; renderSeq(seqState);
    if (e) toast(`${x.call}: ${e}`, true);
  };
  // a new session started by the sequencer itself (Auto Hunt, Auto Answer, a caller
  // to our CQ): select it, so Sessions and Session Messages follow the running QSO
  if (s.session && s.session.started !== lastSessStarted) {
    const first = lastSessStarted === undefined;          // page load: keep what is there
    lastSessStarted = s.session.started;
    const i = s.sessions.findIndex((x) => x.started === s.session.started);
    if (!first && i >= 0 && s.session.state === "running") {
      selSession = s.session.started;
      for (const tr of $("sess-body").children) tr.classList.toggle("sel", +tr.dataset.i === i);
      $("sess-body").querySelector(`tr[data-i="${i}"]`)?.scrollIntoView({ block: "nearest" });
    }
  }
  // a double-clicked station: select the session the sequencer now runs for it,
  // scroll it into view. Wait for that one: the first update after the click
  // only shows the old sequence halted (older, aborted sessions of the same
  // call must not be taken).
  if (jumpToCall && s.session && (s.session.call || "").toUpperCase() === jumpToCall) {
    const i = s.sessions.findIndex((x) => x.started === s.session.started);
    if (i >= 0) {
      jumpToCall = null;
      selSession = s.sessions[i].started;
      for (const tr of $("sess-body").children) tr.classList.toggle("sel", +tr.dataset.i === i);
      $("sess-body").querySelector(`tr[data-i="${i}"]`)?.scrollIntoView({ block: "nearest" });
    }
  }
  const marked = s.sessions.find((x) => x.started === selSession) || null;
  $("sess-qrz").disabled = $("sess-map").disabled = !marked;
  $("sess-qrz").onclick = () => marked && openQrz(marked.call);
  $("sess-map").onclick = () => marked && openMap(marked);
  const cur = s.sessions.find((x) => x.started === selSession) || s.session || s.sessions[0];
  $("sessmsg-call").textContent = cur ? cur.call : "";
  // TX lines appear when the message is decided: "pending" until PTT, "skipped"
  // if it never went out (too late for the period, halted)
  const txCls = (m) => `tx-row${m.pending ? " tx-pending" : m.skipped ? " tx-skipped" : ""}`;
  const txTip = (m) => m.pending ? ' title="Next message: goes out at the start of the period"'
    : m.skipped ? ' title="Not sent (too late for this period, or halted)"' : "";
  $("sessmsg-body").innerHTML = (cur?.msgs || []).map((m) => `<tr class="${m.dir === "tx" ? txCls(m) : m.other ? "sess-other" : ""}"${m.other ? ' title="From the station, not to you"' : m.dir === "tx" ? txTip(m) : ""}><td>${esc((m.utc || "").slice(0, 8))}</td>` +
    `<td class="${m.dir === "tx" ? "arrow-tx" : "arrow-rx"}">${m.dir === "tx" ? (m.pending ? "TX ▷" : m.skipped ? "TX ✕" : "TX ▶") : "◀ RX"}</td><td class="n">${m.snr ?? ""}</td>` +
    `<td class="n">${m.dt != null ? m.dt.toFixed(1) : ""}</td><td class="n">${Math.round(m.df ?? 0)}</td><td class="msg">${esc(m.msg)}</td></tr>`).join("");
  followSessMsg();
  // finished QSOs: every row of that station turns red now — Received and
  // My Messages, also the green ones to/from me (new rows follow doneCalls)
  let recolour = false;
  for (const x of s.sessions) {
    if (x.state !== "done" || markedWorked.has(x.started)) continue;
    markedWorked.add(x.started);
    doneCalls.add(x.call);
    recolour = true;
    for (const d of decodes) {
      if ((d.from || "").toUpperCase() === x.call)
        d.worked = { ...(d.worked || {}), call: true, call_band: true };
    }
    for (const tr of $("my-body").querySelectorAll(`tr[data-call="${CSS.escape(x.call)}"]`)) {
      tr.classList.remove("me"); tr.classList.add("qso-done");
    }
  }
  if (recolour) renderRx();
  // finished but not auto-logged: offer it in the log form (once)
  for (const x of s.sessions) {
    if (x.logged === false && x.state === "done" && !prompted.has(x.started)) {
      prompted.add(x.started);
      $("q-call").value = x.call; $("q-grid").value = x.grid || ""; logReady();
      $("q-sent").value = x.report_sent || x.my_report || ""; $("q-rcvd").value = x.their_report || "";
      toast(`QSO with ${x.call} complete — details are in the log form, press LOG QSO.`);
    }
    if (x.logged === true && !prompted.has("log" + x.started)) {
      prompted.add("log" + x.started);
      toast(`QSO with ${x.call} logged automatically.`);
      loadRecent(); loadLogStatus();
    }
  }
}

// TUNE / Halt / AF / TX level
let txState = { active: null };
// Halt TRX (DIGI title bar): fixed place, dimmed — active only for FT8/FT4
// (an FT transmission or a running sequence)
function showHalt() {
  $("halt").disabled = !(txState?.active === "ft" || seqState?.kind || seqState?.hunt);   // Hunt too: Halt stops it
}
function renderTx(s) {
  txState = s;
  showHalt();
  const on = s.active === "tune";
  $("tune").classList.toggle("on", on);
  $("tune").textContent = on ? `TUNE ${Math.ceil(s.remaining_s ?? 0)}s` : "TUNE";
  if (s.level != null && Date.now() - (touched.get("tx-level") || 0) > 1500) {
    for (const id of ["tx-level", "dg-tx-level"]) {      // Settings ▸ Audio and the DIGI strip
      $(id).value = s.level;
      $(id + "-val").textContent = s.level + "%";
    }
  }
}
async function pollTx() { try { renderTx(await api("/api/tx")); } catch { /* shown by toast */ } }
let txTimer = null;
function watchTx() {
  clearInterval(txTimer);
  txTimer = setInterval(async () => { await pollTx(); if (!txState.active) clearInterval(txTimer); }, 500);
}
$("tune").onclick = async () => {
  const on = txState.active !== "tune";
  try { renderTx(await api("/api/tx/tune", { on, hz: boxes.tx })); if (on) watchTx(); } catch { /* toast */ }
};
$("halt").onclick = async () => {
  const hunting = !!seqState?.hunt;
  try {
    renderTx(await api("/api/tx/halt", {}));
    renderSeq(await api("/api/seq"));                 // Hunt off shows at once
    if (hunting) toast("Halted — Auto Hunt switched off");
  } catch { /* toast */ }
};
$("af").classList.toggle("on", store.get("af", true));
// AF (adopt frequency): clicking a station moves the TX window to it, and Auto
// Answer / Auto Hunt (in the backend) answer on the partner's frequency
const afOn = () => $("af").classList.contains("on");
$("af").onclick = () => {
  const on = !afOn();
  $("af").classList.toggle("on", on);
  store.set("af", on);
  if (seqState?.auto_answer) api("/api/seq/auto", { on: true, adopt: on }).then(renderSeq).catch(() => {});
  if (seqState?.hunt) api("/api/seq/hunt", { on: true, adopt: on }).then(renderSeq).catch(() => {});
};
(function txLevel() {
  // two sliders, one value: FT8 TX DRIVE in Settings ▸ Audio and the DIGI strip
  const ids = ["tx-level", "dg-tx-level"]; let timer;
  for (const id of ids) {
    const el = $(id);
    el.addEventListener("input", () => {
      touched.set("tx-level", Date.now());
      for (const o of ids) { $(o).value = el.value; $(o + "-val").textContent = el.value + "%"; }
      clearTimeout(timer);
      timer = setTimeout(() => api("/api/tx/level", { percent: +el.value }).catch(() => {}), 200);
    });
  }
})();

seg($("digi-mode"), [["AUTO", "AUTO"], ["FT8", "FT8"], ["FT4", "FT4"]],
  (mode) => api("/api/digi/config", { mode }).then(pollDigi));
// Decode depth: fixed 1/2/3 or AUTO; the AUTO button shows the depth it chose.
seg($("digi-depth"), [["1", "D1"], ["2", "D2"], ["3", "D3"], ["AUTO", "AUTO"]],
  (v) => api("/api/digi/config", { depth: v === "AUTO" ? "AUTO" : +v }).then(pollDigi));
function showAutoDepth(d) {
  const b = $("digi-depth").querySelector('[data-v="AUTO"]');
  if (b) b.textContent = "AUTO d" + d;
}
$("digi-on").onclick = () =>
  api("/api/digi/config", { enabled: !$("digi-on").classList.contains("on") }).then(pollDigi);
async function pollDigi() {
  try { applyDigiStatus(await api("/api/digi")); } catch { /* link lamps show it */ }
}

// ---------------------------------------------------------------- clock, cycle bar
function tick() {
  if (uiCovered) { setTimeout(() => requestAnimationFrame(tick), 250); return; }   // behind Settings: 4/s, no drawing
  const now = Date.now() / 1000;
  $("clock").textContent = new Date().toISOString().slice(11, 19) + " UTC";
  const p = PERIOD[digiMode] || 15;
  const n = Math.floor(now / p);
  $("cycle-bar").style.width = ((now - n * p) / p * 100).toFixed(1) + "%";
  const even = n % 2 === 0;
  $("parity").textContent = even ? "EVEN" : "ODD";
  $("parity").classList.toggle("odd", !even);
  // RX / TX and the time into the period, e.g. "RX 7.3 / 15 s"
  const into = now - n * p, txt = `${radio.tx ? "TX" : "RX"} ${into.toFixed(1)} / ${p} s`;
  if ($("cycle-txt").textContent !== txt)            // RX green, TX red
    $("cycle-txt").innerHTML = `<b class="${radio.tx ? "lbl-tx" : "lbl-rx"}">${radio.tx ? "TX" : "RX"}</b>${txt.slice(2)}`;
  requestAnimationFrame(tick);
}

// ---------------------------------------------------------------- waterfall
const F0 = 100, F1 = 3000;
const wfCanvas = $("wf"), wfCtx = wfCanvas.getContext("2d");
let wfFloor = null, lastFramePeriod = null, hzPerBin = 48000 / 16384;
const CMAPS = {
  Purple: [[0, 6, 4, 58], [0.35, 40, 10, 120], [0.6, 150, 40, 190], [0.8, 235, 90, 230], [1, 255, 235, 255]],
  Rainbow: [[0, 0, 0, 80], [0.25, 0, 90, 255], [0.45, 0, 220, 200], [0.62, 90, 240, 40], [0.8, 255, 210, 0], [1, 255, 40, 20]],
  Blue: [[0, 2, 8, 24], [0.4, 10, 60, 140], [0.7, 60, 170, 230], [1, 230, 250, 255]],
};
let lut = null;
function buildLut(name) {
  const stops = CMAPS[name] || CMAPS.Purple;
  lut = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const x = i / 255;
    let k = 0;
    while (k < stops.length - 2 && x > stops[k + 1][0]) k++;
    const [x0, ...a] = stops[k], [x1, ...b] = stops[k + 1];
    const t = (x - x0) / (x1 - x0 || 1);
    for (let c = 0; c < 3; c++) lut[i * 3 + c] = a[c] + (b[c] - a[c]) * t;
  }
}
// The waterfall starts with the default palette (Settings ▸ FT8); the Palette choice
// under the waterfall changes it only for this page view.
// In the settings: the default palette as three colour samples; a click on a sample
// picks it, the chosen one is framed.
let cmapDefault = store.get("cmapDefault", store.get("cmap", "Rainbow"));
if (!CMAPS[cmapDefault]) cmapDefault = "Rainbow";
$("set-cmap-sw").innerHTML = Object.entries(CMAPS).map(([name, stops]) =>
  `<button type="button" class="cmap-opt" data-cmap="${name}" role="radio" title="${name}"><i class="cmap-chip" style="background:linear-gradient(90deg,${
    stops.map(([x, r, g, b]) => `rgb(${r},${g},${b}) ${x * 100}%`).join(",")})"></i>${name}</button>`).join("");
const showCmapDefault = () => $("set-cmap-sw").querySelectorAll(".cmap-opt").forEach((b) => {
  b.classList.toggle("on", b.dataset.cmap === cmapDefault);
  b.setAttribute("aria-checked", b.dataset.cmap === cmapDefault);
});
showCmapDefault();
$("set-cmap-sw").onclick = (e) => {
  const b = e.target.closest(".cmap-opt");
  if (!b || b.dataset.cmap === cmapDefault) return;
  cmapDefault = b.dataset.cmap;
  store.set("cmapDefault", cmapDefault);
  showCmapDefault();
  $("wf-cmap").value = cmapDefault;
  buildLut(cmapDefault);
};
$("wf-cmap").value = cmapDefault;
buildLut(cmapDefault);
$("wf-cmap").onchange = (e) => buildLut(e.target.value);
for (const id of ["wf-contrast", "wf-bright"]) {
  $(id).value = store.get(id, $(id).value);
  $(id + "-val").textContent = $(id).value;
  $(id).oninput = (e) => { store.set(id, +e.target.value); $(id + "-val").textContent = e.target.value; };
}
function sizeWf() {
  const b0 = Math.floor(F0 / hzPerBin), b1 = Math.ceil(F1 / hzPerBin);
  if (wfCanvas.width !== b1 - b0) {
    wfCanvas.width = b1 - b0;
    wfCanvas.height = 240;
    wfCtx.fillStyle = "#06043a";
    wfCtx.fillRect(0, 0, wfCanvas.width, 240);
  }
  return [b0, b1];
}
function onFrame(f) {
  if ($("digi").classList.contains("collapsed")) return;
  if (uiCovered) {                                      // behind Settings: keep the rows, draw on close
    wfBacklog.push(f);
    if (wfBacklog.length > 240) wfBacklog.shift();      // more than the canvas holds is never seen
    return;
  }
  hzPerBin = f.hz_per_bin;
  const [b0, b1] = sizeWf();
  const db = f.db.slice(b0, b1);
  const sorted = [...db].sort((a, b) => a - b);
  const med = sorted[Math.floor(sorted.length * 0.4)];
  wfFloor = wfFloor === null ? med : wfFloor * 0.95 + med * 0.05;
  const range = 70 - (+$("wf-contrast").value / 100) * 55;   // 70 … 15 dB span
  const off = (+$("wf-bright").value - 50) / 50 * 15;         // ±15 dB
  const w = wfCanvas.width;
  wfCtx.drawImage(wfCanvas, 0, 0, w, 239, 0, 1, w, 239);      // scroll down 1 px
  const row = wfCtx.createImageData(w, 1);
  const per = Math.floor(f.t / (PERIOD[digiMode] || 15));
  const boundary = lastFramePeriod !== null && per !== lastFramePeriod;
  lastFramePeriod = per;
  for (let i = 0; i < w; i++) {
    const v = boundary ? 0 : Math.max(0, Math.min(255, Math.round(((db[i] - wfFloor + 4 + off) / range) * 255)));
    row.data[i * 4] = lut[v * 3];
    row.data[i * 4 + 1] = lut[v * 3 + 1];
    row.data[i * 4 + 2] = lut[v * 3 + 2];
    row.data[i * 4 + 3] = 255;
  }
  wfCtx.putImageData(row, 0, 0);
}
function buildAxis() {
  const ax = $("wf-axis");
  const w = ax.clientWidth || 1000;
  const step = w > 900 ? 100 : w > 500 ? 250 : 500;
  const margin = (F1 - F0) * 0.02;
  ax.innerHTML = "";
  for (let f = Math.ceil(F0 / step) * step; f <= F1; f += step) {
    if (f - F0 < margin || F1 - f < margin) continue;
    const s = document.createElement("span");
    s.style.left = ((f - F0) / (F1 - F0) * 100) + "%";
    s.textContent = f;
    ax.appendChild(s);
  }
}
// TX audio range the FT-950 really transmits (menu 064 TX BPF 100–2900 Hz;
// same limits as the backend's clamp_tx_hz)
const TX_MIN_HZ = 200, TX_MAX_HZ = 2700;
const boxes = { tx: Math.max(TX_MIN_HZ, Math.min(TX_MAX_HZ, store.get("txHz", 1500))), rx: store.get("rxHz", 1500) };
function setBox(which, hz) {
  const want = Math.round(Math.max(F0, Math.min(F1 - 100, hz)));
  boxes[which] = which === "tx" ? Math.max(TX_MIN_HZ, Math.min(TX_MAX_HZ, want)) : want;
  if (which === "tx" && boxes.tx !== want)
    toast(`TX kept at ${boxes.tx} Hz — the radio's TX filter ends at 2900 Hz (menu 064)`);
  store.set(which + "Hz", boxes[which]);
  placeBoxes();
  if (which === "tx" && seqState?.auto_answer) {            // auto answer replies on the TX box
    clearTimeout(setBox.t);
    setBox.t = setTimeout(() => api("/api/seq/auto", { on: true, tx_hz: boxes.tx, adopt: afOn() }).catch(() => {}), 400);
  }
}
// While a QSO runs, the TX box shows the frequency the sequencer really uses (AF:
// the partner's, set by Auto Hunt / Auto Answer in the backend); afterwards it goes
// back to the frequency you set. Your setting itself is not changed.
let seqTxHz = null;
function placeBoxes() {
  const bw = SIGNAL_BW[digiMode] || 50;
  const at = { tx: seqTxHz ?? boxes.tx, rx: boxes.rx };
  for (const which of ["tx", "rx"]) {
    const el = $("box-" + which);
    el.style.display = which === "rx" && !rxBoxOn ? "none" : "block";
    el.style.left = ((at[which] - F0) / (F1 - F0) * 100) + "%";
    el.style.width = (bw / (F1 - F0) * 100) + "%";
  }
  $("f-tx").textContent = Math.round(at.tx);
}
const hzAt = (e) => {
  const r = $("wf-wrap").getBoundingClientRect();
  return F0 + (e.clientX - r.left) / r.width * (F1 - F0);
};
// a TX frequency picked by hand switches AF off, so the next click on a station
// (or Auto Answer / Auto Hunt) does not move it away again
$("wf-wrap").addEventListener("click", (e) => {
  if (e.shiftKey) { setBox("rx", hzAt(e)); return; }   // Shift+click: RX window only
  if (afOn()) { $("af").onclick(); toast("Adopt Frequency off: TX frequency set by hand"); }
  setBox("tx", hzAt(e));
});
$("set-rx-box").checked = rxBoxOn;
$("set-rx-box").onchange = (e) => { rxBoxOn = e.target.checked; store.set("rxBox", rxBoxOn); placeBoxes(); };
$("wf-wrap").addEventListener("mousemove", (e) => {
  const tip = $("wf-tip"), r = $("wf-wrap").getBoundingClientRect();
  tip.hidden = false;
  tip.style.left = ((e.clientX - r.left) / r.width * 100) + "%";   // % — the console is zoomed (90 %)
  tip.textContent = Math.round(hzAt(e)) + " Hz";
});
$("wf-wrap").addEventListener("mouseleave", () => { $("wf-tip").hidden = true; });
window.addEventListener("resize", () => { buildAxis(); drawSysGraph(); });

// ---------------------------------------------------------------- settings dialog
const modal = $("settings");
let activeTab = "general";
let paneTimer = null;

function openSettings() {
  modal.classList.add("open");
  showTab(activeTab);
}
function closeSettings() {
  modal.classList.remove("open");
  applyColors(digiColors);              // unsaved colour preview is dropped
  revertAppearance();                   // and so are unsaved panel / accent changes
  clearInterval(paneTimer);
  paneTimer = null;
}
$("open-settings").onclick = openSettings;
$("set-close").onclick = closeSettings;
$("set-cancel").onclick = closeSettings;
for (const b of document.querySelectorAll("[data-close]")) b.onclick = closeSettings;
modal.addEventListener("click", (e) => { if (e.target === modal) closeSettings(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && modal.classList.contains("open")) closeSettings(); });
for (const t of document.querySelectorAll(".tab")) t.onclick = () => showTab(t.dataset.tab);

function showTab(name) {
  activeTab = name;
  for (const t of document.querySelectorAll(".tab")) t.classList.toggle("active", t.dataset.tab === name);
  for (const p of document.querySelectorAll(".tab-pane")) p.classList.toggle("active", p.dataset.pane === name);
  clearInterval(paneTimer);
  paneTimer = null;
  if (name === "general") loadGeneral();
  if (name === "ft8") loadFt8();
  if (name === "log") loadLogSettings();
  if (name === "memories") loadMemEditor();
  if (name === "appearance") renderAppearance();
  if (name === "audio") { loadAudio(true); loadVoice(); loadMenus(); paneTimer = setInterval(() => loadAudio(false), 1000); }
  if (name === "hardware") {
    loadSystem();
    loadSysHistory();
    let n = 0;
    paneTimer = setInterval(() => { loadSystem(); if (++n % 12 === 0) loadSysHistory(); }, 5000);
  }
}

// -- general
async function loadGeneral() {
  const s = await api("/api/settings").catch(() => null);
  if (!s) return;
  $("app-ver").textContent = "v" + s.version;
  $("set-call").value = s.my_call || "";
  $("set-grid").value = s.my_grid || "";
  await loadPorts(s.serial_port, s.serial_baud, s.serial_bauds);
  $("set-hackrf").value = s.hackrf_url || "";
  $("set-shelly-host").value = s.shelly_host || "";
  $("set-shelly-ch").value = String(s.shelly_channel ?? 0);
  $("set-shelly-user").value = s.shelly_user || "";
  $("set-shelly-pass").value = "";
  $("set-autooff").value = s.auto_power_off ? "1" : "0";
  $("set-autooff-min").value = Math.round((s.auto_power_off_s || 300) / 60);
  $("set-shelly-pass").placeholder = s.shelly_password_set ? "stored — leave empty to keep" : "";
  $("cert-field").hidden = !s.ca_available;
  if (s.cert) {
    $("cert-meta").textContent = `${s.cert.subject} · issued by ${s.cert.issuer} · valid until ${s.cert.not_after}` +
      (s.cert.names?.length ? ` · ${s.cert.names.join(", ")}` : "");
  }
  applyCallsign(s.my_call);
}
async function loadPorts(port, baud, bauds) {
  let d = { ports: [] };
  try { d = await api("/api/serial/ports"); } catch { /* keep current */ }
  const sel = $("set-port");
  sel.innerHTML = "";
  const list = d.ports.some((p) => p.device === port) ? d.ports : [{ device: port, description: "(current, not found)" }, ...d.ports];
  for (const p of list) {
    const o = document.createElement("option");
    o.value = p.device;
    o.textContent = `${p.description || ""} — ${p.device.replace("/dev/serial/by-id/", "by-id/")}`;
    sel.appendChild(o);
  }
  sel.value = port;
  $("set-baud").innerHTML = (bauds || d.bauds || [4800, 9600, 19200, 38400])
    .map((b) => `<option value="${b}">${b} Bd${b === 38400 ? " (menu 026 = 3)" : ""}</option>`).join("");
  $("set-baud").value = String(baud);
}
function applyCallsign(call) { $("callsign").textContent = (call || "").toUpperCase().replace(/0/g, "Ø"); }   // display only: slashed zero
$("set-save").onclick = async () => {
  const body = {
    my_call: $("set-call").value.trim(),
    my_grid: $("set-grid").value.trim(),
    serial_port: $("set-port").value || undefined,
    serial_baud: +$("set-baud").value || undefined,
    hackrf_url: $("set-hackrf").value.trim(),
    shelly_host: $("set-shelly-host").value.trim(),
    shelly_channel: +$("set-shelly-ch").value,
    shelly_user: $("set-shelly-user").value.trim(),
    shelly_password: $("set-shelly-pass").value,
    auto_power_off: $("set-autooff").value === "1",
    auto_power_off_s: Math.max(60, Math.min(7200, Math.round(+$("set-autooff-min").value || 5) * 60)),
  };
  try {
    const s = await api("/api/settings", body);
    applyCallsign(s.my_call);
    pollDigi();
    hrfPollStatus();
    refreshPower();
    toast("Saved.");
  } catch { /* toast shown */ }
};

// -- FT8: decoder, closing message, list colours
// Colours are CSS variables; a saved colour overrides both themes, "" = theme default.
const COLOR_DEFS = [
  { key: "cq", name: "CQ", v: "--row-cq", ink: "--row-cq-ink", sample: "CQ EA5KEP IM99" },
  { key: "cq_worked", name: "CQ — worked on this band", v: "--row-cq-worked", ink: "--row-cq-worked-ink", sample: "CQ R1CCX KP40" },
  { key: "me", name: "To / from me", v: "--row-me", ink: "--row-me-ink", sample: "DJ0SH GM3RTG -04" },
  { key: "tx", name: "My transmissions (text)", v: "--row-tx", def: "--tx", text: true, sample: "GM3RTG DJ0SH RR73" },
  { key: "newdx", name: "NEW (DXCC never worked)", v: "--newdx", ink: "--newdx-ink", def: "--amber", sample: "NEW" },
];
let digiColors = {}, colorDraft = {};
const inkFor = (hex) => {                       // readable text on that background
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.25 ? "#08110c" : "#ffffff";
};
function applyColors(cols) {
  const st = document.documentElement.style;
  for (const d of COLOR_DEFS) {
    const c = cols?.[d.key];
    if (c) { st.setProperty(d.v, c); if (d.ink) st.setProperty(d.ink, inkFor(c)); }
    else { st.removeProperty(d.v); if (d.ink) st.removeProperty(d.ink); }
  }
}
function colorNow(d) {                          // the colour in effect, as #rrggbb
  const cs = getComputedStyle(document.documentElement);
  const raw = (cs.getPropertyValue(d.v).trim() || cs.getPropertyValue(d.def || d.v).trim());
  const cv = document.createElement("canvas").getContext("2d");
  cv.fillStyle = "#000"; cv.fillStyle = raw || "#000";
  return cv.fillStyle.startsWith("#") ? cv.fillStyle : "#000000";
}
function renderColorGrid() {
  const grid = $("color-grid");
  grid.innerHTML = "";
  for (const d of COLOR_DEFS) {
    const row = document.createElement("div");
    row.className = "color-row";
    const custom = colorDraft[d.key] ?? digiColors[d.key] ?? "";
    row.innerHTML = `<input type="color" value="${colorNow(d)}" aria-label="${esc(d.name)}">` +
      `<span class="cname">${esc(d.name)}${custom ? "" : ' <small class="dim">(theme)</small>'}</span>` +
      `<span class="cprev" style="${d.text ? `color:var(${d.v}, var(${d.def}))` : `background:var(${d.v}${d.def ? `, var(${d.def})` : ""}); color:var(${d.ink}, ${d.key === "newdx" ? "#1a1000" : "inherit"})`}">${esc(d.sample)}</span>` +
      `<button type="button" class="mini-btn" title="Back to the theme colour">Reset</button>`;
    row.querySelector("input").oninput = (e) => {
      colorDraft[d.key] = e.target.value;
      applyColors({ ...digiColors, ...colorDraft });
      row.querySelector(".cname small")?.remove();
    };
    row.querySelector("button").onclick = () => {
      colorDraft[d.key] = "";
      applyColors({ ...digiColors, ...colorDraft });
      renderColorGrid();
    };
    grid.appendChild(row);
  }
}
// MTft8 (WSJT-X 3.3) ignores the decode depth: the select is greyed out while it is chosen
let ft8Decoders = [];
function depthForDecoder() {
  const mt = ft8Decoders.find((d) => d.id === $("set-decoder").value)?.mt;
  $("set-depth").disabled = !!mt;
  $("set-depth").title = mt ? "MTft8 sets its own depth — this applies to FT4 and to the other decoders" : "";
}
$("set-decoder").onchange = depthForDecoder;
function renderHuntBlocked(list) {             // [{call, t, dxcc, snr, band, grid, tries, manual}], newest first
  $("hunt-blocked-n").textContent = list.length;
  const utc = (t) => new Date(t * 1000).toISOString().slice(5, 16).replace("T", " ");   // MM-DD HH:MM
  const v = (x) => (x == null || x === "" ? "—" : esc(String(x)));
  $("hunt-blocked-list").innerHTML =
    `<table class="hb-table"><thead><tr><th>Blocked (UTC)</th><th>Call</th><th>Country</th><th>Grid</th>` +
    `<th>Band</th><th class="n">SNR</th><th class="n">Tries</th><th></th></tr></thead><tbody>` +
    (list.length ? list.map((r) =>
      `<tr><td>${r.t ? utc(r.t) : "—"}</td><td class="hb-c">${esc(r.call)}</td>` +
      `<td>${r.manual ? '<span class="dim">added by hand</span>' : v(r.dxcc)}</td><td>${v(r.grid)}</td><td>${v(r.band)}</td>` +
      `<td class="n">${r.snr == null ? "—" : r.snr}</td><td class="n">${v(r.tries)}</td>` +
      `<td class="x"><button type="button" data-call="${esc(r.call)}" title="Take ${esc(r.call)} off the list: Hunt may call it again" aria-label="remove ${esc(r.call)}">×</button></td></tr>`).join("")
      : '<tr class="empty"><td colspan="8">No station on the list</td></tr>') +
    "</tbody></table>";
  $("hunt-unblock").disabled = !list.length;
  for (const b of $("hunt-blocked-list").querySelectorAll("button[data-call]"))
    b.onclick = () => huntBlockEdit(b.dataset.call, false);
}
// edit the list by hand: applied at once (as Clear list), the settings list reloads
async function huntBlockEdit(call, blocked) {
  try {
    renderSeq(await api("/api/seq/hunt/block", { call, blocked }));
    const s = await api("/api/settings");
    renderHuntBlocked(s.hunt_blocked || []);
    toast(blocked ? `${call} added — Hunt will not call it` : `${call} taken off — Hunt may call it again`);
  } catch { /* toast */ }
}
function huntAdd() {
  const c = $("hunt-add-call").value.trim().toUpperCase();
  if (!/^[A-Z0-9/]{3,12}$/.test(c)) { toast("Not a callsign", true); return; }
  $("hunt-add-call").value = "";
  huntBlockEdit(c, true);
}
$("hunt-add").onclick = huntAdd;
$("hunt-add-call").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); huntAdd(); } };
$("hunt-unblock").onclick = async () => {
  try {
    renderSeq(await api("/api/seq/hunt/clear", {}));
    renderHuntBlocked([]);
    toast("Hunt list cleared — these stations may be called again");
  } catch { /* toast */ }
};
async function loadFt8() {
  const s = await api("/api/settings").catch(() => null);
  if (!s) return;
  $("set-depth").value = String(s.digi_depth);
  $("set-decoder").innerHTML = (s.digi_decoders || []).map((d) =>
    `<option value="${esc(d.id)}"${d.available ? "" : " disabled"}>${esc(d.label)}${d.available ? "" : " — not installed"}</option>`).join("");
  $("set-decoder").value = s.digi_decoder || "jt9";
  ft8Decoders = s.digi_decoders || [];
  depthForDecoder();
  $("set-low").value = s.digi_low_hz;
  $("set-high").value = s.digi_high_hz;
  $("set-closing").value = s.ft_closing || "RR73";
  $("set-hunt-target").value = s.hunt_target || "band";
  $("set-hunt-snr").value = s.hunt_min_snr ?? -24;
  $("set-hunt-min").value = s.hunt_minutes ?? 180;
  $("set-psk").checked = !!s.psk_report;
  $("set-psk-ant").value = s.psk_antenna || "";
  $("psk-dest").textContent = s.psk_dest || "—";
  renderHuntBlocked(s.hunt_blocked || []);
  digiColors = s.digi_colors || {};
  colorDraft = {};
  applyColors(digiColors);
  renderColorGrid();
}
$("col-reset-all").onclick = () => {
  for (const d of COLOR_DEFS) colorDraft[d.key] = "";
  applyColors({});
  renderColorGrid();
};
$("ft8-save").onclick = async () => {
  const depth = $("set-depth").value;
  const cols = Object.fromEntries(COLOR_DEFS.map((d) => [d.key, colorDraft[d.key] ?? digiColors[d.key] ?? ""]));
  try {
    const s = await api("/api/settings", {
      digi_depth: depth === "AUTO" ? "AUTO" : +depth,
      digi_decoder: $("set-decoder").value || undefined,
      digi_low_hz: +$("set-low").value,
      digi_high_hz: +$("set-high").value,
      ft_closing: $("set-closing").value,
      hunt_target: $("set-hunt-target").value,
      hunt_min_snr: Math.round(+$("set-hunt-snr").value),
      hunt_minutes: Math.max(0, Math.min(1440, Math.round(+$("set-hunt-min").value) || 0)),   // 0 = never
      psk_report: $("set-psk").checked,
      psk_antenna: $("set-psk-ant").value.trim(),
      digi_colors: cols,
    });
    digiColors = s.digi_colors || {};
    colorDraft = {};
    applyColors(digiColors);
    renderColorGrid();
    pollDigi();
    toast("Saved.");
  } catch { /* toast shown */ }
};

// Styled stand-in for window.confirm (#ask). The browser's own dialog is the
// one piece of foreign chrome left in this console, and it turned up where it
// hurts most: in front of the switch that cuts the radio's mains. Resolves
// true only on the confirming button - Escape, CANCEL and a click on the dim
// behind the card all mean no, and CANCEL holds the focus so a stray Enter
// never switches anything off.
function ask(text, { title = "Please confirm", yes = "OK", danger = false } = {}) {
  const m = $("ask"), y = $("ask-yes"), n = $("ask-no");
  $("ask-title").textContent = title;
  $("ask-text").textContent = text;
  y.textContent = yes;
  y.className = danger ? "btn-danger" : "btn-primary";
  m.classList.add("open");
  n.focus();
  return new Promise((resolve) => {
    const onKey = (e) => { if (e.key === "Escape") done(false); };
    const done = (v) => {
      m.classList.remove("open");
      y.onclick = n.onclick = m.onclick = null;
      document.removeEventListener("keydown", onKey);
      resolve(v);
    };
    y.onclick = () => done(true);
    n.onclick = () => done(false);
    m.onclick = (e) => { if (e.target === m) done(false); };
    document.addEventListener("keydown", onKey);
  });
}

// HF band conditions under the waterfall (VOACAP for the own locator). The
// backend computes the whole day once and answers from a cache, so a short
// poll is cheap - and it catches the change of the UTC hour.
// The sun/moon switches the chips between "now" and the other half of the
// day: at night one click shows how the bands do in daylight (the mean over
// today's daylight hours here), a second click goes back to now. "Now" always
// follows the real time of day; only the preview is chosen by hand.
const PROP_LEVEL = { good: "good", fair: "fair", poor: "poor" };
let propData = null, propView = "now";            // "now" | "day" | "night"
const hh = (h) => String(h).padStart(2, "0");
async function loadProp() {
  try { propData = await api("/api/propagation"); } catch { return; }
  renderProp();
}
function renderProp() {
  const p = propData, box = $("prop");
  if (!p?.available) { box.hidden = true; return; }
  if (propView !== "now" && !p.halves?.[propView]) propView = "now";   // polar day/night
  const half = propView === "now" ? null : p.halves[propView];
  const showDay = half ? propView === "day" : p.day;
  const other = p.day ? "night" : "day";
  const dn = $("prop-dn");
  dn.className = "prop-dn " + (showDay ? "day" : "night");
  dn.setAttribute("aria-pressed", String(!!half));
  dn.disabled = !half && !p.halves?.[other];
  dn.title = half ? `Showing ${propView} (mean ${hh(half.from)}\u2013${hh(half.to)} UTC) \u2014 click: back to now`
    : `Now: ${p.day ? "day" : "night"} here \u2014 click: show the ${other}`;
  dn.setAttribute("aria-label", dn.title);
  box.classList.toggle("preview", !!half);
  const m = p.model;
  box.title = `VOACAP for ${p.grid} \u00b7 SSN ${Math.round(p.ssn)}${p.ssn_month ? ` (${p.ssn_month})` : ""}` +
    ` \u00b7 ${m.power_w} W ${m.mode}, ${m.antenna} antenna` +
    ` \u00b7 mean over ${m.paths} paths, ${m.ring_km[0]}\u2013${m.ring_km[m.ring_km.length - 1]} km`;
  (half ? half.groups : p.groups).forEach((g, i) => {
    const el = box.querySelector(`.prop-g[data-i="${i}"]`);
    if (!el) return;
    const single = !g.name.includes("-");           // 6 m stands alone
    el.textContent = single ? `${g.name}m` : g.name;
    el.className = "prop-g " + (PROP_LEVEL[g.level] || "");
    const bands = Object.entries(g.bands).map(([b, r]) => `${b} m ${Math.round(r * 100)} %`).join(" \u00b7 ");
    // VOACAP knows the F2 layer only - on 6 m that is the rare solar-maximum
    // opening, not the sporadic-E that brings the summer openings
    const es = single ? "\nF2 only: sporadic-E is not in the model" : "";
    el.title = (half
      ? `${g.name} m by ${propView} (${hh(half.from)}\u2013${hh(half.to)} UTC): ${g.level} \u2014 ${bands}`
      : `${g.name} m now (${hh(p.utc_hour)} UTC): ${g.level} \u2014 ${bands}\nnext: ` +
        p.next.map((n) => `${hh(n.utc_hour)}h ${n.levels[i]}`).join(", ")) + es;
  });
  box.hidden = false;
}
$("prop-dn").onclick = () => {
  if (!propData?.available) return;
  propView = propView === "now" ? (propData.day ? "night" : "day") : "now";
  renderProp();
};

// -- radio power (Shelly), title-bar switch and power-off plate as in TM-V71 Remote
let powerState = null;
function refreshPowerUi() {
  const btn = $("power-switch");
  const avail = !!powerState?.available, on = powerState?.on;
  btn.classList.toggle("unavailable", !avail);
  btn.classList.toggle("on", avail && on === true);
  btn.classList.toggle("off", avail && on === false);
  document.body.classList.toggle("radio-off", avail && on === false);
  btn.title = !powerState?.configured ? "Radio power: set up a Shelly in Settings ▸ General"
    : !avail ? `Shelly not reachable: ${powerState.error || "?"}`
    : on ? "Radio is ON — click to turn off" : "Radio is OFF — click to turn on";
  paintRadioPlate();
}

// The plate in front of the console says why nothing works, and there are
// three reasons, not one: the mains are off, the rig is still booting (the
// Shelly switched on seconds ago and an FT-950 answers nothing until its CAT
// port is up), or the mains are on and nothing ever answered. Only the middle
// one goes away by itself, so only that one carries a moving bar.
const BOOT_GRACE_MS = 30000;            // measured: mains to CAT is ~8-12 s
let mainsNoCatSince = 0, plateState = "", plateTimer = 0;
const PLATE_TXT = {
  off:  ["Radio is off", "switch on with the power button in the title bar"],
  boot: ["FT-950 booting \u2026",
         "waiting for the rig's CAT port \u2014 the memory keys fill in by themselves"],
  wait: ["No CAT answer",
         "mains is on, but nothing answers: check the SCU-17 cable and the rig's own power switch"],
};
function paintRadioPlate() {
  const hint = $("radio-hint");
  if (!hint) return;
  const mainsOn = powerState?.on === true;
  if (!mainsOn || radio.connected) mainsNoCatSince = 0;
  else if (!mainsNoCatSince) mainsNoCatSince = Date.now();
  const st = !powerState?.available ? ""                     // no Shelly: nothing to say
    : powerState.on === false ? "off"
    : radio.connected ? ""
    : Date.now() - mainsNoCatSince < BOOT_GRACE_MS ? "boot" : "wait";
  if (st !== plateState) { plateState = st; hint.dataset.dismissed = ""; }  // a new reason earns a new look
  clearTimeout(plateTimer);
  plateTimer = 0;
  // the bar is stopped on the way out too: a running animation in a
  // display:none plate is invisible, but the invariant "it only moves while
  // the rig boots" is worth keeping true
  if (!st) { hint.hidden = true; $("rh-prog").hidden = true; return; }
  const [txt, sub] = PLATE_TXT[st];
  hint.dataset.state = st;
  $("rh-txt").textContent = txt;
  $("rh-sub").textContent = sub;
  $("rh-prog").hidden = st !== "boot";
  hint.hidden = hint.dataset.dismissed === "1";
  if (st === "boot")                   // the grace window ends the boot state
    plateTimer = setTimeout(paintRadioPlate,
                            BOOT_GRACE_MS - (Date.now() - mainsNoCatSince) + 200);
}
async function refreshPower() {
  try { powerState = await api("/api/power-switch"); } catch { powerState = null; }
  refreshPowerUi();
}
$("power-switch").onclick = async () => {
  if (!powerState?.configured) { toast("Set up the Shelly in Settings ▸ General", true); openSettings(); return; }
  const turnOff = powerState.on === true;
  if (turnOff && !(await ask(
      "A running transmission and the sequencer are stopped first, then the Shelly cuts the 13.8 V line.",
      { title: "Turn the radio off?", yes: "TURN OFF", danger: true }))) return;
  try {
    powerState = await api("/api/power-switch", { on: !turnOff });
    refreshPowerUi();
    powerOnPending = !!powerState.on;
    paintRadioPlate();
    toast(powerState.on ? "Radio on — waiting for the FT-950 to boot" : "Radio off");
  } catch { refreshPower(); }
};
$("radio-hint-x").onclick = () => { $("radio-hint").dataset.dismissed = "1"; paintRadioPlate(); };
api("/api/settings").then((s) => { $("rh-cert").hidden = !s.ca_available; }).catch(() => {});
refreshPower();
setInterval(refreshPower, 15000);

// -- logo (trademark: stays on the Pi, see /api/branding)
$("logo-yaesu").onclick = async () => {
  try { await api("/api/branding/logo/yaesu", {}); await loadBranding(); toast("Yaesu logo loaded."); } catch { /* toast */ }
};
$("logo-remove").onclick = async () => {
  try { await request("DELETE", "/api/branding/logo"); await loadBranding(); } catch { /* toast */ }
};
$("logo-file").onchange = async (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (!f) return;
  const ext = (f.name.split(".").pop() || "").toLowerCase();
  try { await request("POST", "/api/branding/logo?ext=" + encodeURIComponent(ext), f, true); await loadBranding(); } catch { /* toast */ }
};

// -- audio
const audSliders = {};
function audSlider(id, send) {
  const el = $(id), out = $(id + "-val");
  let timer;
  el.addEventListener("input", () => {
    touched.set(id, Date.now());
    out.textContent = el.value;
    clearTimeout(timer);
    timer = setTimeout(() => send(+el.value).catch(() => {}), 250);
  });
  audSliders[id] = (v) => {
    if (Date.now() - (touched.get(id) || 0) < 2000) return;
    el.disabled = v === null || v === undefined;
    el.value = v ?? 0;
    out.textContent = v ?? "—";
  };
}
audSlider("aud-play", (v) => api("/api/audio/playback", { percent: v }));
$("aud-device").onchange = (e) => api("/api/audio/device", { device: e.target.value }).then(() => toast("Capture device changed."));

async function loadAudio(full) {
  let a;
  try { a = await api("/api/audio"); } catch { return; }
  const rms = a.rms_dbfs, peak = a.peak_dbfs;
  const meter = $("aud-fill");
  meter.style.width = rms != null ? Math.max(0, Math.min(100, (rms + 60) / 60 * 100)) + "%" : "0";
  meter.parentElement.classList.toggle("hot", peak != null && peak > -1);
  $("aud-read").textContent = a.running
    ? `RMS ${rms} dBFS · peak ${peak} dBFS` + (rms < -40 ? " — too low: raise 051" : rms > -20 ? " — too high: lower 051" : " — in target range")
    : "no capture: " + (a.error || "—");
  if (full) {
    const sel = $("aud-device");
    sel.innerHTML = "";
    const devs = a.devices.some((d) => d.device === a.device) ? a.devices : [{ device: a.device, name: a.device }, ...a.devices];
    for (const d of devs) {
      const o = document.createElement("option");
      o.value = d.device;
      o.textContent = `${d.name} — ${d.device}`;
      sel.appendChild(o);
    }
    sel.value = a.device;
  }
  audSliders["aud-play"](a.playback_percent);
}

// -- FT-950 audio menus (EX) with saved default
const menuShow = (it, v) => it.kind === "enum" ? (it.options[v] ?? v)
  : it.kind === "micgain" ? (v === 1000 ? "MIC knob" : `${v} (fixed)`)
  : `${v > 0 && it.kind === "sint" ? "+" : ""}${v}${it.unit ? " " + it.unit : ""}`;
function menuRow(it) {
  const row = document.createElement("div");
  row.className = "menu-row" + (it.changed ? " changed" : "");
  row.dataset.no = it.no;
  let ctl;
  if (it.kind === "enum") {
    ctl = `<select>${Object.entries(it.options).map(([k, l]) => `<option value="${k}"${+k === it.value ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  } else if (it.kind === "micgain") {
    const knob = it.value === 1000;
    ctl = `<select class="mg"><option value="knob"${knob ? " selected" : ""}>MIC knob</option><option value="fix"${knob ? "" : " selected"}>fixed</option></select>
           <input type="range" min="0" max="100" step="1" value="${knob ? 50 : it.value}"${knob ? " disabled" : ""}>`;
  } else {
    ctl = `<input type="range" min="${it.min}" max="${it.max}" step="${it.step}" value="${it.value ?? it.min}">`;
  }
  row.innerHTML = `<span class="ml">${esc(it.label)}<small>EX${String(it.no).padStart(3, "0")}</small></span>
    <span class="mc">${ctl}</span><span class="mv">${it.value == null ? "—" : esc(menuShow(it, it.value))}</span>
    <span class="md" title="Default">${it.default == null ? "" : "Def " + esc(menuShow(it, it.default))}</span>
    <button class="mr" type="button" title="Reset to default">↺</button>`;
  let timer;
  const send = (v) => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      row.classList.add("busy");
      try { row.replaceWith(menuRow(await api("/api/radio/menu", { no: it.no, value: v }))); }
      catch { loadMenus(); }
    }, 300);
  };
  const range = row.querySelector("input[type=range]"), sel = row.querySelector("select");
  if (it.kind === "micgain") {
    sel.onchange = () => { range.disabled = sel.value === "knob"; send(sel.value === "knob" ? 1000 : +range.value); };
    range.oninput = () => { row.querySelector(".mv").textContent = range.value + " (fixed)"; send(+range.value); };
  } else if (sel) {
    sel.onchange = () => send(+sel.value);
  } else {
    range.oninput = () => { row.querySelector(".mv").textContent = menuShow(it, +range.value); send(+range.value); };
  }
  row.querySelector(".mr").onclick = async () => {
    row.classList.add("busy");
    try { renderMenus(await api("/api/radio/menus/reset", { nos: [it.no] })); } catch { loadMenus(); }
  };
  return row;
}
function renderMenus(d) {
  const box = $("menu-list");
  box.innerHTML = "";
  let grp = null;
  for (const it of d.items) {
    if (it.group !== grp) {
      grp = it.group;
      const h = document.createElement("div");
      h.className = "menu-grp";
      h.textContent = grp;
      box.appendChild(h);
    }
    box.appendChild(menuRow(it));
  }
  const n = d.items.filter((i) => i.changed).length;
  $("menu-status").textContent = `${d.items.length} menus · ${n ? n + " differ from default" : "all at default"}` +
    (d.defaults_created ? " · default just created" : "") + (d.reset?.length ? ` · reset: ${d.reset.join(", ")}` : "");
}
async function loadMenus() {
  $("menu-status").textContent = "reading …";
  try { renderMenus(await api("/api/radio/menus")); } catch (e) { $("menu-status").textContent = e.message; }
}
$("menu-reset").onclick = async () => { try { renderMenus(await api("/api/radio/menus/reset", {})); } catch { /* toast */ } };
$("menu-snap").onclick = async () => { try { renderMenus(await api("/api/radio/menus/defaults", {})); toast("Current values saved as default."); } catch { /* toast */ } };

// -- Pi hardware (as TM-V71 Remote): live figures + 24 h graph
const fmtBytes = (n) => {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
};
const fmtUptime = (s) => {
  s = Math.floor(s);
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return (d ? `${d}d ` : "") + (d || h ? `${h}h ` : "") + `${m}m`;
};
async function loadSystem() {
  let s;
  try { s = await api("/api/system"); } catch { return; }
  const pct = (u, t) => (t ? ` (${Math.round(u / t * 100)}%)` : "");
  $("sys-model").textContent = s.model;
  $("sys-cpu").textContent = `${s.cpu_percent}% · ${s.cores} cores${s.freq_mhz ? " @ " + s.freq_mhz + " MHz" : ""}`;
  $("sys-load").textContent = (s.load || []).join("  ·  ");
  $("sys-temp").textContent = s.temp_c != null ? `${s.temp_c} °C` : "—";
  $("sys-mem").textContent = `${fmtBytes(s.mem_used)} / ${fmtBytes(s.mem_total)}${pct(s.mem_used, s.mem_total)}`;
  $("sys-swap").textContent = s.swap_total ? `${fmtBytes(s.swap_used)} / ${fmtBytes(s.swap_total)}${pct(s.swap_used, s.swap_total)}` : "none";
  $("sys-disk").textContent = `${fmtBytes(s.disk_used)} / ${fmtBytes(s.disk_total)}${pct(s.disk_used, s.disk_total)}`;
  $("sys-app").textContent = s.app_rss ? `${fmtBytes(s.app_rss)} · ${s.app_threads} Threads` : "—";
  $("sys-uptime").textContent = fmtUptime(s.uptime_sec);
  // RX dropout counters of the audio path (since the service started)
  const au = s.audio;
  $("sys-audio-card").textContent = au ? `${au.overruns} overruns · ${au.slow} slow (max ${au.max_ms} ms)${au.restarts ? ` · ${au.restarts} restarts` : ""}` : "—";
  $("sys-audio-web").textContent = au ? `${au.rx_gaps} gaps · ${au.rx_drops} drops · ${au.peers} listening` : "—";
  // the WSJT-X decoder (jt9): its last pass and the last minute
  const d = s.decoder || {};
  const ago = d.last_age_s != null ? ` · ${Math.round(d.last_age_s)} s ago` : "";
  $("sys-dec").textContent = d.decoder
    ? `${d.decoder} · last pass ${d.last_cpu_s} s CPU in ${d.last_wall_s} s${d.last_parts > 1 ? ` · ${d.last_parts} parts` : ""}${ago}`
    : "no decode yet";
  $("sys-decmem").textContent = d.decoder
    ? `${d.last_mem_mb} MB${d.last_parts > 1 ? ` (${d.last_parts} × up to ${d.last_part_mem_mb} MB)` : ""} · peak ${d.peak_mem_mb} MB`
    : "—";
  $("sys-deccpu").textContent = d.decoder
    ? `${d.min_cpu_s} s CPU · ${(d.min_cpu_s / 60 / (s.cores || 1) * 100).toFixed(1)} % of the Pi · ${d.min_passes} passes`
    : "—";
}
// History graphs (24 h, one value a minute): stacked panels on one canvas, one
// scale per panel, a shared time axis. Each graph keeps its own data, range and
// hover; the hardware graph and the WSJT-X decoder graph are two of them.
const SYS_HUES = { dark: ["#3987e5", "#d95926", "#199e70"], light: ["#2a78d6", "#eb6834", "#1baf7a"] };
const hhmm = (t) => { const d = new Date(t * 1000); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };
const GRAPHS = {
  sys: {
    canvas: "sys-canvas", tip: "sys-tip", range: ".sys-only", url: "/api/system/history?hours=24",
    hist: null, rangeH: 24, hover: -1,
    panels: [
      { key: "temp_c", title: "TEMPERATURE", slot: 1, fmt: (v) => `${v.toFixed(1)} °C`, min0: false },
      { key: "load1", title: "LOAD (1 MIN)", slot: 2, fmt: (v) => v.toFixed(2), min0: true, lab: (v) => v.toFixed(1) },
      { key: "app_rss_mb", title: "MEMORY", slot: 0, fmt: (v) => `${v} MB`, min0: true, with: "mem_used_mb",
        head: (now, other) => `Service ${now} MB   Host ${other} MB` },
    ],
    tipHtml: (h, i) => `${h.temp_c[i] == null ? "—" : h.temp_c[i].toFixed(1) + " °C"} &middot; Load ${h.load1[i] == null ? "—" : h.load1[i].toFixed(2)}<br>` +
      `Service ${h.app_rss_mb[i]} MB &middot; Host ${h.mem_used_mb[i]} MB`,
  },
  dec: {
    canvas: "dec-canvas", tip: "dec-tip", range: ".dec-range-btn", url: "/api/system/decoder-history?hours=24",
    hist: null, rangeH: 24, hover: -1,
    panels: [
      { key: "cpu_pct", title: "CPU (SHARE OF THE PI)", slot: 0, fmt: (v) => `${v.toFixed(1)} %`, min0: true, lab: (v) => v.toFixed(1) },
      { key: "mem_mb", title: "MEMORY (PEAK OF A PASS)", slot: 2, fmt: (v) => `${v} MB`, min0: true },
    ],
    tipHtml: (h, i) => `CPU ${h.cpu_pct[i].toFixed(1)} % (${h.cpu_s[i]} s) &middot; ${h.passes[i]} passes<br>` +
      `Memory ${h.mem_mb[i]} MB &middot; longest pass ${(h.wall_ms[i] / 1000).toFixed(1)} s`,
  },
};
const cssVar = (name, fb) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fb;
function niceMax(v) {
  if (!(v > 0)) return 1;
  const mag = 10 ** Math.floor(Math.log10(v));
  for (const st of [1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10]) if (v <= st * mag) return st * mag;
  return 10 * mag;
}
function graphWindow(G) {
  const h = G.hist;
  if (!h || !h.n) return null;
  const cut = (h.ts[h.n - 1] || 0) - G.rangeH * 3600;
  let from = 0;
  while (from < h.n && h.ts[from] < cut) from++;
  return { from, n: h.n - from };
}
function panelRange(G, p, win) {
  const h = G.hist;
  let hi = -Infinity, lo = Infinity;
  for (const col of [h[p.key]].concat(p.with ? [h[p.with]] : [])) {
    for (let i = win.from; i < h.n; i++) {
      const v = col[i];
      if (v == null) continue;
      if (v > hi) hi = v;
      if (v < lo) lo = v;
    }
  }
  if (!isFinite(hi)) { hi = 1; lo = 0; }
  if (p.min0) return { lo, hi, yMin: 0, yMax: niceMax(hi) || 1 };
  const pad = Math.max(2, (hi - lo) * 0.25);
  return { lo, hi, yMin: Math.floor((lo - pad) / 5) * 5, yMax: Math.ceil((hi + pad) / 5) * 5 };
}
function drawGraph(G) {
  const cv = $(G.canvas);
  if (!cv || !modal.classList.contains("open") || activeTab !== "hardware") return;
  const dpr = window.devicePixelRatio || 1;
  const r = cv.getBoundingClientRect();
  const w = Math.max(1, Math.round(r.width * dpr)), hgt = Math.max(1, Math.round(r.height * dpr));
  if (cv.width !== w || cv.height !== hgt) { cv.width = w; cv.height = hgt; }
  const ctx = cv.getContext("2d"), W = cv.width, H = cv.height, h = G.hist;
  const light = document.documentElement.dataset.theme === "light";
  const hues = light ? SYS_HUES.light : SYS_HUES.dark;
  const ink = cssVar("--text", "#c9d6df"), dim = cssVar("--text-dim", "#93a6b4");
  const fUi = cssVar("--font-ui", "sans-serif"), fMono = cssVar("--font-mono", "monospace");
  ctx.clearRect(0, 0, W, H);
  const win = graphWindow(G);
  if (!win || win.n < 2) {
    ctx.fillStyle = dim;
    ctx.font = `${11 * dpr}px ${fUi}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(h ? "collecting values — one per minute" : "no history yet", W / 2, H / 2);
    return;
  }
  const padL = 44 * dpr, padR = 10 * dpr, padT = 8 * dpr, padB = 18 * dpr, gap = 10 * dpr;
  const plotW = W - padL - padR;
  const panelH = (H - padT - padB - gap * (G.panels.length - 1)) / G.panels.length;
  const t0 = h.ts[win.from], t1 = h.ts[h.n - 1], span = Math.max(1, t1 - t0);
  const xOf = (t) => padL + (t - t0) / span * plotW;
  const last = (col) => { for (let i = h.n - 1; i >= win.from; i--) if (col[i] != null) return col[i]; return null; };

  G.panels.forEach((p, pi) => {
    const top = padT + pi * (panelH + gap), bot = top + panelH;
    const { lo, hi, yMin, yMax } = panelRange(G, p, win);
    const yOf = (v) => bot - (v - yMin) / Math.max(1e-9, yMax - yMin) * panelH;
    ctx.strokeStyle = "rgba(127,140,150,0.12)";
    ctx.lineWidth = dpr;
    for (let g = 0; g <= 2; g++) {
      const y = Math.round(top + panelH * g / 2) + 0.5;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
    }
    ctx.fillStyle = dim; ctx.textAlign = "right"; ctx.textBaseline = "middle";
    ctx.font = `${9 * dpr}px ${fMono}`;
    const lab = p.lab || ((v) => String(Math.round(v)));
    ctx.fillText(lab(yMax), padL - 6 * dpr, top + 5 * dpr);
    ctx.fillText(lab(yMin), padL - 6 * dpr, bot - 5 * dpr);
    if (p.with) {                       // the larger quantity as a recessive area
      ctx.beginPath(); ctx.moveTo(xOf(h.ts[win.from]), bot);
      for (let i = win.from; i < h.n; i++) {
        const v = h[p.with][i];
        if (v != null) ctx.lineTo(xOf(h.ts[i]), yOf(v));
      }
      ctx.lineTo(xOf(t1), bot); ctx.closePath();
      ctx.fillStyle = light ? "rgba(90,105,118,0.16)" : "rgba(147,166,180,0.18)";
      ctx.fill();
    }
    ctx.strokeStyle = hues[p.slot]; ctx.lineWidth = 2 * dpr; ctx.lineJoin = "round";
    ctx.beginPath();
    let pen = false;
    for (let i = win.from; i < h.n; i++) {
      const v = h[p.key][i];
      if (v == null) { pen = false; continue; }
      const x = xOf(h.ts[i]), y = yOf(v);
      if (pen) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      pen = true;
    }
    ctx.stroke();
    const sw = 7 * dpr, hx = padL + 2 * dpr, hy = top + 9 * dpr;
    ctx.fillStyle = hues[p.slot];
    ctx.fillRect(hx, hy - sw / 2, sw, sw);
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.font = `${9 * dpr}px ${fUi}`;
    ctx.fillStyle = dim;
    ctx.fillText(p.title, hx + sw + 5 * dpr, hy);
    const now = last(h[p.key]);
    let txt = now == null ? "—" : `${p.fmt(now)}   min ${p.fmt(lo)}   max ${p.fmt(hi)}`;
    if (p.head) { const ov = last(h[p.with]); txt = p.head(now == null ? "—" : now, ov == null ? "—" : ov); }
    ctx.textAlign = "right"; ctx.fillStyle = ink;
    ctx.font = `${10 * dpr}px ${fMono}`;
    ctx.fillText(txt, W - padR - 2 * dpr, hy);
    if (G.hover >= win.from && G.hover < h.n && h[p.key][G.hover] != null) {
      const x = Math.round(xOf(h.ts[G.hover])) + 0.5, y = yOf(h[p.key][G.hover]);
      ctx.beginPath(); ctx.arc(x, y, 3.2 * dpr, 0, Math.PI * 2);
      ctx.fillStyle = hues[p.slot]; ctx.fill();
      ctx.strokeStyle = cssVar("--panel-2", "#161e25"); ctx.lineWidth = 2 * dpr; ctx.stroke();
    }
  });
  ctx.fillStyle = dim; ctx.textBaseline = "top";
  ctx.font = `${9 * dpr}px ${fMono}`;
  const ticks = Math.min(6, Math.max(2, Math.round(plotW / (70 * dpr))));
  for (let i = 0; i <= ticks; i++) {
    const t = t0 + span * i / ticks;
    ctx.textAlign = i === 0 ? "left" : (i === ticks ? "right" : "center");
    ctx.fillText(hhmm(t), xOf(t), H - padB + 4 * dpr);
  }
  if (G.hover >= win.from && G.hover < h.n) {
    const x = Math.round(xOf(h.ts[G.hover])) + 0.5;
    ctx.strokeStyle = light ? "rgba(27,39,49,0.35)" : "rgba(201,214,223,0.35)";
    ctx.lineWidth = dpr;
    ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, H - padB); ctx.stroke();
  }
}
function drawSysGraph() { for (const G of Object.values(GRAPHS)) drawGraph(G); }
async function loadSysHistory() {
  await Promise.all(Object.values(GRAPHS).map(async (G) => {
    try { G.hist = await api(G.url); } catch { G.hist = null; }
  }));
  drawSysGraph();
}
for (const G of Object.values(GRAPHS)) {
  for (const b of document.querySelectorAll(G.range)) {
    b.onclick = () => {
      G.rangeH = +b.dataset.hours;
      for (const x of document.querySelectorAll(G.range)) x.classList.toggle("is-on", x === b);
      drawGraph(G);
    };
  }
  $(G.canvas).addEventListener("mousemove", (ev) => {
    const win = graphWindow(G), h = G.hist;
    if (!win || win.n < 2) return;
    const r = $(G.canvas).getBoundingClientRect(), padL = 44, padR = 10;
    const plotW = Math.max(1, r.width - padL - padR);
    const f = Math.min(1, Math.max(0, (ev.clientX - r.left - padL) / plotW));
    const t = h.ts[win.from] + (h.ts[h.n - 1] - h.ts[win.from]) * f;
    let best = win.from, bd = Infinity;
    for (let i = win.from; i < h.n; i++) {
      const d = Math.abs(h.ts[i] - t);
      if (d < bd) { bd = d; best = i; }
    }
    G.hover = best;
    const tip = $(G.tip);
    tip.innerHTML = `<b>${hhmm(h.ts[best])}</b><br>` + G.tipHtml(h, best);
    tip.hidden = false;
    tip.style.left = `${Math.min(r.width - 10, Math.max(60, padL + f * plotW))}px`;
    tip.style.top = `${Math.max(0, ev.clientY - r.top - 54)}px`;
    drawGraph(G);
  });
  $(G.canvas).addEventListener("mouseleave", () => { $(G.tip).hidden = true; G.hover = -1; drawGraph(G); });
}

// ---------------------------------------------------------------- log panel (Wavelog + QRZ)
let logCfg = null;
function fillQso(d) {
  $("q-call").value = d.from;
  logReady();
  $("q-grid").value = d.grid || "";
  $("q-rcvd").value = (d.snr < 0 ? "-" : "+") + String(Math.abs(d.snr)).padStart(2, "0");   // −5 → "-05"
  for (const id of ["q-name", "q-qth", "q-country", "q-sent", "q-comment"]) $(id).value = "";
  $("q-country").value = d.dxcc || "";
  showWorked(d.worked);
}
function showWorked(w) {
  const el = $("q-worked");
  if (!w) { el.textContent = ""; el.className = "q-worked"; return; }
  el.className = "q-worked " + (w.call_band ? "dupe" : "new");
  el.textContent = w.call_band ? "WORKED (this band)" : w.call ? "NEW ON THIS BAND" : !w.dxcc ? "NEW DXCC" : "NEW STATION";
}
function qsoAutoLine() {
  const dial = radio.vfo?.freq_hz;
  $("q-auto").textContent = dial
    ? `${((dial + boxes.tx) / 1e6).toFixed(6)} MHz · ${digiMode} · ${radio.power ?? "—"} W`
    : "— · — · —";
}
setInterval(qsoAutoLine, 1000);
async function qrzLookup() {
  const inp = $("q-call");
  const call = inp.value.trim().toUpperCase();
  if (!call) { toast("Enter a callsign", true); inp.focus(); return; }
  inp.value = call;
  const btn = $("q-lookup");
  btn.disabled = true;
  try {
    const r = await api("/api/log/lookup?call=" + encodeURIComponent(call));
    for (const [id, k] of [["q-name", "name"], ["q-grid", "gridsquare"], ["q-qth", "qth"], ["q-country", "country"]]) {
      if (r[k] && !$(id).value.trim()) $(id).value = r[k];
    }
    showWorked(r.worked);
    const w = r.worked.call_band ? "already worked on this band" : r.worked.call ? "worked, but not on this band" : "not worked yet";
    const bits = [r.name, r.gridsquare, r.qth, r.country].filter(Boolean).join(" · ");
    toast(r.sources.length ? `${call}: ${bits || "no details"} · ${w}`
      : `${call}: QRZ.com not configured (Settings ▸ Log) — ${r.dxcc || "DXCC unknown"} · ${w}`);
  } catch { /* toast shown */ } finally { btn.disabled = false; }
}
$("q-lookup").onclick = qrzLookup;
// Enter in the callsign field looks up — it never logs: a stray key press must
// not put a QSO into the log. Logging only via LOG QSO.
$("q-call").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); qrzLookup(); } });
$("qso-form").onsubmit = (e) => e.preventDefault();
$("q-clear").onclick = () => { $("qso-form").reset(); showWorked(null); };
// LOG QSO is lit (filled) only when there is something to log: a callsign in the form
// LOG WL / LOG QRZ: lit (filled) when there is a callsign to log; ✓ once logged to
// that target (the form stays, so the same QSO can go to the other one as well)
const LOG_BTNS = ["q-log-wl", "q-log-qrz"];
function logReady() {
  const has = !!$("q-call").value.trim();
  for (const id of LOG_BTNS) {
    $(id).classList.toggle("ready", has);
    if (!has || $(id).dataset.doneFor !== $("q-call").value.trim().toUpperCase()) {
      $(id).classList.remove("done"); delete $(id).dataset.doneFor;
    }
  }
}
$("q-call").addEventListener("input", logReady);
$("qso-form").addEventListener("reset", () => setTimeout(logReady));
logReady();
async function logTo(btn) {
  if (!$("q-call").value.trim()) { toast("Enter a callsign", true); $("q-call").focus(); return; }
  const to = btn.dataset.to, where = to === "qrz" ? "the QRZ.com logbook" : "Wavelog";
  const body = {
    call: $("q-call").value.trim(), gridsquare: $("q-grid").value.trim(),
    rst_sent: $("q-sent").value.trim(), rst_rcvd: $("q-rcvd").value.trim(),
    name: $("q-name").value.trim(), qth: $("q-qth").value.trim(),
    country: $("q-country").value.trim(), comment: $("q-comment").value.trim(),
    tx_offset_hz: boxes.tx, to: [to],
  };
  btn.disabled = true;
  try {
    const r = await api("/api/log/qso", body);
    const ok = to === "qrz" ? r.qrz?.ok : r.ok;
    const msg = to === "qrz" ? (r.qrz?.message || "no QRZ logbook key (Settings ▸ Log)") : r.message;
    toast(ok ? `${r.entry.call} logged to ${where}.` : `${r.entry.call} saved locally — ${where}: ${msg}`, !ok);
    if (ok) { btn.classList.add("done"); btn.dataset.doneFor = r.entry.call.toUpperCase(); }
    loadRecent(); loadLogStatus();
  } catch { /* toast */ } finally { btn.disabled = false; }
}
for (const id of LOG_BTNS) $(id).onclick = () => logTo($(id));
let selRecent = null;                       // ts of the marked recent QSO
async function loadRecent() {
  let list;
  try { list = await api("/api/log/recent"); } catch { return; }
  $("recent-body").innerHTML = list.map((e) => `<tr data-ts="${esc(e.ts)}"${e.ts === selRecent ? ' class="sel"' : ""}>
    <td>${esc(e.ts.slice(11, 16))}</td><td class="msg">${esc(e.call)}</td><td>${esc(e.band)}</td>
    <td>${esc(e.mode)}</td><td>${esc(e.rst_sent)}</td><td>${esc(e.rst_rcvd)}</td><td>${esc(e.name)}</td>
    <td class="dx">${esc(e.country)}</td>
    <td class="${e.wavelog == null ? "" : e.wavelog ? "ok" : "bad"}" title="${esc(e.message)}${e.auto ? " (Auto-Log)" : ""}">${e.wavelog == null ? "—" : e.wavelog ? "✓" : "✗"}${e.auto ? " A" : ""}</td>
    <td class="${e.qrz == null ? "" : e.qrz ? "ok" : "bad"}" title="${esc(e.qrz_message || "no QRZ logbook key")}">${e.qrz == null ? "—" : e.qrz ? "✓" : "✗"}</td>
    <td><button class="del" data-ts="${esc(e.ts)}" title="Remove from the local list (not from Wavelog)">✕</button></td></tr>`).join("");
  for (const b of $("recent-body").querySelectorAll(".del")) {
    b.onclick = async (ev) => {
      ev.stopPropagation();                   // not a row click
      await request("DELETE", "/api/log/recent/" + encodeURIComponent(b.dataset.ts)); loadRecent();
    };
  }
  // mark a row → QRZ.com / map popups (as for a marked session)
  const marked = () => list.find((e) => e.ts === selRecent) || null;
  const sync = () => {
    for (const tr of $("recent-body").children) tr.classList.toggle("sel", tr.dataset.ts === selRecent);
    $("recent-qrz").disabled = $("recent-map").disabled = !marked();
  };
  for (const tr of $("recent-body").children) tr.onclick = () => { selRecent = tr.dataset.ts; sync(); };
  $("recent-qrz").onclick = () => { const e = marked(); if (e) openQrz(e.call); };
  $("recent-map").onclick = () => { const e = marked(); if (e) openMap({ call: e.call, grid: e.gridsquare || "" }); };
  sync();
}
async function loadLogStatus() {
  let s;
  try { s = await api("/api/log/status"); } catch { return; }
  logCfg = s;
  // LED + text, as CAT / USB AUDIO in the title bar
  $("l-wl").classList.toggle("online", !!s.wavelog_ready);
  $("l-qrz").classList.toggle("online", !!s.qrz_ready);
  $("l-auto").classList.toggle("online", !!s.auto_log);
  $("sess-autolog").classList.toggle("on", !!s.auto_log);
  const w = s.worked;
  $("worked-stat").textContent = w.synced_at
    ? `Index: ${w.calls} calls · ${w.entities} DXCC · updated ${new Date(w.synced_at * 1000).toLocaleTimeString("en-GB")}`
    : "Index empty — set up Wavelog in Settings ▸ Log";
  $("log-stat").textContent = s.sync_error ? "Sync error: " + s.sync_error
    : s.wavelog_ready ? "" : "Wavelog not configured";
}
$("log-sync").onclick = async () => {
  try { const r = await api("/api/log/sync", {}); toast(`Wavelog: +${r.new} QSOs, ${r.calls} calls in the index.`); } catch { /* toast */ }
  loadLogStatus();
};

async function loadLogSettings() {
  await loadLogStatus();
  const s = logCfg;
  if (!s) return;
  $("wl-url").value = s.wavelog_url || "";
  $("wl-key").value = "";
  $("wl-key").placeholder = s.wavelog_key_set ? "saved — leave empty to keep" : "read/write API-Key";
  $("qrz-user").value = s.qrz_username || "";
  $("qrz-pass").value = "";
  $("qrz-pass").placeholder = s.qrz_password_set ? "saved — leave empty to keep" : "";
  $("qrz-key").value = "";
  $("qrz-key").placeholder = s.qrz_api_key_set ? "saved — leave empty to keep" : "";
  $("qrz-before").checked = s.qrz_before_log;
  $("auto-log").checked = s.auto_log;
  $("auto-log-qrz").checked = !!s.auto_log_qrz;
  $("auto-log-qrz").disabled = !s.qrz_upload_ready && !s.auto_log_qrz;   // no API key: nothing to log to
  await loadStations(s.wavelog_station_id);
}
async function loadStations(selected) {
  const sel = $("wl-station");
  let st = [];
  try { st = await api("/api/log/stations"); } catch { /* none */ }
  sel.innerHTML = st.length ? "" : `<option value="${esc(selected || "")}">${selected ? "Profile " + esc(selected) : "—"}</option>`;
  for (const s of st) {
    const o = document.createElement("option");
    o.value = s.id;
    o.textContent = `${s.name} — ${s.callsign} ${s.grid}`;
    sel.appendChild(o);
  }
  if (selected) sel.value = selected;
  return st.length;
}
function logSettingsBody() {
  return {
    wavelog_url: $("wl-url").value.trim(), wavelog_key: $("wl-key").value,
    wavelog_station_id: $("wl-station").value, qrz_username: $("qrz-user").value.trim(),
    qrz_password: $("qrz-pass").value, qrz_api_key: $("qrz-key").value,
    qrz_before_log: $("qrz-before").checked, auto_log: $("auto-log").checked,
    auto_log_qrz: $("auto-log-qrz").checked,
  };
}
function setStatusLine(id, ok, text) {
  const el = $(id);
  el.textContent = text;
  el.classList.toggle("ok", ok === true);
  el.classList.toggle("bad", ok === false);
}
// Save first (the test runs against what is stored), then test; every
// failure — also a rejected save — lands visibly in the section's status line.
async function saveThenTest(statusId, pick) {
  setStatusLine(statusId, null, "testing …");
  try {
    await api("/api/log/config", logSettingsBody());
    const r = await api("/api/log/test", {});
    const lines = pick(r).map(([label, x]) => `${label}: ${x.ok ? "✓" : "✗"} ${x.message}`);
    setStatusLine(statusId, pick(r).every(([, x]) => x.ok), lines.join("  ·  "));
    loadLogStatus();
    return r;
  } catch (e) {
    setStatusLine(statusId, false, "✗ " + e.message);
    return null;
  }
}
$("wl-test").onclick = async () => {
  const r = await saveThenTest("wl-status", (x) => [["Wavelog", x.wavelog]]);
  if (r?.wavelog.ok) await loadStations($("wl-station").value);
};
$("qrz-test").onclick = () => saveThenTest("qrz-status", (x) => [
  ...(/missing/i.test(x.qrz.message) && x.qrz_logbook.ok ? [] : [["Lookup", x.qrz]]),
  ...(/^no /i.test(x.qrz_logbook.message) && x.qrz.ok ? [] : [["Logbook", x.qrz_logbook]])]);
$("wl-reload").onclick = async () => {
  try {
    await api("/api/log/config", logSettingsBody());
    const n = await loadStations($("wl-station").value);
    setStatusLine("wl-status", n > 0, n ? `${n} profile(s) loaded — pick one and SAVE` : "no profiles — check URL/token (Test connection)");
  } catch (e) { setStatusLine("wl-status", false, "✗ " + e.message); }
};
$("log-save").onclick = async () => {
  try { await api("/api/log/config", logSettingsBody()); toast("Log settings saved."); loadLogSettings(); } catch { /* toast */ }
};
$("log-resync").onclick = async () => {
  $("log-sync-out").textContent = "loading …";
  try {
    const r = await api("/api/log/sync?full=true", {});
    $("log-sync-out").textContent = `${r.qsos} QSOs · ${r.calls} calls · ${r.entities} DXCC`;
  } catch (e) { $("log-sync-out").textContent = e.message; }
  loadLogStatus();
};

// ---------------------------------------------------------------- peak hold
// A white tick at the highest recent value: held 1.5 s, then falls ~25 %/s —
// how a hardware meter's peak memory behaves.
function peakHold(bar) {
  const pk = document.createElement("b");
  pk.className = "pk";
  bar.appendChild(pk);
  let peak = 0, at = 0, last = performance.now();
  return {
    set(pct) {
      const now = performance.now();
      if (pct >= peak) { peak = pct; at = now; }
      else if (now - at > 1500) peak = Math.max(pct, peak - (now - last) / 1000 * 25);
      last = now;
      pk.style.left = `calc(${peak.toFixed(1)}% - 1px)`;
    },
  };
}
const lvlPeak = peakHold($("lvl-bar").parentElement);

// ---------------------------------------------------------------- memories 1-20 (PTT panel)
const MEM_KEYS = 32;                     // 16 per side, 2 × 8
let mems = [];
(function buildMemKeys() {
  for (let ch = 1; ch <= MEM_KEYS; ch++) {
    const b = document.createElement("button");
    b.className = "qbtn empty";
    b.dataset.ch = ch;
    b.innerHTML = `M${ch}<small>—</small>`;
    b.title = `M${ch}: click = recall, hold 3 s = store VFO A here`;
    let holdTimer = null, held = false;
    b.addEventListener("pointerdown", () => {
      held = false;
      b.classList.add("holding");
      holdTimer = setTimeout(async () => {
        held = true;
        b.classList.remove("holding");
        await memoryStore(ch);
      }, 3000);
    });
    const cancel = () => { clearTimeout(holdTimer); b.classList.remove("holding"); };
    for (const ev of ["pointerup", "pointerleave", "pointercancel"]) b.addEventListener(ev, cancel);
    b.addEventListener("contextmenu", (e) => e.preventDefault());
    b.onclick = async () => {
      if (held) { held = false; return; }        // the long press already stored
      if (b.classList.contains("empty")) return;
      try {
        const m = await api("/api/memory/recall", { channel: ch });
        toast(`M${ch} → VFO A: ${(m.freq_hz / 1e6).toFixed(3)} MHz ${m.mode}` +
          (m.split ? ` · split, TX ${(m.vfo_b_hz / 1e6).toFixed(3)} MHz` : m.vfo_b_hz ? ` · VFO B ${(m.vfo_b_hz / 1e6).toFixed(3)}` : ""));
      } catch { /* toast */ }
    };
    (ch <= MEM_KEYS / 2 ? $("quick-mem") : $("quick-mem2")).appendChild(b);
  }
})();
// Memory set: long press stores VFO A (frequency + mode) into the channel (MW, read back)
async function memoryStore(ch) {
  const v = radio.vfo;
  if (!v?.freq_hz) { toast("Radio not connected", true); return; }
  const m = mems.find((x) => x.channel === ch);
  const desc = `${(v.freq_hz / 1e6).toFixed(3)} MHz ${v.mode}`;
  if (m && !m.empty && !(m.freq_hz === v.freq_hz && m.mode === v.mode) &&
      !(await ask(`M${ch} holds ${(m.freq_hz / 1e6).toFixed(3)} MHz ${m.mode}. Overwrite it with ${desc}?`,
                  { title: `Overwrite M${ch}?`, yes: "OVERWRITE", danger: true }))) return;
  try {
    await api("/api/memory/write", { channel: ch, freq_hz: v.freq_hz, mode: v.mode });
    toast(`M${ch} set: ${desc}`);
    loadMemories();
  } catch { /* toast */ }
}
async function loadMemories(tries = 3) {
  try { mems = await api(`/api/memories?start=1&end=${MEM_KEYS}`); }
  catch {
    // measured on a cold rig: it identifies itself before it answers every MR,
    // so one failed read is not an empty memory bank
    if (tries > 1) setTimeout(() => loadMemories(tries - 1), 1500);
    return;
  }
  for (const m of mems) {
    const b = document.querySelector(`.qbtn[data-ch="${m.channel}"]`);
    if (!b) continue;
    b.classList.toggle("empty", !!m.empty);
    const sub = m.label ? esc(m.label) : m.empty ? "empty" : esc(m.mode);
    b.innerHTML = m.empty ? `M${m.channel}<small>${sub}</small>`
      : `M${m.channel} ${(m.freq_hz / 1e6).toFixed(3)}<small>${sub}</small>`;
    b.title = m.empty ? `Memory ${m.channel} is empty` : `Memory ${m.channel} → VFO A: ${(m.freq_hz / 1e6).toFixed(6)} MHz ${m.mode}` +
      (m.vfo_b_hz ? ` · VFO B ${(m.vfo_b_hz / 1e6).toFixed(6)} MHz` : "") + (m.split ? " · SPLIT (TX on B)" : "") +
      (m.ctcss ? ` · CTCSS ${m.ctcss === 2 ? "ENC" : "TSQ"} ${ctcssHz(m.tone)}` : "") + "\nclick = recall, hold 3 s = store VFO A here";
  }
  if (radio.vfo) highlightMem(radio.vfo);
}
function highlightMem(v) {
  for (const m of mems) {
    const b = document.querySelector(`.qbtn[data-ch="${m.channel}"]`);
    if (b) b.classList.toggle("active", !m.empty && m.freq_hz === v.freq_hz && m.mode === v.mode);
  }
}

// DIGI strip: RX level on the Pi — software gain after the codec (the radio is not touched)
(function rxLevel() {
  const el = $("rx-level"); let timer;
  const fmt = (v) => `${v > 0 ? "+" : ""}${(+v).toFixed(0)} dB`;
  const show = (v) => { el.value = v; $("rx-level-val").textContent = fmt(v); };
  api("/api/audio/rxlevel").then((r) => show(r.db)).catch(() => {});
  el.addEventListener("input", () => {
    $("rx-level-val").textContent = fmt(el.value);
    clearTimeout(timer);
    timer = setTimeout(() => api("/api/audio/rxlevel", { db: +el.value }).catch(() => {}), 200);
  });
  el.addEventListener("dblclick", () => { show(0); api("/api/audio/rxlevel", { db: 0 }).catch(() => {}); });
})();

// Default memory plan, 32 keys (Diamond CP6 bands 80/40/20/15/10/6 m), one band per key row:
// left keys SSB | CW (IARU R1 QRP centres = usual calling frequencies; 6 m: SSB/CW calling),
// right keys FT8 | FT4. Rows 7-8: 10 m / 6 m FM, 10 m repeater (split, input −100 kHz), SSTV;
// IARU R1 emergency centres. [channel, Hz, mode, label, extra]
const DEFAULT_MEMS = [
  [1, 3690000, "LSB", "SSB 80m"], [2, 3560000, "CW", "CW 80m"],
  [3, 7090000, "LSB", "SSB 40m"], [4, 7030000, "CW", "CW 40m"],
  [5, 14285000, "USB", "SSB 20m"], [6, 14060000, "CW", "CW 20m"],
  [7, 21285000, "USB", "SSB 15m"], [8, 21060000, "CW", "CW 15m"],
  [9, 28360000, "USB", "SSB 10m"], [10, 28060000, "CW", "CW 10m"],
  [11, 50150000, "USB", "SSB 6m"], [12, 50090000, "CW", "CW 6m"],
  [13, 29600000, "FM", "FM 10m"], [14, 29640000, "FM-N", "RPT 10m", { split: true, vfo_b_hz: 29540000 }],
  [15, 51510000, "FM", "FM 6m"], [16, 14230000, "USB", "SSTV 20m"],
  [17, 3573000, "DATA-U", "FT8 80m"], [18, 3575000, "DATA-U", "FT4 80m"],
  [19, 7074000, "DATA-U", "FT8 40m"], [20, 7047500, "DATA-U", "FT4 40m"],
  [21, 14074000, "DATA-U", "FT8 20m"], [22, 14080000, "DATA-U", "FT4 20m"],
  [23, 21074000, "DATA-U", "FT8 15m"], [24, 21140000, "DATA-U", "FT4 15m"],
  [25, 28074000, "DATA-U", "FT8 10m"], [26, 28180000, "DATA-U", "FT4 10m"],
  [27, 50313000, "DATA-U", "FT8 6m"], [28, 50318000, "DATA-U", "FT4 6m"],
  [29, 3760000, "LSB", "EMCOM 80m"], [30, 7110000, "LSB", "EMCOM 40m"],
  [31, 14300000, "USB", "EMCOM 20m"], [32, 21360000, "USB", "EMCOM 15m"]];
$("mem-defaults").onclick = async () => {
  if (!(await ask("M1-M32 are replaced by the default plan: SSB/CW calling frequencies, FT8/FT4, FM, "
                  + "repeater, SSTV and the IARU R1 emergency centres for 80/40/20/15/10/6 m.",
                  { title: "Overwrite all 32 memories?", yes: "OVERWRITE ALL", danger: true }))) return;
  const b = $("mem-defaults");
  b.disabled = true;
  let ok = 0;
  for (const [channel, freq_hz, mode, label, x] of DEFAULT_MEMS) {
    $("mem-status").textContent = `writing M${channel} …`;
    const body = { channel, freq_hz, mode, label, split: !!x?.split, vfo_b_hz: x?.vfo_b_hz || 0 };
    if (mode.startsWith("FM")) Object.assign(body, { ctcss: 0, tone: 8 });
    try { await api("/api/memory/write", body); ok++; } catch { break; }
  }
  b.disabled = false;
  toast(ok === DEFAULT_MEMS.length ? "M1-M32 set to the default plan" : `Stopped after ${ok} of ${DEFAULT_MEMS.length} memories`, ok !== DEFAULT_MEMS.length);
  loadMemEditor();
  loadMemories();
};

// FLT / IPO / R.FLT / AGC in the VFO title bar: click = next value (the LCD shows the state)
function showHeadQuick() {}
function headQuick(id, values, current, send) {
  const next = values[(values.indexOf(current) + 1) % values.length];
  send(next).then(() => toast(`${$(id).textContent.trim()} → ${next}`)).catch(() => {});
}
// the CLAR group: ON switches the RX clarifier, CLR and a double-click on the
// slider return the offset to 0
$("clar-rx").onclick = () => {
  const on = !$("clar-rx").classList.contains("on");
  clarLit(on);
  api("/api/clar", { on }).catch(() => { touched.delete("clar-rx"); });
};
const clarZero = () => {
  touched.set("clar-off", Date.now());
  $("clar-off").value = 0;
  $("clar-off-val").textContent = fmtClar(0);
  api("/api/clar/clear", {}).catch(() => {});
};
$("clar-zero").onclick = clarZero;
$("clar-off").addEventListener("dblclick", clarZero);
$("hq-flt").onclick = () => headQuick("hq-flt", ["THRU", "VRF"], radio.vrf ? "VRF" : "THRU",
  (v) => api("/api/vrf", { on: v === "VRF" }));
$("hq-ipo").onclick = () => {
  const names = Object.values(meta?.preamp || {}), byName = Object.fromEntries(Object.entries(meta?.preamp || {}).map(([k, v]) => [v, +k]));
  headQuick("hq-ipo", names, radio.preamp, (v) => api("/api/preamp", { level: byName[v] }));
};
$("hq-rflt").onclick = () => headQuick("hq-rflt", meta?.roofing || [], (radio.roofing || "").replace(/^AUTO-.*/, "AUTO"),
  (v) => api("/api/roofing", { name: v }));
$("hq-agc").onclick = () => headQuick("hq-agc", meta?.agc || [], (radio.agc || "").replace(/^AUTO-.*/, "AUTO"),
  (v) => api("/api/agc", { name: v }));

// LCD signal path: every value is a fixed cell, only the active ones light up
function litCells(id, values) {
  for (const c of $(id).children) c.classList.toggle("on", values.includes(c.dataset.v));
}

// LCD mode lamps (fixed, lit — never rewritten), as on the FT-950 display
const MODE_LAMPS = {
  LSB: ["LSB"], USB: ["USB"], CW: ["CW"], "CW-R": ["CW", "REV"], AM: ["AM"], "AM-N": ["AM"],
  FM: ["FM"], "FM-N": ["FM"], "RTTY-L": ["RTTY", "LSB"], "RTTY-U": ["RTTY", "USB"],
  "DATA-L": ["PKT", "LSB"], "DATA-U": ["PKT", "USB"], "PKT-FM": ["PKT", "FM"],
};
function showModeLamps(mode) {
  const lit = MODE_LAMPS[mode] || [];
  for (const l of $("mode-lamps").children) l.classList.toggle("on", lit.includes(l.dataset.m));
  $("mode-lamps").title = `Mode: ${mode || "—"}`;
}

// CTCSS next to the mode row (CT mode, CN tone number; the radio makes the tone)
function showCtcss(mode, tone) {
  if (Date.now() - (touched.get("ctcss") || 0) < 2000) return;
  if (mode != null) $("ctcss-mode").value = String(mode);
  if (tone != null && $("ctcss-tone").options.length) $("ctcss-tone").value = String(tone);
  $("ctcss-mode").closest(".ctcss").classList.toggle("on", !!mode);
}
api("/api/ctcss/tones").then((tones) => {
  $("ctcss-tone").innerHTML = tones.map((hz, i) => `<option value="${i}">${hz.toFixed(1)} Hz</option>`).join("");
  if (radio.ctcss_tone != null) $("ctcss-tone").value = String(radio.ctcss_tone);
}).catch(() => {});
for (const [id, key] of [["ctcss-mode", "mode"], ["ctcss-tone", "tone"]]) {
  $(id).onchange = async (e) => {
    touched.set("ctcss", Date.now());
    $("ctcss-mode").closest(".ctcss").classList.toggle("on", $("ctcss-mode").value !== "0");
    try { await api("/api/ctcss", { [key]: +e.target.value }); } catch { touched.delete("ctcss"); }
  };
}

// Settings ▸ Memories: edit what the quick keys hold (radio channel + local name)
let memModes = null, ctcssTones = null;
const ctcssHz = (i) => (ctcssTones?.[i] != null ? `${ctcssTones[i].toFixed(1)} Hz` : `tone ${i}`);
async function loadMemEditor() {
  $("mem-status").textContent = "reading …";
  try {
    memModes ??= await api("/api/memory/modes");
    ctcssTones ??= await api("/api/ctcss/tones");
    await loadMemories();
  } catch (e) { $("mem-status").textContent = e.message; return; }
  const list = $("mem-list");
  list.innerHTML = `<div class="mem-row mem-head"><span>CH</span><span>MHz</span><span>MODE</span><span>CTCSS</span><span>TONE</span>
    <span>SPLIT</span><span>VFO B</span><span>NAME</span><span></span><span></span></div>`;
  for (const m of mems) {
    const row = document.createElement("div");
    row.className = "mem-row" + (m.empty ? " empty" : "");
    const opts = ["", ...memModes].map((x) => `<option${x === (m.empty ? "" : m.mode) ? " selected" : ""}>${x}</option>`).join("");
    const ct = m.empty ? 0 : m.ctcss, tn = m.empty ? 8 : m.tone;
    const ctOpts = [[0, "OFF"], [2, "ENC"], [1, "TSQ"]].map(([v, t]) => `<option value="${v}"${v === ct ? " selected" : ""}>${t}</option>`).join("");
    const toneOpts = (ctcssTones || []).map((hz, i) => `<option value="${i}"${i === tn ? " selected" : ""}>${hz.toFixed(1)}</option>`).join("");
    row.innerHTML = `<span class="mn">M${m.channel}</span>
      <input class="mf" inputmode="decimal" placeholder="MHz" title="Frequency in MHz" value="${m.empty ? "" : (m.freq_hz / 1e6).toFixed(6)}">
      <select class="mm" title="Mode">${opts}</select>
      <select class="mc" title="CTCSS, stored in the radio's memory and set on recall in FM: ENC = send the tone, TSQ = send and open the squelch only on it">${ctOpts}</select>
      <select class="mt" title="CTCSS tone (Hz)">${toneOpts}</select>
      <label class="ms" title="Split: transmit on VFO B (stored here on the Pi — the radio's memories have no split)"><input type="checkbox"${m.split ? " checked" : ""}></label>
      <input class="mb" inputmode="decimal" placeholder="—" title="VFO B in MHz, set on recall (A's mode; stored here on the Pi). Empty = VFO B is left alone" value="${m.vfo_b_hz ? (m.vfo_b_hz / 1e6).toFixed(6) : ""}">
      <input class="ml" maxlength="12" placeholder="name" title="Shown on the key; stored here, not in the radio" value="${esc(m.label)}">
      <button class="mv" title="Fill in VFO A's frequency and mode, VFO B and split">◀ VFO</button>
      <button class="mw" title="Write to the radio">WRITE</button>`;
    const q = (c) => row.querySelector(c);
    const [f, md, lb, mc, mt, ms, mb] = [q(".mf"), q(".mm"), q(".ml"), q(".mc"), q(".mt"), q(".ms input"), q(".mb")];
    const mhz = (el) => Math.round(parseFloat(el.value.replace(",", ".")) * 1e6);
    const hz = () => mhz(f);
    const bHz = () => (mb.value.trim() ? mhz(mb) : 0);
    const ctcssChanged = () => !m.empty && (+mc.value !== m.ctcss || +mt.value !== m.tone);
    const radioChanged = () => ctcssChanged() || !(m.empty ? !f.value && !md.value : hz() === m.freq_hz && md.value === m.mode);
    const extraChanged = () => ms.checked !== !!m.split || bHz() !== (m.vfo_b_hz || 0);
    const mark = () => {
      mt.disabled = mc.value === "0";
      row.classList.toggle("changed", radioChanged() || extraChanged() || lb.value.trim() !== (m.label || ""));
    };
    mt.disabled = mc.value === "0";
    for (const el of [f, md, lb, mc, mt, ms, mb]) { el.oninput = mark; el.onchange = mark; }
    lb.onkeydown = f.onkeydown = mb.onkeydown = (e) => { if (e.key === "Enter") q(".mw").click(); };
    q(".mv").onclick = () => {
      const v = radio.vfo;
      if (!v) return;
      f.value = (v.freq_hz / 1e6).toFixed(6);
      md.value = memModes.includes(v.mode) ? v.mode : "";
      if (radio.other) {
        ms.checked = radio.rx_vfo !== radio.tx_vfo;
        mb.value = ms.checked ? (radio.other.freq_hz / 1e6).toFixed(6) : "";
      }
      mark();
    };
    q(".mw").onclick = async () => {
      const body = { channel: m.channel };
      if (radioChanged()) {
        if (!(hz() >= 30000 && hz() <= 56e6) || !md.value) { toast(`M${m.channel}: frequency (0.03-56 MHz) and mode needed`, true); return; }
        Object.assign(body, { freq_hz: hz(), mode: md.value, ctcss: +mc.value, tone: +mt.value });
      }
      if (extraChanged()) {
        const b = bHz();
        if (b && !(b >= 30000 && b <= 56e6)) { toast(`M${m.channel}: VFO B 0.03-56 MHz or empty`, true); return; }
        if (ms.checked && !b) { toast(`M${m.channel}: split needs a VFO B frequency`, true); return; }
        Object.assign(body, { split: ms.checked, vfo_b_hz: b });
      }
      if (lb.value.trim() !== (m.label || "")) body.label = lb.value.trim();
      row.classList.add("busy");
      try {
        await api("/api/memory/write", body);
        toast(`M${m.channel} saved`);
        await loadMemEditor();
        loadMemories();
      } catch { row.classList.remove("busy"); }
    };
    list.appendChild(row);
  }
  $("mem-status").textContent = `${mems.filter((m) => !m.empty).length} of ${mems.length} channels in use`;
}

// ---------------------------------------------------------------- PTT (as TM-V71 Remote)
const pttBtn = $("ptt"), lockBtn = $("ptt-lock");
let keyed = false, pttLock = false, leaseTimer = null, pttSince = 0;
const VOICE_MAX_S = 180;
function pttTimerTick() {
  if (!keyed) return;
  const s = (Date.now() - pttSince) / 1000;
  $("ptt-timer").textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  pttBtn.style.setProperty("--sweep", Math.min(360, s / VOICE_MAX_S * 360).toFixed(1));
  requestAnimationFrame(pttTimerTick);
}
function showKeyed(on) {
  pttBtn.classList.toggle("tx", on);
  if (on && !keyed) { pttSince = Date.now(); keyed = true; requestAnimationFrame(pttTimerTick); }
  if (!on) { keyed = false; $("ptt-timer").textContent = "00:00"; pttBtn.style.setProperty("--sweep", 0); }
  micGate();
}
async function key(on) {
  if (on && !audioUp()) { toast("Connect audio first (CONNECT) — no PTT without a microphone", true); return; }
  if (on === keyed) return;
  clearInterval(leaseTimer);
  if (on) {
    showKeyed(true);
    try { await api("/api/ptt", { on: true, peer: myPeer }); }
    catch { showKeyed(false); setLock(false); return; }
    // the backend releases PTT if this keep-alive stops for 2.5 s
    leaseTimer = setInterval(() => api("/api/ptt", { on: true, peer: myPeer }).catch(() => { clearInterval(leaseTimer); showKeyed(false); setLock(false); }), 1000);
  } else {
    showKeyed(false);
    try { await api("/api/ptt", { on: false }); } catch { /* toast */ }
  }
}
function setLock(on) {
  pttLock = on;
  lockBtn.classList.toggle("locked", on);
  lockBtn.setAttribute("aria-pressed", String(on));
  lockBtn.textContent = on ? "PTT-LOCK · ON" : "PTT-LOCK";
}
lockBtn.onclick = () => { const on = !pttLock; if (on && !audioUp()) { key(true); return; } setLock(on); key(on); };
pttBtn.addEventListener("pointerdown", (e) => { e.preventDefault(); pttBtn.setPointerCapture(e.pointerId); key(true); });
for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) pttBtn.addEventListener(ev, () => { if (!pttLock) key(false); });
pttBtn.addEventListener("contextmenu", (e) => e.preventDefault());
const typing = () => /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName) || modal.classList.contains("open");
window.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !e.repeat && !typing()) { e.preventDefault(); key(true); }
});
window.addEventListener("keyup", (e) => {
  if (e.code !== "Space" || typing()) return;
  e.preventDefault();
  if (!pttLock) key(false);
});
// leaving the page must never leave the radio keyed
for (const ev of ["blur", "pagehide"]) window.addEventListener(ev, () => { if (keyed && !pttLock) key(false); });
document.addEventListener("visibilitychange", () => { if (document.hidden && keyed) { setLock(false); key(false); } });
function showRoger(on) {
  $("btn-roger").classList.toggle("on", !!on);
  $("btn-roger").setAttribute("aria-pressed", String(!!on));
}
$("btn-roger").onclick = async () => {
  try { showRoger((await api("/api/audio/voice", { roger: !$("btn-roger").classList.contains("on") })).roger); } catch { /* toast */ }
};
// 1750 Hz tone call (FM repeaters), as TM-V71 Remote: the button arms the tone,
// PTT sends it instead of the microphone, releasing PTT disarms it (the level
// stream reports the state, so the button follows on its own)
let t1750At = 0;
function show1750(on) {
  $("btn-1750").classList.toggle("on", !!on);
  $("btn-1750").setAttribute("aria-pressed", String(!!on));
}
$("btn-1750").onclick = async () => {
  const want = !$("btn-1750").classList.contains("on");
  t1750At = Date.now(); show1750(want);
  try { show1750((await api("/api/tx/tone1750", { on: want })).tone_1750); }
  catch { show1750(false); }
};

// ---------------------------------------------------------------- audio: WebRTC / Opus (as TM-V71 Remote)
let pc = null, micStream = null, statsTimer = null, lastStats = null, myPeer = null;
const audioUp = () => !!pc && pc.connectionState === "connected" && !!micStream;
// the microphone stays muted (silence over WebRTC) unless this browser is
// transmitting or running the mic test
let micOpenForTest = false;
function micGate() {
  const open = keyed || micOpenForTest;
  if (micStream) for (const tr of micStream.getAudioTracks()) tr.enabled = open;
}
function setAudioUi(state) {
  const b = $("audio-toggle");
  b.classList.toggle("connected", state === "up");
  b.classList.toggle("busy", state === "busy");
  b.textContent = state === "up" ? "DISCONNECT" : state === "busy" ? "CONNECTING …" : "CONNECT";
  $("audio-stat").textContent = state === "up" ? (micStream ? "" : "RX only (no microphone)") : "";
  pttBtn.disabled = state !== "up" || !micStream;
  lockBtn.disabled = pttBtn.disabled;
}
// The browser's Opus encoder follows the answer's maxaveragebitrate: without it
// Chrome sends the mic at ~14-32 kbit/s, which cuts the treble (muffled voice,
// also in MIC TEST). 64 kbit/s mono keeps the full voice band.
function opusHiFi(sdp) {
  const pt = (sdp.match(/a=rtpmap:(\d+) opus\/48000/i) || [])[1];
  if (!pt) return sdp;
  const want = "maxaveragebitrate=64000;stereo=0;useinbandfec=1";
  const re = new RegExp(`a=fmtp:${pt} ([^\r\n]*)`);
  if (re.test(sdp)) {
    return sdp.replace(re, (m, p) => `a=fmtp:${pt} ` +
      p.split(";").filter((kv) => !/^(maxaveragebitrate|stereo|useinbandfec)=/.test(kv.trim())).concat(want).join(";"));
  }
  return sdp.replace(new RegExp(`(a=rtpmap:${pt} opus\/48000[^\r\n]*\r?\n)`, "i"), `$1a=fmtp:${pt} ${want}\r\n`);
}
// Microphone, as TM-V71 Remote takes it (measured there):
//  • Desktop: leave echo cancellation / noise suppression / auto gain at the
//    browser default. Forcing them off makes the browser (macOS, all browsers)
//    reconfigure the input and drop the level by ~40 dB — measured here as
//    speech at −72 dBFS on the Pi, a near-silent, muffled MIC TEST.
//  • Android / installed PWA on a phone: force them off, or a Bluetooth
//    headset switches to mono HFP and playback leaves the headset.
//  • Prefer a built-in (non-Bluetooth) input: a headset microphone runs on
//    narrow-band HFP (8/16 kHz) and sounds dull.
// localStorage "ft950.micMode" = "auto" | "off" overrides the default.
const isTouchMac = () => /Mac/i.test(navigator.platform || navigator.userAgent || "") && (navigator.maxTouchPoints || 0) > 1;
const isIos = () => /iPhone|iPad|iPod/i.test(navigator.userAgent || "") || isTouchMac();
const isMobile = () => /Android|iPhone|iPad|iPod/i.test(navigator.userAgent || "") || isTouchMac()
  || window.matchMedia("(display-mode: standalone)").matches;
function micMode() {
  try { const v = localStorage.getItem("ft950.micMode"); if (v === "auto" || v === "off") return v; } catch { /* default */ }
  return isMobile() && !isIos() ? "off" : "auto";
}
async function pickNonBtMicId() {
  try {
    const ins = (await navigator.mediaDevices.enumerateDevices())
      .filter((d) => d.kind === "audioinput" && d.deviceId && d.deviceId !== "default");
    const isBt = (l) => /bluetooth|headset|hands?-?free|hfp|sco|airpod|buds|wh-|wf-/i.test(l || "");
    const builtin = ins.find((d) => /built|internal|phone|handset|mic|mikrofon/i.test(d.label) && !isBt(d.label));
    const nonBt = builtin || ins.find((d) => d.label && !isBt(d.label));
    return nonBt ? nonBt.deviceId : null;
  } catch { return null; }
}
async function captureMic() {
  const base = micMode() === "off"
    ? { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 }
    : { channelCount: 1 };
  let id = await pickNonBtMicId();                 // known once permission was given
  let stream = await navigator.mediaDevices.getUserMedia({ audio: id ? { ...base, deviceId: { exact: id } } : base });
  if (!id) {                                       // first run: labels exist now — switch off a headset mic
    const id2 = await pickNonBtMicId(), cur = stream.getAudioTracks()[0]?.getSettings?.().deviceId;
    if (id2 && id2 !== cur) {
      stream.getTracks().forEach((t) => t.stop());
      stream = await navigator.mediaDevices.getUserMedia({ audio: { ...base, deviceId: { exact: id2 } } });
    }
  }
  return stream;
}
async function audioConnect() {
  specStart();                                 // AudioContext needs the click's user gesture
  setAudioUi("busy");
  try {
    try {
      micStream = await captureMic();
    } catch (e) {
      micStream = null;
      toast("No microphone (" + e.name + ") — receive only. The microphone needs HTTPS and permission in the browser.", true);
    }
    pc = new RTCPeerConnection();
    if (micStream) specAttach("mic", micStream);
    micGate();
    if (micStream) for (const tr of micStream.getAudioTracks()) pc.addTrack(tr, micStream);
    else pc.addTransceiver("audio", { direction: "recvonly" });
    pc.ontrack = (e) => {
      const st = e.streams[0] || new MediaStream([e.track]);
      $("rx-audio").srcObject = st; $("rx-audio").play().catch(() => {});
      specAttach("rx", st);
    };
    pc.onconnectionstatechange = () => {
      if (pc?.connectionState === "connected") setAudioUi("up");
      if (["failed", "closed"].includes(pc?.connectionState)) { audioDisconnect(); toast("Audio connection lost", true); }
    };
    await pc.setLocalDescription(await pc.createOffer());
    await new Promise((res) => {               // non-trickle: wait for ICE gathering
      if (pc.iceGatheringState === "complete") return res();
      const t = setTimeout(res, 3000);
      pc.addEventListener("icegatheringstatechange", () => { if (pc.iceGatheringState === "complete") { clearTimeout(t); res(); } });
    });
    const ans = await api("/api/webrtc/offer", { sdp: pc.localDescription.sdp, type: pc.localDescription.type });
    myPeer = ans.peer;                         // names this browser's mic for PTT
    await pc.setRemoteDescription({ sdp: opusHiFi(ans.sdp), type: ans.type });
    statsTimer = setInterval(updateRate, 1000);
  } catch (e) {
    audioDisconnect();
    toast("Audio: " + e.message, true);
  }
}
function audioDisconnect() {
  if (keyed) { setLock(false); key(false); }
  if ($("mic-test").checked) setMicTest(false);
  clearInterval(statsTimer);
  lastStats = null;
  $("graph-bytes").textContent = "";
  if (pc) { try { pc.close(); } catch { /* closed */ } pc = null; }
  myPeer = null;
  if (micStream) { for (const tr of micStream.getTracks()) tr.stop(); micStream = null; }
  $("rx-audio").srcObject = null;
  specStop();
  setAudioUi("down");
}
$("audio-toggle").onclick = () => (pc ? audioDisconnect() : audioConnect());
// MUTE (until pressed again) and DROP (only the current over) — as TM-V71 Remote
let rxMuted = false, dropOn = false, dropQuietSince = 0, dropRef = -120, dropAt = 0, rxLevelNow = -120;
function applyRxMute() { $("rx-audio").muted = rxMuted || dropOn; }
$("rx-mute").onclick = () => {
  rxMuted = !rxMuted;
  $("rx-mute").classList.toggle("armed", rxMuted);
  $("rx-mute").setAttribute("aria-pressed", String(rxMuted));
  $("rx-mute").textContent = rxMuted ? "MUTED" : "MUTE";
  applyRxMute();
  threeToneChime(rxMuted);                 // muting = descending, release = ascending
};
function setDrop(on, chime = false) {
  dropOn = on;
  dropQuietSince = 0;
  dropRef = rxLevelNow;                   // the over's level when DROP was pressed
  dropAt = performance.now();
  $("sq-drop").classList.toggle("armed", on);
  $("sq-drop").setAttribute("aria-pressed", String(on));
  applyRxMute();
  if (chime) threeToneChime(false);
}
$("sq-drop").onclick = () => setDrop(!dropOn);
// called with every RX level (10 Hz): lift DROP when the station stops
function dropWatch(rxDb) {
  rxLevelNow = rxDb;
  if (!dropOn) return;
  const now = performance.now();
  if (radio.busy === false || now - dropAt > 60000) { setDrop(false, true); return; }   // squelch closed / 60 s cap
  // relative: on SSB the squelch is usually open and the noise floor varies,
  // so "over ended" = 8 dB below the level at the press, for 3 s
  if (rxDb > dropRef - 8) { dropQuietSince = 0; return; }           // still signal
  if (!dropQuietSince) dropQuietSince = now;
  else if (now - dropQuietSince > 3000) setDrop(false, true);        // 3 s without signal
}
// three-tone chime (as TM-V71 Remote), made in the browser so it can never be
// transmitted: down = muting, up = released
let chimeCtx = null;
function threeToneChime(down = false) {
  try {
    chimeCtx = chimeCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (chimeCtx.state === "suspended") chimeCtx.resume();
    const t0 = chimeCtx.currentTime + 0.02, notes = [659.3, 880.0, 1174.7];   // e5 · a5 · d6
    (down ? [...notes].reverse() : notes).forEach((f, i) => {
      const o = chimeCtx.createOscillator(), g = chimeCtx.createGain(), t = t0 + i * 0.11;
      o.type = "sine"; o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.16, t + 0.012);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.10);
      o.connect(g).connect(chimeCtx.destination);
      o.start(t); o.stop(t + 0.12);
    });
  } catch { /* no audio context */ }
}
async function updateRate() {
  if (!pc) return;
  const st = await pc.getStats();
  let rx = 0, tx = 0;
  st.forEach((r) => {
    if (r.type === "inbound-rtp" && r.kind === "audio") rx = r.bytesReceived;
    if (r.type === "outbound-rtp" && r.kind === "audio") tx = r.bytesSent;
  });
  const now = performance.now();
  if (lastStats) {
    const dt = (now - lastStats.t) / 1000;
    const k = (b, a) => ((b - a) * 8 / 1000 / dt).toFixed(0);
    $("graph-bytes").innerHTML = `<span class="gb-rx">↓ ${k(rx, lastStats.rx)} kbit/s</span><span class="gb-tx">↑ ${k(tx, lastStats.tx)} kbit/s</span>`;
  }
  lastStats = { t: now, rx, tx };
}
const fillPct = (el) => el.style.setProperty("--gpct", ((el.value - el.min) / (el.max - el.min) * 100) + "%");
function gainSlider(id, fmt, key2) {
  const el = $(id); let t2;
  el.addEventListener("input", () => {
    fillPct(el);
    touched.set(id, Date.now());
    $(id + "-val").textContent = fmt(+el.value);
    clearTimeout(t2);
    t2 = setTimeout(() => api("/api/audio/voice", { [key2]: +el.value }).catch(() => {}), 200);
  });
  return (v) => { if (Date.now() - (touched.get(id) || 0) > 1500) { el.value = v; $(id + "-val").textContent = fmt(v); fillPct(el); } };
}
const setRxGain = gainSlider("rx-gain", (v) => v + "×", "rx_gain");
const setTxGain = gainSlider("tx-gain", (v) => v.toFixed(1) + "×", "tx_gain");
const setTxBuf = gainSlider("tx-buffer", (v) => v + " ms", "tx_buffer_ms");
const setTail = gainSlider("ptt-tail", (v) => v + " ms", "ptt_tail_ms");
const setRxBuf = gainSlider("rx-buffer", (v) => v + " ms", "rx_buffer_ms");
// TX auto gain: the manual slider is locked and shows the live AGC factor
function showTxAuto(on, agc) {
  const b = $("tx-auto"), sl = $("tx-gain");
  b.checked = !!on;
  sl.disabled = !!on;
  sl.closest(".acg").classList.toggle("is-auto", !!on);
  if (on && agc != null) {
    sl.value = Math.min(+sl.max, Math.max(+sl.min, agc));
    $("tx-gain-val").textContent = agc.toFixed(1) + "×";
    fillPct(sl);
  }
}
$("tx-auto").onchange = async () => {
  const want = $("tx-auto").checked;
  try {
    const v = await api("/api/audio/voice", { tx_auto_gain: want });
    showTxAuto(v.tx_auto_gain, v.agc_gain);
    if (!v.tx_auto_gain) setTxGain(v.tx_gain);
  } catch { $("tx-auto").checked = !want; /* toast */ }
};

// ---- RX recorder (as TM-V71 Remote): REC · play · WAV download ------------
let recBlobUrl = null, recPoll = null;
function reflectRec(a) {
  const on = !!a.recording, secs = +a.rec_seconds || 0, has = secs > 0;
  $("rec-toggle").classList.toggle("on", on);
  $("rec-toggle").textContent = on ? "■ STOP" : "● REC";
  const m = Math.floor(secs / 60), s = Math.floor(secs % 60);
  $("rec-time").textContent = has ? `${m}:${String(s).padStart(2, "0")} · ${(a.rec_bytes / 1048576).toFixed(1)} MB` : "";
  $("rec-play").disabled = $("rec-dl").disabled = !has || on;
  clearInterval(recPoll);
  recPoll = on ? setInterval(() => api("/api/audio/record").then(reflectRec).catch(() => {}), 1000) : null;
}
api("/api/audio/record").then(reflectRec).catch(() => {});
$("rec-toggle").onclick = async () => {
  try { reflectRec(await api("/api/audio/record", { on: !$("rec-toggle").classList.contains("on") })); } catch { /* toast */ }
};
async function recBlob() {
  const r = await fetch("/api/audio/record.wav?t=" + Date.now());
  if (!r.ok) throw new Error("HTTP " + r.status);
  const b = await r.blob();
  if (b.size < 64) throw new Error("nothing recorded yet");
  return b;
}
{
  const au = $("rec-audio"), play = $("rec-play"), pt = $("rec-playtime");
  const fmt = (s) => { s = Math.max(0, Math.floor(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  const upd = () => {
    const playing = !au.paused && !au.ended;
    play.textContent = playing ? "■" : "▶";
    pt.hidden = !playing;
    if (playing) pt.textContent = `${fmt(au.currentTime)} / ${fmt(isFinite(au.duration) ? au.duration : 0)}`;
  };
  for (const ev of ["play", "pause", "ended", "timeupdate"]) au.addEventListener(ev, upd);
  play.onclick = async () => {
    if (!au.paused) { au.pause(); au.currentTime = 0; return; }
    try {
      if (recBlobUrl) URL.revokeObjectURL(recBlobUrl);
      recBlobUrl = URL.createObjectURL(await recBlob());
      au.src = recBlobUrl;
      au.play();
    } catch (e) { toast("Play: " + e.message, true); }
  };
  $("rec-dl").onclick = async () => {
    try {
      const url = URL.createObjectURL(await recBlob());
      const a = document.createElement("a"), d = new Date(), p = (n) => String(n).padStart(2, "0");
      a.href = url;
      a.download = `ft950-rx-${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}_${p(d.getUTCHours())}${p(d.getUTCMinutes())}${p(d.getUTCSeconds())}.wav`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    } catch (e) { toast("Download: " + e.message, true); }
  };
}

async function loadVoice() {
  try {
    const v = await api("/api/audio/voice");
    setRxGain(v.rx_gain); setTxGain(v.tx_gain); setTxBuf(v.tx_buffer_ms); setTail(v.ptt_tail_ms); setRxBuf(v.rx_buffer_ms);
    showRoger(v.roger);
    showTxAuto(v.tx_auto_gain, v.agc_gain);
    $("tx-comp").checked = !!v.tx_comp;
    $("tx-preemph").checked = !!v.tx_preemph;
  } catch { /* backend down */ }
}
for (const [id, key] of [["tx-comp", "tx_comp"], ["tx-preemph", "tx_preemph"]]) {
  $(id).onchange = async (e) => {
    try { await api("/api/audio/voice", { [key]: e.target.checked }); toast(`${e.target.parentElement.textContent.trim()} ${e.target.checked ? "on" : "off"}`); }
    catch { e.target.checked = !e.target.checked; }
  };
}

// mic echo test (TM-V71 Remote): record on the Pi, replay on switch-off
let micTestTimer = null;
let micTestAt = 0;
const micTestPending = () => Date.now() - micTestAt < 1500;
async function setMicTest(on) {
  micTestAt = Date.now();
  $("mic-test").checked = on;
  if (on && !audioUp()) { $("mic-test").checked = false; toast("Connect audio first (CONNECT) — the test runs through the Pi", true); return; }
  clearTimeout(micTestTimer);
  micOpenForTest = on;
  micGate();
  try {
    await api("/api/audio/mictest", { on, peer: myPeer });
    if (on) micTestTimer = setTimeout(() => setMicTest(false), 30000);   // backend caps at 30 s too
  } catch { if (on) { micOpenForTest = false; micGate(); $("mic-test").checked = false; } }
}
$("mic-test").onchange = () => setMicTest($("mic-test").checked);   // the level stream confirms the state

// level graph (30 s) + VU bars + codec level, fed by /ws/audio at 10 Hz
const HIST = 300, lvHist = { rx: [], tx: [] };
const vuPct = (db) => Math.max(0, Math.min(100, (db + 60) / 60 * 100));
const vuPk = { rx: 0, tx: 0, rxAt: 0, txAt: 0 };
function onLevels(m) {
  if (m.agc_gain != null && $("tx-auto").checked) showTxAuto(true, m.agc_gain);
  m.tx = (myPeer && m.mics?.[myPeer]) ?? -120;   // my own mic, not another browser's
  dropWatch(m.rx);
  for (const k of ["rx", "tx"]) {
    lvHist[k].push(m[k]);
    if (lvHist[k].length > HIST) lvHist[k].shift();
    const p = vuPct(m[k]), now = performance.now();
    $("ptt-vu-" + k).style.height = p + "%";
    if (p >= vuPk[k] || now - vuPk[k + "At"] > 1500) { vuPk[k] = Math.max(p, vuPk[k] - 4); if (p >= vuPk[k]) vuPk[k + "At"] = now; }
    $("ptt-vu-" + k + "-pk").style.bottom = `calc(${vuPk[k]}% - 1px)`;
  }
  if (m.codec != null) {
    $("lvl-bar").style.setProperty("--p", vuPct(m.codec) + "%");
    lvlPeak.set(vuPct(m.codec_peak ?? m.codec));
    $("lvl-val").textContent = m.codec.toFixed(1) + " dBFS";
  }
  if (!micTestPending()) $("mic-test").checked = !!m.mic_test;
  if (m.tone_1750 != null && Date.now() - t1750At > 1500) show1750(m.tone_1750);
  $("mt-rec").hidden = !m.mic_test;
  $("mt-pb").hidden = !m.echo;
  if (m.ptt === "voice" && !keyed && !pttLock) showKeyed(true);
  if (m.ptt !== "voice" && keyed) { showKeyed(false); setLock(false); }
  drawLevels();
}
function drawLevels() {
  const cv = $("level-graph");
  if ($("audiop").classList.contains("collapsed")) return;
  const dpr = window.devicePixelRatio || 1, r = cv.getBoundingClientRect();
  const w = Math.round(r.width * dpr), h = Math.round(r.height * dpr);
  if (!w || !h) return;
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(127,140,150,0.15)";
  ctx.lineWidth = dpr;
  for (const db of [-50, -40, -30, -20, -10]) {
    const y = Math.round(h - vuPct(db) / 100 * h) + 0.5;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }
  for (const [k, col] of [["rx", "#8b7ad6"], ["tx", "#cf6159"]]) {
    const a = lvHist[k];
    ctx.strokeStyle = col; ctx.lineWidth = 1.6 * dpr; ctx.beginPath();
    a.forEach((db, i) => {
      const x = w - (a.length - 1 - i) / (HIST - 1) * w, y = h - vuPct(db) / 100 * h;
      if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    });
    ctx.stroke();
  }
}

// audio spectrum under the level graph: Web Audio analysers on the received
// WebRTC stream and on the own microphone (drawn while transmitting or testing)
const SPEC_HZ = 3000, SPEC_DB = [-100, -25];   // the FT-950's SSB/DATA passband ends near 3 kHz
const spec = { ctx: null, rx: null, mic: null, nodes: [], raf: 0, last: 0, buf: null,
  peak: null, peakOn: store.get("spec.peak", true) };     // RX peak hold per pixel column (0-255)
const SPEC_PEAK_DECAY = 0.8;                                // per frame (~30 fps): about 10 s to fall away
function showSpecPeak() {
  $("lg-peak").classList.toggle("off", !spec.peakOn);
  $("lg-peak").setAttribute("aria-pressed", String(spec.peakOn));
}
$("lg-peak").onclick = () => { spec.peakOn = !spec.peakOn; spec.peak = null; store.set("spec.peak", spec.peakOn); showSpecPeak(); };
showSpecPeak();
function specStart() {
  try {
    spec.ctx ??= new (window.AudioContext || window.webkitAudioContext)();
    spec.ctx.resume().catch(() => {});
  } catch { spec.ctx = null; }
}
function specAttach(which, stream) {
  if (!spec.ctx) return;
  try {
    const src = spec.ctx.createMediaStreamSource(stream), an = spec.ctx.createAnalyser();
    an.fftSize = 4096; an.smoothingTimeConstant = 0.6;
    an.minDecibels = SPEC_DB[0]; an.maxDecibels = SPEC_DB[1];
    src.connect(an);                              // analysis only, not to the speakers
    spec.nodes.push(src);
    spec[which] = an;
    $("spec-hint").hidden = true;
    if (!spec.raf) spec.raf = requestAnimationFrame(specDraw);
  } catch { /* no Web Audio: the graph stays empty */ }
}
function specStop() {
  for (const n of spec.nodes) { try { n.disconnect(); } catch { /* gone */ } }
  spec.nodes = []; spec.rx = spec.mic = null; spec.peak = null;
  cancelAnimationFrame(spec.raf); spec.raf = 0;
  const cv = $("spec-graph"); cv.getContext("2d").clearRect(0, 0, cv.width, cv.height);
  $("spec-hint").hidden = false;
}
function specDraw(t) {
  spec.raf = requestAnimationFrame(specDraw);
  if (t - spec.last < 33 || uiCovered || $("audiop").classList.contains("collapsed")) return;   // ~30 fps
  spec.last = t;
  const cv = $("spec-graph"), dpr = window.devicePixelRatio || 1, r = cv.getBoundingClientRect();
  const w = Math.round(r.width * dpr), h = Math.round(r.height * dpr);
  if (!w || !h) return;
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(127,140,150,0.15)"; ctx.lineWidth = dpr;
  for (let k = 1; k < SPEC_HZ / 500; k++) {       // every 500 Hz, whole kHz stronger
    const x = Math.round(k * 500 / SPEC_HZ * w) + 0.5;
    ctx.strokeStyle = k % 2 ? "rgba(127,140,150,0.08)" : "rgba(127,140,150,0.18)";
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  }
  const micOn = keyed || $("mic-test").checked;
  for (const [an, col, fill] of [[spec.rx, "#8b7ad6", "rgba(139,122,214,0.22)"], [micOn ? spec.mic : null, "#cf6159", null]]) {
    if (!an) continue;
    const n = an.frequencyBinCount, top = Math.min(n, Math.ceil(SPEC_HZ / (spec.ctx.sampleRate / 2) * n));
    if (!spec.buf || spec.buf.length !== n) spec.buf = new Uint8Array(n);
    an.getByteFrequencyData(spec.buf);
    const isRx = an === spec.rx, cols = Math.ceil(w / dpr);
    if (isRx && spec.peakOn && (!spec.peak || spec.peak.length !== cols)) spec.peak = new Float32Array(cols);
    const pk = isRx && spec.peakOn ? spec.peak : null;
    ctx.beginPath();
    for (let x = 0, c = 0; x < w; x += dpr, c++) {   // per pixel column: peak of its bins
      const b0 = Math.floor(x / w * top), b1 = Math.max(b0 + 1, Math.floor((x + dpr) / w * top));
      let v = 0;
      for (let b = b0; b < b1; b++) if (spec.buf[b] > v) v = spec.buf[b];
      if (pk) pk[c] = Math.max(v, pk[c] - SPEC_PEAK_DECAY);
      const y = h - v / 255 * h;
      if (x) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    }
    ctx.strokeStyle = col; ctx.lineWidth = 1.4 * dpr; ctx.stroke();
    if (fill) {                                   // area under the line, without stroking its edges
      ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
      ctx.fillStyle = fill; ctx.fill();
    }
    if (pk) {                                     // peak hold, as in the HackRF panel
      ctx.beginPath();
      for (let c = 0; c < pk.length; c++) {
        const x = c * dpr, y = h - pk[c] / 255 * h;
        if (c) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      }
      ctx.strokeStyle = hrfDark() ? HRF_PEAKC.dark : HRF_PEAKC.light; ctx.lineWidth = dpr; ctx.stroke();
    }
  }
}

// ---------------------------------------------------------------- HackRF waterfall (ported from TM-V71 Remote)
// The HackRF hangs off the TM-V71 station (.155); the backend proxies its
// control API and stream. "follow" = the backend keeps the panadapter on VFO A
// (+ offset_hz, so the dial frequency sits left of centre). The stream only
// runs while the panel is visible and switched ON.
const hrf = {
  on: false, mode: "pan", follow: true, lna: 24, vga: 20, amp: false,
  center: null, sweepStart: 1.8e6, sweepStop: 30e6,
  sock: null, meta: null, floorEMA: null,          // auto-tracked noise floor (dB)
  level: store.get("hrf.level", 50),               // display peak height
  peakHold: store.get("hrf.peak", true),           // decaying max-hold line
  smooth: null, peak: null,                        // per-bin EMA trace and peak buffers
  statusTimer: null,
};
const hrfDark = () => document.documentElement.dataset.theme !== "light";
const HRF_TRACE = { dark: "#6fd89a", light: "#1f9d57" };   // live spectrum line
const HRF_FILL = { dark: "rgba(111,216,154,0.18)", light: "rgba(31,157,87,0.16)" };
const HRF_PEAKC = { dark: "#ffcf6a", light: "#b86a00" };   // peak hold
// waterfall ramp low→high: deep navy → blue → cyan → yellow → red
const HRF_PAL_DARK = [[10, 14, 30], [26, 60, 150], [40, 130, 225], [70, 205, 225], [245, 225, 70], [220, 55, 40]];
const HRF_PAL_LIGHT = [[223, 230, 239], [120, 165, 222], [40, 120, 212], [40, 178, 205], [232, 195, 55], [208, 48, 38]];
// baked once into 256 steps: a colour per bin per frame without garbage
const HRF_LUT = { light: null, dark: null };
function hrfLut() {
  const key = hrfDark() ? "dark" : "light";
  if (HRF_LUT[key]) return HRF_LUT[key];
  const s = key === "light" ? HRF_PAL_LIGHT : HRF_PAL_DARK, lut = new Uint8Array(256 * 3);
  for (let k = 0; k < 256; k++) {
    const x = (k / 255) * (s.length - 1), i = Math.floor(x), f = x - i;
    const a = s[i], b = s[Math.min(i + 1, s.length - 1)];
    for (let c = 0; c < 3; c++) lut[k * 3 + c] = a[c] + (b[c] - a[c]) * f;
  }
  return (HRF_LUT[key] = lut);
}

// "visible" = the body is actually laid out (collapsed → display:none)
function hrfVisible() { const b = $("hrf-body"); return !!(b && b.offsetParent !== null && b.offsetHeight > 0); }
function hrfShouldRun() { return hrf.on && hrfVisible(); }
function hrfStat(msg) { $("hrf-stat").textContent = msg; }
const hrfMhz = (h) => (h / 1e6).toFixed(3);
const hrfVfoA = () => (radio.connected && radio.vfo?.freq_hz) || null;

function hrfCfg() {
  const c = { mode: hrf.mode, follow: hrf.follow, lna: hrf.lna, vga: hrf.vga, amp: hrf.amp };
  if (hrf.mode === "pan" && !hrf.follow && hrf.center) c.center = hrf.center;
  if (hrf.mode === "sweep") { c.sweep_start = hrf.sweepStart; c.sweep_stop = hrf.sweepStop; }
  return c;
}

async function hrfSync() {
  if (hrfShouldRun() && !hrf.sock) {
    try { applyHrfStatus(await api("/api/hackrf/start", hrfCfg())); }
    catch (e) { hrfStat("start failed: " + e.message); hrf.on = false; updateHrfPower(); return; }
    if (!hrfShouldRun()) return;                   // switched off / hidden meanwhile
    hrf.sock = ws("/ws/hackrf", hrfFrame);
    hrfStat("starting…");
  } else if (!hrfShouldRun() && hrf.sock) {
    hrf.sock.close(); hrf.sock = null;
    try { await api("/api/hackrf/stop", {}); } catch { /* toasted */ }
    hrfStat("");
    $("hrf-vfoline").hidden = true;
  }
}
async function hrfReconfig() {
  if (!hrfShouldRun() || !hrf.sock) return;
  try { applyHrfStatus(await api("/api/hackrf/config", hrfCfg())); } catch { /* toasted */ }
}
function hrfPanelToggled() {
  if (hrfVisible()) { sizeHrfCanvases(); if (hrf.meta) updateHrfVfoLine(hrf.meta); }
  hrfSync();
}

// Frames arrive ~15/s: keep the newest and paint it on the next animation frame.
let hrfPending = null, hrfRaf = 0;
function hrfPaint() {
  hrfRaf = 0;
  const m = hrfPending; hrfPending = null;
  if (!m || !hrfVisible()) return;
  if (uiCovered) {                                      // behind Settings: keep the rows, draw on close
    hrfBacklog.push(m);
    if (hrfBacklog.length > 120) hrfBacklog.shift();
    return;
  }
  hrfUpdateFloor(m.db);
  drawHrfWaterfall(m.db);
  drawHrfSpectrum(m.db);
}
function hrfFrame(m) {
  if (!m || !hrf.sock) return;
  if (m.t === "status") { applyHrfStatus(m); return; }
  if ((m.t !== "pan" && m.t !== "sweep") || !Array.isArray(m.db) || !m.db.length) return;   // idle
  if (m.band && Array.isArray(m.zdb) && m.zdb.length > 8) {
    m = { ...m, db: m.zdb, f0: m.zf0, f1: m.zf1 };   // fine row of the band from TM-V71 Remote
  }
  if (m.band) {                    // follow: show just the amateur band of VFO A
    const [lo, hi] = m.band, n = m.db.length, span = m.f1 - m.f0;
    const i0 = Math.max(0, Math.floor((lo - m.f0) / span * (n - 1)));
    const i1 = Math.min(n - 1, Math.ceil((hi - m.f0) / span * (n - 1)));
    if (i1 - i0 >= 8) {
      m = { ...m, db: m.db.slice(i0, i1 + 1), f0: m.f0 + i0 / (n - 1) * span, f1: m.f0 + i1 / (n - 1) * span };
    }
  }
  hrf.meta = m;
  hrfPending = m;
  if (!hrfRaf) hrfRaf = requestAnimationFrame(hrfPaint);
  $("hrf-f0").textContent = hrfMhz(m.f0);
  $("hrf-fc").textContent = hrfMhz(m.band ? (m.f0 + m.f1) / 2 : m.center);   // band view: its own middle
  $("hrf-f1").textContent = hrfMhz(m.f1);
  updateHrfVfoLine(m);
  hrfStat(`${m.t === "pan" ? "PAN" : "SWEEP"} · ${hrfMhz(m.center)} MHz`);
  if (m.t === "pan") hrf.center = m.center;
  if (m.t === "pan" && hrf.follow) {
    const inp = $("hrf-center-in");
    if (document.activeElement !== inp) inp.value = hrfMhz(m.center);
  }
}

// the source in full, in the panel's footer: "HackRF via https://192.168.201.155:8443"
function hrfSrcLabel(src) {
  try { const u = new URL(src); return `HackRF via ${u.protocol}//${u.host}`; }
  catch { return `HackRF via ${src}`; }
}
function applyHrfStatus(s) {
  if (!s) return;
  if (s.source) { $("hrf-src").textContent = hrfSrcLabel(s.source); $("hrf-src").title = "HackRF source: " + s.source; }
  const reachable = s.reachable !== false;
  const present = reachable && s.available !== false && s.detected !== false;
  $("hrf-ready").hidden = !present;
  if (s.center) hrf.center = s.center;
  if (!reachable) hrfStat("source unreachable" + (s.error ? ": " + s.error : ""));
  else if (s.available === false) hrfStat("hackrf tools missing on the source");
  else if (s.detected === false) hrfStat("no HackRF detected on the source");
  else if (s.error) hrfStat("! " + s.error);
  else if (!hrf.sock) hrfStat("");
  // switching ON stays possible while the source is down: it may come back
  $("hrf-power").disabled = reachable && !present && !hrf.on;
}
// quiet status poll while not streaming (no toast: a missing/unreachable
// HackRF is a normal state of this panel, not an error)
async function hrfPollStatus() {
  if (hrf.sock) return;
  try {
    const r = await fetch("/api/hackrf");
    if (!r.ok) { hrfStat(r.status === 404 ? "HackRF service not available" : `HackRF status: HTTP ${r.status}`);
      $("hrf-ready").hidden = true; return; }
    applyHrfStatus(await r.json());
  } catch { hrfStat("HackRF status unavailable"); $("hrf-ready").hidden = true; }
}

// noise floor: slow EMA of the 10th percentile (1 dB histogram, no sort)
let hrfHist = null;
function hrfUpdateFloor(db) {
  const n = db.length;
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < n; i++) { const v = db[i]; if (v < lo) lo = v; if (v > hi) hi = v; }
  if (!isFinite(lo) || !isFinite(hi)) return;
  const bins = Math.max(1, Math.min(512, Math.ceil(hi - lo) + 1));
  if (!hrfHist || hrfHist.length !== bins) hrfHist = new Uint16Array(bins);
  else hrfHist.fill(0);
  const scale = bins / Math.max(1e-6, hi - lo + 1e-6);
  for (let i = 0; i < n; i++) {
    let k = Math.floor((db[i] - lo) * scale);
    if (k < 0) k = 0; else if (k >= bins) k = bins - 1;
    hrfHist[k]++;
  }
  const target = Math.floor(n * 0.10);
  let acc = 0, k = 0;
  for (; k < bins; k++) { acc += hrfHist[k]; if (acc > target) break; }
  const p = lo + (k + 0.5) / scale;
  hrf.floorEMA = hrf.floorEMA == null ? p : hrf.floorEMA * 0.93 + p * 0.07;
}
// dB → 0..1 with the floor at the bottom; LEVEL sets the dB span above it
function hrfNorm() {
  const floor = hrf.floorEMA == null ? -60 : hrf.floorEMA;
  const dr = Math.max(8, 80 - (hrf.level / 100) * 70);   // level 0→80 dB … 100→10 dB
  return (v) => Math.max(0, Math.min(1, (v - floor) / dr));
}

function sizeHrfCanvases() {
  const dpr = window.devicePixelRatio || 1;
  for (const id of ["hrf-spectrum", "hrf-wf"]) {
    const cv = $(id), r = cv.getBoundingClientRect();
    if (!r.width) continue;
    const w = Math.max(1, Math.round(r.width * dpr)), h = Math.max(1, Math.round(r.height * dpr));
    // assigning clears the canvas: only on a real size change
    if (cv.width !== w) cv.width = w;
    if (cv.height !== h) cv.height = h;
  }
}

let hrfRowCv = null, hrfRowImg = null;   // offscreen 1-row strip at bin resolution
function drawHrfWaterfall(db) {
  const cv = $("hrf-wf"), ctx = cv.getContext("2d"), W = cv.width;
  const dpr = window.devicePixelRatio || 1;
  // sweeps land seldom → fat rows; the panadapter streams → thin rows
  const sweep = hrf.meta && hrf.meta.t === "sweep";
  const rowH = Math.max(1, Math.round((sweep ? 8 : 2) * dpr));
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(cv, 0, rowH);                         // crisp integer scroll down
  const n = db.length, norm = hrfNorm();
  if (!hrfRowCv || hrfRowCv.width !== n) {
    hrfRowCv = document.createElement("canvas"); hrfRowCv.width = n; hrfRowCv.height = 1;
  }
  const rctx = hrfRowCv.getContext("2d");
  if (!hrfRowImg || hrfRowImg.width !== n) hrfRowImg = rctx.createImageData(n, 1);
  const d = hrfRowImg.data, lut = hrfLut();
  for (let i = 0; i < n; i++) {
    const k = Math.max(0, Math.min(255, Math.round(norm(db[i]) * 255))) * 3, o = i * 4;
    d[o] = lut[k]; d[o + 1] = lut[k + 1]; d[o + 2] = lut[k + 2]; d[o + 3] = 255;
  }
  rctx.putImageData(hrfRowImg, 0, 0);
  ctx.imageSmoothingEnabled = true;                   // stretch smoothly, not blocky
  ctx.drawImage(hrfRowCv, 0, 0, n, 1, 0, 0, W, rowH);
}

function drawHrfSpectrum(db) {
  const cv = $("hrf-spectrum"), ctx = cv.getContext("2d"), W = cv.width, H = cv.height;
  const dpr = window.devicePixelRatio || 1, th = hrfDark() ? "dark" : "light";
  ctx.clearRect(0, 0, W, H);
  ctx.strokeStyle = "rgba(127,140,150,0.12)"; ctx.lineWidth = 1;
  for (let g = 1; g < 3; g++) { const y = Math.round(H * g / 3) + 0.5; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }
  const norm = hrfNorm(), n = db.length;
  if (n < 2) return;
  const yOf = (v) => H - norm(v) * (H - 2);
  // EMA per bin calms the fast panadapter; sweep frames stay raw
  let trace = db;
  if (hrf.meta && hrf.meta.t === "pan") {
    if (!hrf.smooth || hrf.smooth.length !== n) hrf.smooth = Float32Array.from(db);
    else { const S = 0.62; for (let i = 0; i < n; i++) hrf.smooth[i] = hrf.smooth[i] * S + db[i] * (1 - S); }
    trace = hrf.smooth;
  } else hrf.smooth = null;
  ctx.beginPath(); ctx.moveTo(0, H);
  for (let i = 0; i < n; i++) ctx.lineTo(i / (n - 1) * W, yOf(trace[i]));
  ctx.lineTo(W, H); ctx.closePath();
  ctx.fillStyle = HRF_FILL[th]; ctx.fill();
  ctx.beginPath(); ctx.moveTo(0, yOf(trace[0]));
  for (let i = 1; i < n; i++) ctx.lineTo(i / (n - 1) * W, yOf(trace[i]));
  ctx.strokeStyle = HRF_TRACE[th]; ctx.lineWidth = 1.2 * dpr; ctx.stroke();
  // peak hold: max of the raw level per bin, decaying 0.5 dB a frame
  if (hrf.peakHold) {
    if (!hrf.peak || hrf.peak.length !== n) hrf.peak = Float32Array.from(db);
    else for (let i = 0; i < n; i++) hrf.peak[i] = db[i] > hrf.peak[i] ? db[i] : hrf.peak[i] - 0.5;
    ctx.beginPath(); ctx.moveTo(0, yOf(hrf.peak[0]));
    for (let i = 1; i < n; i++) ctx.lineTo(i / (n - 1) * W, yOf(hrf.peak[i]));
    ctx.strokeStyle = HRF_PEAKC[th]; ctx.lineWidth = 1 * dpr; ctx.stroke();
  } else hrf.peak = null;
}

// marker at the FT-950 VFO A (left of centre in follow mode: offset_hz)
function updateHrfVfoLine(m) {
  const el = $("hrf-vfoline"), f = hrfVfoA();
  if (!f || !m || !(m.f1 > m.f0)) { el.hidden = true; return; }
  const frac = (f - m.f0) / (m.f1 - m.f0);
  if (frac < 0 || frac > 1) { el.hidden = true; return; }
  el.hidden = false; el.style.left = (frac * 100).toFixed(2) + "%";
  el.title = "FT-950 VFO A · " + hrfMhz(f) + " MHz";
}

function hrfFreqAtX(clientX) {
  const m = hrf.meta; if (!m) return null;
  const r = $("hrf-graph").getBoundingClientRect();
  const frac = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
  const n = m.db.length, idx = Math.max(0, Math.min(n - 1, Math.round(frac * (n - 1))));
  return { freq: Math.round(m.f0 + frac * (m.f1 - m.f0)), db: m.db[idx], frac, gw: r.width };
}

function updateHrfPower() {
  const b = $("hrf-power");
  b.textContent = hrf.on ? "ON" : "OFF";
  b.setAttribute("aria-pressed", String(hrf.on));
  $("hackrf").classList.toggle("hrf-off", !hrf.on);   // dim the body when off
}
function updateHrfModeUi() {
  for (const x of document.querySelectorAll(".hrf-m")) x.classList.toggle("active", x.dataset.mode === hrf.mode);
  for (const x of document.querySelectorAll(".hrf-pan-only")) x.hidden = hrf.mode !== "pan";
  for (const x of document.querySelectorAll(".hrf-sweep-only")) x.hidden = hrf.mode !== "sweep";
  $("hrf-center-in").disabled = hrf.mode === "pan" && hrf.follow;
}

function bindHackRF() {
  hrfPollStatus();
  hrf.statusTimer = setInterval(hrfPollStatus, 30000);

  $("hrf-power").onclick = () => { hrf.on = !hrf.on; updateHrfPower(); hrfSync(); };
  for (const b of document.querySelectorAll(".hrf-m")) b.onclick = () => {
    if (hrf.mode === b.dataset.mode) return;
    hrf.mode = b.dataset.mode; hrf.smooth = hrf.peak = null; updateHrfModeUi(); hrfReconfig();
  };
  $("hrf-follow").onchange = (e) => {
    hrf.follow = e.target.checked;
    const inp = $("hrf-center-in");
    if (!hrf.follow && !inp.value) inp.value = hrfMhz(hrf.center || hrfVfoA() || 14.2e6);
    if (!hrf.follow) hrf.center = Math.round(parseFloat(inp.value) * 1e6) || hrf.center;
    updateHrfModeUi(); hrfReconfig();
  };
  $("hrf-amp").onchange = (e) => { hrf.amp = e.target.checked; hrfReconfig(); };
  const peakCb = $("hrf-peak");
  peakCb.checked = hrf.peakHold;
  peakCb.onchange = (e) => {
    hrf.peakHold = e.target.checked; hrf.peak = null;   // restart the hold from the next frame
    store.set("hrf.peak", hrf.peakHold);
    if (hrf.meta && hrfVisible()) drawHrfSpectrum(hrf.meta.db);
  };
  const lna = $("hrf-lna"), vga = $("hrf-vga"), lvl = $("hrf-level");
  lna.oninput = () => { $("hrf-lna-v").textContent = lna.value; fillPct(lna); };
  vga.oninput = () => { $("hrf-vga-v").textContent = vga.value; fillPct(vga); };
  lna.onchange = () => { hrf.lna = +lna.value; hrfReconfig(); };
  vga.onchange = () => { hrf.vga = +vga.value; hrfReconfig(); };
  lvl.value = hrf.level; $("hrf-level-v").textContent = hrf.level;
  lvl.oninput = () => {                               // display only
    hrf.level = +lvl.value; $("hrf-level-v").textContent = lvl.value; fillPct(lvl);
    store.set("hrf.level", hrf.level);
    if (hrf.meta && hrfVisible()) drawHrfSpectrum(hrf.meta.db);
  };
  fillPct(lna); fillPct(vga); fillPct(lvl);
  $("hrf-center-in").onchange = (e) => {
    const mhz = parseFloat(String(e.target.value).replace(",", "."));
    if (isFinite(mhz) && mhz > 0) { hrf.center = Math.round(mhz * 1e6); hrfReconfig(); }
  };
  $("hrf-range-in").onchange = (e) => {
    const m = String(e.target.value).replace(/,/g, ".").match(/(\d+(?:\.\d+)?)\s*[-–:]\s*(\d+(?:\.\d+)?)/);
    if (!m) { toast("Range: e.g. 1.8–30 (MHz)", true); return; }
    const a = parseFloat(m[1]) * 1e6, b = parseFloat(m[2]) * 1e6;
    hrf.sweepStart = Math.round(Math.min(a, b)); hrf.sweepStop = Math.round(Math.max(a, b));
    hrfReconfig();
  };
  // hover readout (frequency + dB) over the whole graph; double-click → VFO A
  const g = $("hrf-graph"), cur = $("hrf-cur"), tip = $("hrf-tip");
  g.addEventListener("mousemove", (e) => {
    const info = hrfFreqAtX(e.clientX);
    if (!info) { cur.hidden = tip.hidden = true; return; }
    // positions in % of the graph: clientX/rects are zoomed (console at 90 %), CSS px are not
    cur.hidden = false; cur.style.left = (info.frac * 100) + "%";
    tip.hidden = false;
    tip.textContent = `${hrfMhz(info.freq)} MHz · ${Math.round(info.db)} dB`;
    const edge = 60 / info.gw;                       // keep the tip inside the graph
    tip.style.left = (Math.max(edge, Math.min(1 - edge, info.frac)) * 100) + "%";
  });
  g.addEventListener("mouseleave", () => { cur.hidden = tip.hidden = true; });
  g.addEventListener("click", (e) => {             // one click tunes VFO A there
    const info = hrfFreqAtX(e.clientX);
    if (!info) return;
    const hz = Math.round(info.freq / 10) * 10;
    tuneLive(hz);
    toast(`VFO A → ${hrfMhz(hz)} MHz`);
  });
  window.addEventListener("resize", () => { if (hrfVisible()) sizeHrfCanvases(); });
  updateHrfPower(); updateHrfModeUi();
  if (hrfVisible()) sizeHrfCanvases();
}

// ---------------------------------------------------------------- Settings covers the page
// While the Settings dialog is open the page behind it is hidden: its animations
// pause (they cost the browser a lot of CPU, the dialog redrawn over every frame).
// Data keeps coming in; the waterfalls keep their rows and draw them on close.
let uiCovered = false;
const wfBacklog = [], hrfBacklog = [];
new MutationObserver(() => {
  const open = $("settings").classList.contains("open");
  if (open === uiCovered) return;
  uiCovered = open;
  if (open) return;
  for (const f of wfBacklog.splice(0)) onFrame(f);
  const rows = hrfBacklog.splice(0);
  if (rows.length && hrfVisible()) {
    for (const m of rows.slice(0, -1)) { hrfUpdateFloor(m.db); drawHrfWaterfall(m.db); }
    hrfPending = rows[rows.length - 1]; hrfPaint();     // the last one with its spectrum
  }
  if (!needle.raf) needle.raf = requestAnimationFrame(needleStep);
}).observe($("settings"), { attributes: true, attributeFilter: ["class"] });

// ---------------------------------------------------------------- start
(async function main() {
  renderFreq(0);
  buildAxis();
  placeBoxes();
  requestAnimationFrame(tick);
  loadBranding();
  api("/api/settings").then((s) => {
    applyCallsign(s.my_call); $("app-ver").textContent = "v" + s.version;
    digiColors = s.digi_colors || {}; applyColors(digiColors);
  }).catch(() => {});
  try { await loadMeta(); } catch { /* backend down: ws reconnect shows it */ }
  ws("/ws", renderRadio, (up) => { if (!up) renderRadio({ connected: false, error: "Backend unreachable" }); });
  ws("/ws/digi", onDigi);
  ws("/ws/waterfall", onFrame);
  pollDigi();
  setInterval(pollDigi, 2000);
  pollTx();
  ws("/ws/audio", onLevels);
  loadVoice();
  loadProp();
  setInterval(loadProp, 5 * 60 * 1000);
  setAudioUi("down");
  loadRecent();
  loadLogStatus();
  setInterval(loadLogStatus, 30000);
  bindHackRF();
})();

// ---------------------------------------------------------------- CW skimmer + keyer
let cwState = { channels: [] }, cwSel = null, cwRadio = null;
function cwRf(pitch) {
  // FT-950 CW: the display shows the carrier; a signal heard at `pitch` Hz is
  // (pitch − KP) above it in CW, below it in CW-R
  const v = radio.vfo, kp = cwRadio?.pitch || 700;
  if (!v?.freq_hz) return null;
  switch (v.mode) {
    case "CW": return v.freq_hz + (pitch - kp);
    case "CW-R": return v.freq_hz - (pitch - kp);
    case "LSB": case "DATA-L": case "RTTY-L": return v.freq_hz - pitch;   // lower sideband: dial − tone
    default: return v.freq_hz + pitch;                                       // USB / DATA-U / AM / FM: dial + tone
  }
}
function renderCw(s) {
  cwState = s;
  $("cw-on").classList.toggle("on", !!s.enabled);
  $("cw-stat").textContent = "";
  $("cw-count").textContent = s.enabled ? `${s.channels.length} signal${s.channels.length === 1 ? "" : "s"}` : "";
  const body = $("cw-body");
  if (!s.enabled || !s.channels.length) {
    body.innerHTML = `<tr class="empty"><td colspan="5">${s.enabled ? "Listening … no CW carrier in the passband yet." : "Switch DECODE on — every CW signal in the passband gets a row. Click one to follow it."}</td></tr>`;
  } else {
    body.innerHTML = s.channels.map((c) => {
      const rf = cwRf(c.hz);
      return `<tr data-id="${c.id}" class="${c.id === cwSel ? "sel" : ""}${c.on ? " cw-on" : ""}"><td class="n">${Math.round(c.hz)}</td>` +
        `<td class="n">${rf ? (rf / 1000).toFixed(2) : "—"}</td><td class="n">${c.wpm}</td><td class="n">${c.snr}</td>` +
        `<td class="msg cw-tail"><span>${esc(c.text.slice(-120))}</span></td></tr>`;
    }).join("");
    for (const tr of body.querySelectorAll("tr[data-id]")) tr.onclick = () => { cwSel = +tr.dataset.id; renderCw(cwState); };
  }
  const sel = s.channels.find((c) => c.id === cwSel);
  $("cw-zb").disabled = !sel;
  $("cw-sel-lbl").textContent = sel ? `${Math.round(sel.hz)} Hz · ${(cwRf(sel.hz) / 1000).toFixed(2)} kHz · ${sel.wpm} WPM` : "no signal selected";
  if (sel) {
    const t = $("cw-text"); t.textContent = sel.text; t.scrollTop = t.scrollHeight;
    const c = cwCallFrom(sel.text);
    if (c && Date.now() - cwCallTouched > 20000) $("cw-call").value = c;   // don't overwrite a hand-typed call
  }
}
async function loadCw() {
  try {
    const s = await api("/api/cw");
    cwRadio = s.radio;
    if (s.radio) {
      if (document.activeElement !== $("cw-wpm")) $("cw-wpm").value = s.radio.wpm;
      $("cw-pitch").textContent = `${s.radio.pitch} Hz`;
      $("cw-bk").classList.toggle("on", !!s.radio.breakin);
    }
    renderCw(s);
  } catch { /* backend down */ }
}
ws("/ws/cw", (m) => { if (m.t === "cw") renderCw(m); });
loadCw();
setInterval(loadCw, 10000);
$("cw-on").onclick = async () => { try { renderCw(await api("/api/cw/enable", { on: !cwState.enabled })); } catch { /* toast */ } };
$("cw-clear").onclick = async () => { try { renderCw(await api("/api/cw/clear", {})); $("cw-text").textContent = ""; } catch { /* toast */ } };
$("cw-bk").onclick = async () => { try { await api("/api/cw/breakin", { on: !$("cw-bk").classList.contains("on") }); loadCw(); } catch { /* toast */ } };
$("cw-wpm").onchange = async (e) => { try { await api("/api/cw/speed", { wpm: +e.target.value }); loadCw(); } catch { loadCw(); } };
$("cw-zb").onclick = () => {
  const sel = cwState.channels.find((c) => c.id === cwSel);
  const rf = sel && cwRf(sel.hz);
  if (rf) { tuneLive(Math.round(rf / 10) * 10); toast(`Zero beat: ${(rf / 1000).toFixed(2)} kHz`); }
};
$("cw-send").onclick = async () => {
  const text = $("cw-msg").value.trim();
  if (!text) return;
  try {
    const r = await api("/api/cw/send", { text });
    toast(`Sending CW (${r.seconds} s)`);
    $("cw-msg").value = "";
    const row = document.createElement("div");
    row.innerHTML = `<span class="t">${new Date().toISOString().slice(11, 19)}</span>${esc(text.toUpperCase())}`;
    $("cw-sent").appendChild(row);
    $("cw-sent").scrollTop = $("cw-sent").scrollHeight;
  } catch { /* toast */ }
};
$("cw-msg").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("cw-send").click(); } });
// CW macros: click puts the text into the send line (Enter / SEND transmits)
const CW_MACROS = [
  ["CQ", "CQ CQ DE {MY} {MY} K"], ["CALL", "{CALL} DE {MY} K"],
  ["5NN", "{CALL} DE {MY} TU UR 5NN 5NN LOC {LOC} K"], ["RR", "R R TU 5NN BK"],
  ["73", "{CALL} DE {MY} TU 73 SK"], ["AGN", "AGN AGN"], ["QRZ", "QRZ? DE {MY} K"],
];
let cwMy = { call: "", loc: "" };
api("/api/settings").then((s) => { cwMy = { call: (s.my_call || "").toUpperCase(), loc: (s.my_grid || "").slice(0, 4).toUpperCase() }; }).catch(() => {});
$("cw-macro-btns").innerHTML = CW_MACROS.map(([n]) => `<button class="btn sm cw-mac" data-n="${n}">${n}</button>`).join("");
for (const b of document.querySelectorAll(".cw-mac")) {
  const tpl = CW_MACROS.find(([n]) => n === b.dataset.n)[1];
  b.title = tpl;
  b.onclick = () => {
    const call = $("cw-call").value.trim().toUpperCase();
    if (tpl.includes("{CALL}") && !call) { toast("No callsign — select a signal or type one into CALL", true); $("cw-call").focus(); return; }
    $("cw-msg").value = tpl.replaceAll("{MY}", cwMy.call).replaceAll("{LOC}", cwMy.loc).replaceAll("{CALL}", call);
    $("cw-msg").focus();
  };
}
// callsign of the selected signal: the last call-like word that is not mine
function cwCallFrom(text) {
  const calls = (text.match(/\b[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,3}[A-Z]\b/g) || []).filter((c) => c !== cwMy.call && /[A-Z]/.test(c[0] + c.slice(-1)));
  return calls.length ? calls[calls.length - 1] : "";
}
let cwCallTouched = 0;
$("cw-call").addEventListener("input", () => { cwCallTouched = Date.now(); });
$("cw-sent-clear").onclick = () => { $("cw-sent").innerHTML = ""; };
$("cw-stop").onclick = () => api("/api/cw/stop", {}).then(() => toast("CW stopped after the current chunk")).catch(() => {});

// ---------------------------------------------------------------- RTTY skimmer + AFSK
let rtState = { channels: [] }, rtSel = null;
const RT_SIDES = ["LSB", "DATA-L", "RTTY-L", "CW-R"];
function rtRf(tone) {                          // the signal's centre on the air
  const v = radio.vfo;
  if (!v?.freq_hz) return null;
  return RT_SIDES.includes(v.mode) ? v.freq_hz - tone : v.freq_hz + tone;
}
const RT_TX_MODES = ["DATA-L", "DATA-U", "USB"];
function renderRtty(s) {
  rtState = s;
  $("rt-on").classList.toggle("on", !!s.enabled);
  $("rt-rev").classList.toggle("on", !!s.rev);
  $("rt-side").textContent = s.upper_mark ? "MARK ↑" : "MARK ↓";
  const txOk = RT_TX_MODES.includes(s.mode);
  $("rt-stat").textContent = s.sending ? "SENDING" : (txOk ? "" : `TX needs DATA-L / DATA-U (radio: ${s.mode || "—"})`);
  $("rt-stat").classList.toggle("rt-sending", !!s.sending);
  $("rt-send").disabled = !!s.sending;
  $("rt-count").textContent = s.enabled ? `${s.channels.length} signal${s.channels.length === 1 ? "" : "s"}` : "";
  const body = $("rt-body");
  if (!s.enabled || !s.channels.length) {
    body.innerHTML = `<tr class="empty"><td colspan="5">${s.enabled ? "Listening … no RTTY signal in the passband yet." : "Switch DECODE on — every RTTY signal in the passband gets a row. Click one to follow it."}</td></tr>`;
  } else {
    body.innerHTML = s.channels.map((c) => {
      const rf = rtRf(c.hz);
      return `<tr data-id="${c.id}" class="${c.id === rtSel ? "sel" : ""}"><td class="n">${Math.round(c.hz)}</td>` +
        `<td class="n">${rf ? (rf / 1000).toFixed(2) : "—"}</td><td class="n">${c.snr}</td><td class="n">${c.q}</td>` +
        `<td class="msg cw-tail"><span>${esc(c.text.replace(/\n+/g, " ").slice(-120))}</span></td></tr>`;
    }).join("");
    for (const tr of body.querySelectorAll("tr[data-id]")) tr.onclick = () => {
      rtSel = +tr.dataset.id;
      const c = rtState.channels.find((x) => x.id === rtSel);
      if (c) { $("rt-txhz").value = Math.round(c.hz); store.set("rttyTxHz", Math.round(c.hz)); }   // answer on its frequency
      renderRtty(rtState);
    };
  }
  const sel = s.channels.find((c) => c.id === rtSel);
  $("rt-sel-lbl").textContent = sel ? `${Math.round(sel.hz)} Hz · ${(rtRf(sel.hz) / 1000).toFixed(2)} kHz · Q ${sel.q} %` : "no signal selected";
  if (sel) {
    const t = $("rt-text");
    const atEnd = t.scrollTop + t.clientHeight >= t.scrollHeight - 8;
    t.textContent = sel.text;
    if (atEnd) t.scrollTop = t.scrollHeight;
    const c = cwCallFrom(sel.text.toUpperCase());
    if (c && Date.now() - rtCallTouched > 20000) $("rt-call").value = c;
  }
}
let rtCallTouched = 0;
$("rt-call").addEventListener("input", () => { rtCallTouched = Date.now(); });
$("rt-txhz").value = store.get("rttyTxHz", 2210);
$("rt-txhz").onchange = (e) => store.set("rttyTxHz", Math.max(300, Math.min(2600, +e.target.value || 2210)));
async function loadRtty() { try { renderRtty(await api("/api/rtty")); } catch { /* backend down */ } }
ws("/ws/rtty", (m) => { if (m.t === "rtty") renderRtty(m); });
loadRtty();
setInterval(loadRtty, 10000);
$("rt-on").onclick = async () => { try { renderRtty(await api("/api/rtty/enable", { on: !rtState.enabled })); } catch { /* toast */ } };
$("rt-rev").onclick = async () => { try { renderRtty(await api("/api/rtty/rev", { on: !rtState.rev })); } catch { /* toast */ } };
$("rt-clear").onclick = async () => { try { renderRtty(await api("/api/rtty/clear", {})); $("rt-text").textContent = ""; } catch { /* toast */ } };
$("rt-send").onclick = async () => {
  const text = $("rt-msg").value.trim();
  if (!text) return;
  const hz = Math.max(300, Math.min(2600, +$("rt-txhz").value || 2210));
  try {
    const r = await api("/api/rtty/send", { text: text.toUpperCase(), hz });
    toast(`Sending RTTY at ${hz} Hz (${r.seconds} s)`);
    $("rt-msg").value = "";
    const row = document.createElement("div");
    row.innerHTML = `<span class="t">${new Date().toISOString().slice(11, 19)}</span>${esc(text.toUpperCase())}`;
    $("rt-sent").appendChild(row);
    $("rt-sent").scrollTop = $("rt-sent").scrollHeight;
  } catch { /* toast */ }
};
$("rt-msg").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("rt-send").click(); } });
$("rt-stop").onclick = () => api("/api/rtty/stop", {}).then(() => toast("RTTY stopped")).catch(() => {});
$("rt-sent-clear").onclick = () => { $("rt-sent").innerHTML = ""; };
// RTTY macros: click puts the text into the send line (Enter / SEND transmits)
const RT_MACROS = [
  ["CQ", "CQ CQ CQ DE {MY} {MY} {MY} PSE K"], ["CALL", "{CALL} DE {MY} {MY} K"],
  ["599", "{CALL} DE {MY} TU UR 599 599 {LOC} {LOC} BK"], ["TU", "{CALL} TU 73 DE {MY} SK"],
  ["AGN", "AGN AGN PSE"], ["QRZ", "QRZ? DE {MY} K"], ["RY", "RYRYRYRYRYRYRYRYRYRY"],
];
$("rt-macro-btns").innerHTML = RT_MACROS.map(([n]) => `<button class="btn sm rt-mac" data-n="${n}">${n}</button>`).join("");
for (const b of document.querySelectorAll(".rt-mac")) {
  const tpl = RT_MACROS.find(([n]) => n === b.dataset.n)[1];
  b.title = tpl;
  b.onclick = () => {
    const call = $("rt-call").value.trim().toUpperCase();
    if (tpl.includes("{CALL}") && !call) { toast("No callsign — select a signal or type one into CALL", true); $("rt-call").focus(); return; }
    $("rt-msg").value = tpl.replaceAll("{MY}", cwMy.call).replaceAll("{LOC}", cwMy.loc).replaceAll("{CALL}", call);
    $("rt-msg").focus();
  };
}

// ---------------------------------------------------------------- Settings ▸ Appearance
// Panels on/off and their accent colours, per browser (as TM-V71 Remote's Panels tab).
// VFO, PTT and Audio always stay (`fixed`: no tile, only an accent colour) - the radio
// cannot be worked without them; the others get one tile each.
const PANELS = [
  { key: "vfo", sel: "#vfo", name: "VFO", fixed: true },
  { key: "ptt", sel: "#pttp", name: "PTT · Memory", fixed: true },
  { key: "audio", sel: "#audiop", name: "Audio · WebRTC", fixed: true },
  { key: "digi", sel: "#digi", name: "DIGI · FT8/FT4" },
  { key: "rtty", sel: "#rttyp", name: "RTTY" },
  { key: "cw", sel: "#cwp", name: "CW" },
  { key: "hackrf", sel: "#hackrf", name: "HackRF waterfall" },
  { key: "log", sel: "#log", name: "Log · Wavelog" },
];
const panelEl = (p) => document.querySelector(p.sel);
// keys this build no longer has are dropped, so SHOW ALL never does nothing
// saved = what SAVE stored; panelsOff / accents = the working copy, shown live as a preview
const savedPanels = () => store.get("panelsOff", []).filter((k) => PANELS.some((p) => p.key === k && !p.fixed));
let panelsOff = new Set(savedPanels());
let accents = store.get("accents", {});

function applyPanels() {
  for (const p of PANELS) panelEl(p)?.classList.toggle("panel-off", panelsOff.has(p.key));
}
function setPanelHidden(key, hidden) {
  if (hidden) panelsOff.add(key); else panelsOff.delete(key);
  applyPanels();
  // canvases laid out while hidden come up zero-sized: measure again on the way back
  requestAnimationFrame(() => {
    window.dispatchEvent(new Event("resize"));
    if (key === "digi" && !hidden) buildAxis();
    if (key === "hackrf") hrfPanelToggled();      // hidden: the sweep stops, shown: resumes
  });
}
function applyAccents() {
  for (const p of PANELS) {
    const el = panelEl(p), c = accents[p.key];
    if (!el) continue;
    if (c) { el.style.setProperty("--accent", c); el.style.setProperty("--accent-glow", c); }
    else { el.style.removeProperty("--accent"); el.style.removeProperty("--accent-glow"); }
  }
}
function accentNow(el) {                        // the accent in effect, as #rrggbb
  const ctx = document.createElement("canvas").getContext("2d");
  ctx.fillStyle = "#000";
  ctx.fillStyle = getComputedStyle(el).getPropertyValue("--accent").trim() || "#000";
  return ctx.fillStyle.startsWith("#") ? ctx.fillStyle : "#000000";
}
function renderAppearance() {
  $("peak-hold").checked = peakOn;
  const box = $("panel-list");
  box.textContent = "";
  for (const p of PANELS) {
    if (!panelEl(p) || p.fixed) continue;
    const tile = document.createElement("label");
    tile.className = "switch-label panel-tile";
    tile.title = p.name;
    tile.innerHTML = `<span class="panel-row-txt">${esc(p.name)}</span><input type="checkbox" class="switch">`;
    const cb = tile.querySelector("input");
    cb.checked = !panelsOff.has(p.key);
    tile.classList.toggle("is-off", !cb.checked);
    cb.onchange = () => {
      tile.classList.toggle("is-off", !cb.checked);
      setPanelHidden(p.key, !cb.checked);
      toast(`${p.name}: ${cb.checked ? "shown" : "hidden"}`);
    };
    box.appendChild(tile);
  }
  const grid = $("accent-grid");
  grid.textContent = "";
  for (const p of PANELS) {
    const el = panelEl(p);
    if (!el) continue;
    const row = document.createElement("div");
    row.className = "color-row";
    row.innerHTML = `<input type="color" value="${accentNow(el)}" aria-label="${esc(p.name)} accent">` +
      `<span class="cname">${esc(p.name)}${accents[p.key] ? "" : ' <small class="dim">(theme)</small>'}</span>` +
      `<span class="cprev acc-prev">${esc(p.name.split(" ")[0])}</span>` +
      `<button type="button" class="mini-btn" title="Back to the theme colour">Reset</button>`;
    const prev = row.querySelector(".acc-prev");
    prev.style.color = prev.style.borderColor = accentNow(el);
    row.querySelector("input").oninput = (e) => {
      accents[p.key] = e.target.value;
      applyAccents();
      prev.style.color = prev.style.borderColor = e.target.value;
      row.querySelector(".cname small")?.remove();
    };
    row.querySelector("button").onclick = () => {
      delete accents[p.key];
      applyAccents();
      renderAppearance();
    };
    grid.appendChild(row);
  }
}
$("panels-all").onclick = () => {
  for (const k of [...panelsOff]) setPanelHidden(k, false);
  renderAppearance();
  toast("All panels shown");
};
$("accents-reset").onclick = () => {
  accents = {};
  applyAccents();
  renderAppearance();
  toast("Accent colours back to the theme's");
};
$("peak-hold").onchange = (e) => {
  peakOn = e.target.checked;
  if (!peakOn) pk.held = false;                 // a held display goes at once
  $("tx-meters").classList.toggle("no-peak", !peakOn);
  $("pk-clr").hidden = !peakOn;                 // nothing to clear without peak hold
};
$("appearance-save").onclick = () => {
  store.set("panelsOff", [...panelsOff]);
  store.set("accents", accents);
  store.set("peakHold", peakOn);
  toast("Appearance saved");
};
// closing Settings without SAVE drops the preview: back to the saved panels and colours
function revertAppearance() {
  const was = new Set(panelsOff);
  panelsOff = new Set(savedPanels());
  accents = store.get("accents", {});
  applyAccents();
  peakOn = store.get("peakHold", true);
  $("tx-meters").classList.toggle("no-peak", !peakOn);
  for (const p of PANELS) if (was.has(p.key) !== panelsOff.has(p.key)) setPanelHidden(p.key, panelsOff.has(p.key));
}
applyPanels();
applyAccents();
// at load, once everything is defined: the PO/SWR/ALC/ID digit places with their
// dimmed 8s (the HTML has plain text until the first write)
txVals(0, 0, 0, 0);

// Segment bars (PO … VDD, the S meter): equal columns at 90 % zoom land on
// fractions of a pixel, so the screen rounds the gaps to 1 or 2 pixels and they
// look uneven. Size segment and gap in whole screen pixels instead, centred.
function snapSegBars() {
  const dpr = window.devicePixelRatio || 1;
  for (const el of document.querySelectorAll(".lbar, .dsm-bar")) {
    const n = el.children.length;
    if (!n || !el.offsetWidth) continue;
    const cs = getComputedStyle(el);
    const z = el.getBoundingClientRect().width / el.offsetWidth;          // zoom of this element
    const inner = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const px = z * dpr;                                                     // screen pixels per CSS pixel
    const gapDev = Math.max(1, Math.round(2 * px));
    const segDev = Math.floor((inner * px - gapDev * (n - 1)) / n);
    if (segDev < 1) continue;
    el.style.gridTemplateColumns = `repeat(${n}, ${segDev / px}px)`;
    el.style.columnGap = `${gapDev / px}px`;
    el.style.justifyContent = "center";
  }
}
snapSegBars();
{
  const ro = new ResizeObserver(() => requestAnimationFrame(snapSegBars));
  for (const el of document.querySelectorAll(".lbar, .dsm-bar")) ro.observe(el);
  matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`).addEventListener?.("change", snapSegBars);
}
