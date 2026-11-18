// Entry point: calls the backend and fills the map, KPI cards, charts and side panels.
import { initMap, setTiles, setOpacity, setBoundary, fitHome, toggleBasemap, renderLegend } from "./map.js";
import { initCharts, sparkline, renderSeasonal, renderVci, renderRain, renderDonut, renderRanking, renderGauge, RISK_COLOR } from "./charts.js";
import { initFilters, setDistricts, selectDistrict, getFilters, buildLayerPanel, setActiveLayer } from "./filters.js";

const API_BASE = (window.AGROWATCH_API_BASE ?? "").replace(/\/$/, "");
const VIEW_LAYER = { overview: "stress", vegetation: "s2_ndvi", rainfall: "rain_pct", drought: "adsi" };
let token = localStorage.getItem("agrowatch_token") || "";
const state = { meta: null, layer: "stress", stats: null, run: 0, tileRun: 0 };

const $ = (s) => document.querySelector(s);
const fmtInt = (n) => (n == null || isNaN(n) ? "--" : Math.round(n).toLocaleString("en-US"));
const fmt = (n, d = 2) => (n == null || isNaN(n) ? "--" : Number(n).toFixed(d));

// ------------------------------------------------------------------ API
async function api(path, params = {}) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null)).toString();
  const res = await fetch(`${API_BASE}${path}${qs ? "?" + qs : ""}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (res.status === 401) { await login(); return api(path, params); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function login() {
  return new Promise((resolve) => {
    const dlg = $("#login-dialog"), form = $("#login-form");
    dlg.oncancel = (e) => e.preventDefault();
    dlg.showModal();
    form.onsubmit = async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const res = await fetch(`${API_BASE}/api/login`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: fd.get("username"), password: fd.get("password") }),
      });
      if (!res.ok) { $("#login-error").textContent = "Wrong username or password."; return; }
      token = (await res.json()).token;
      localStorage.setItem("agrowatch_token", token);
      dlg.close();
      resolve();
    };
  });
}

const query = (f, extra = {}) => ({
  province: f.province, district: f.district, year: f.year, season: f.season, crop: f.crop ? 1 : 0, ...extra,
});

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 8000);
}

// ------------------------------------------------------------ KPI cards
function setKpi(id, value, sub, tone) {
  const el = $("#" + id);
  el.querySelector(".kpi-val").textContent = value;
  const s = el.querySelector(".kpi-sub");
  s.textContent = sub || "";
  s.className = "kpi-sub";
  if (tone) el.dataset.tone = tone;
}
function setDelta(id, text, dir) {
  const s = $("#" + id + " .kpi-sub");
  s.textContent = text;
  s.className = "kpi-sub " + (dir || "");
}
function resetPanels() {
  ["kpi-area", "kpi-adsi", "kpi-ndvi", "kpi-vci", "kpi-rain", "kpi-stress"].forEach((id) => setKpi(id, "--", "Loading...", "neutral"));
  $("#gauge-pct").textContent = "--";
  $("#gauge-level").textContent = "Loading";
}

// Tone helpers shared by the KPI cards and the indicator tiles.
const adsiTone = (v) => (v == null ? "neutral" : v < 0.30 ? "good" : v < 0.45 ? "watch" : "bad");
const condTone = (v) => (v == null ? "neutral" : v >= 50 ? "good" : v >= 35 ? "watch" : "bad");   // VCI, TCI, VHI, SMCI
const spiTone = (v) => (v == null ? "neutral" : v > -0.5 ? "good" : v > -1 ? "watch" : "bad");

function renderIndicators(m) {
  const items = [
    ["SPI (rain)", fmt(m.SPI), spiTone(m.SPI)],
    ["SMCI (soil water)", fmt(m.SMCI, 0), condTone(m.SMCI)],
    ["TCI (temperature)", fmt(m.TCI, 0), condTone(m.TCI)],
    ["VHI (veg. health)", fmt(m.VHI, 0), condTone(m.VHI)],
    ["Day LST", m.LST == null ? "--" : `${fmt(m.LST, 1)} \u00b0C`, "neutral"],
    ["ESI (ET/PET)", fmt(m.ESI), "neutral"],
    ["LAI", fmt(m.LAI), "neutral"],
    ["GPP", m.GPP == null ? "--" : `${fmtInt(m.GPP)} gC/m\u00b2`, "neutral"],
  ];
  $("#indicator-list").replaceChildren(...items.map(([label, value, tone]) => {
    const li = document.createElement("li");
    li.className = "ind";
    li.dataset.tone = tone;
    const l = document.createElement("span"); l.textContent = label;
    const v = document.createElement("b"); v.textContent = value;
    li.append(l, v);
    return li;
  }));
}

// The class list and donut follow the selected map layer: VCI classes for the vegetation-only
// layer, ADSI drought classes for everything else.
function renderClasses() {
  const s = state.stats;
  if (!s) return;
  const veg = state.layer === "vci_stress";
  const classes = veg ? s.vci_classes : s.classes;
  $("#class-title").textContent = veg ? "Cropland by vegetation stress class" : "Cropland by drought class";
  $("#donut-title").textContent = veg ? "Vegetation stress class share" : "Drought class share";
  $("#class-list").replaceChildren(...classes.map((c) => {
    const li = document.createElement("li");
    li.className = "cls";
    const chip = document.createElement("i"); chip.style.background = c.color;
    const name = document.createElement("b"); name.textContent = c.name;
    const val = document.createElement("em"); val.textContent = `${fmtInt(c.ha)} ha (${fmt(c.pct, 1)}%)`;
    const bar = document.createElement("div"); bar.className = "bar";
    const fill = document.createElement("div"); fill.style.cssText = `width:${Math.min(100, c.pct)}%;background:${c.color}`;
    bar.appendChild(fill);
    li.append(chip, name, val, bar);
    return li;
  }));
  renderDonut("chart-donut", classes);
}

function renderStats(s) {
  state.stats = s;
  const m = s.means || {};
  setKpi("kpi-area", fmtInt(s.total_ha), "ESA WorldCover cropland");
  setKpi("kpi-adsi", fmt(m.ADSI), "0 = no stress, 1 = extreme", adsiTone(m.ADSI));
  setKpi("kpi-ndvi", fmt(m.NDVI), `EVI ${fmt(m.EVI)}`, "good");
  setKpi("kpi-vci", fmt(m.VCI, 0), "Season vs 2001 to 2018 range", condTone(m.VCI));
  const r = m.rain_pct;
  const rTone = r == null ? "neutral" : r >= 85 && r <= 130 ? "good" : r >= 60 ? "watch" : "bad";
  setKpi("kpi-rain", fmtInt(m.rain), r == null ? "No normal available" : `${fmt(r, 0)}% of normal`, rTone);
  const sp = s.stressed_pct;
  setKpi("kpi-stress", fmt(sp, 1), `${fmtInt(s.stressed_ha)} ha, moderate or worse`, sp < 10 ? "good" : sp < 25 ? "watch" : "bad");
  renderIndicators(m);
  renderClasses();
}

function renderSeries(series) {
  const rows = series.rows;
  renderSeasonal("chart-seasonal", rows);
  renderVci("chart-vci", rows);
  sparkline($("#kpi-ndvi .spark"), rows.map((r) => r.NDVI), "#34d399");
  sparkline($("#kpi-vci .spark"), rows.map((r) => r.VCI), "#2f8cff");
  sparkline($("#kpi-rain .spark"), rows.map((r) => r.rain), "#22d3ee");

  const [b0, b1] = series.base;
  const baseVals = rows.filter((r) => r.year <= b1 && r.NDVI != null).map((r) => r.NDVI);
  const cur = rows.length ? rows[rows.length - 1].NDVI : null;
  if (cur != null && baseVals.length) {
    const d = cur - baseVals.reduce((a, b) => a + b, 0) / baseVals.length;
    setDelta("kpi-ndvi", `${d >= 0 ? "+" : ""}${d.toFixed(2)} vs ${b0}-${b1} average`, d >= 0 ? "up" : "down");
  }
}

function renderInsights(ins) {
  renderGauge("gauge", ins.stressed_pct, ins.risk);
  $("#gauge-pct").textContent = `${fmt(ins.stressed_pct, 0)}%`;
  const lvl = $("#gauge-level");
  lvl.textContent = `${ins.risk} risk`;
  lvl.style.color = RISK_COLOR[ins.risk] || "";
  $("#insights-list").replaceChildren(...ins.messages.map((m) => {
    const li = document.createElement("li");
    li.className = "ins " + m.level;
    const b = document.createElement("b"); b.textContent = m.title;
    const s = document.createElement("span"); s.textContent = m.text;
    li.append(b, s);
    return li;
  }));
}

// -------------------------------------------------------------- loaders
async function loadTiles(f) {
  const run = ++state.tileRun;
  $("#map-loading").classList.remove("hidden");
  try {
    const t = await api("/api/tiles", query(f, { layer: state.layer }));
    if (run !== state.tileRun) return;
    setTiles(t.url);
    renderLegend($("#legend"), t);
  } catch (e) {
    if (run === state.tileRun) toast(`Map layer failed: ${e.message}`);
  } finally {
    if (run === state.tileRun) $("#map-loading").classList.add("hidden");
  }
}

async function loadBoundary(f, stale) {
  try {
    const gj = await api("/api/boundary", query(f));
    if (!stale()) setBoundary(gj, f.district);
  } catch (e) { if (!stale()) toast(`Boundary failed: ${e.message}`); }
}

async function guarded(label, stale, fn) {
  try { const r = await fn(); return stale() ? null : r; }
  catch (e) { if (!stale()) toast(`${label} failed: ${e.message}`); return null; }
}

async function refreshAll() {
  const run = ++state.run;
  const stale = () => run !== state.run;
  const f = getFilters();
  $("#apply").disabled = true;
  resetPanels();

  const stats = guarded("Statistics", stale, () => api("/api/stats", query(f))).then((s) => { if (s) renderStats(s); return s; });
  const series = guarded("Trend charts", stale, () => api("/api/series", query(f))).then((s) => { if (s) renderSeries(s); return s; });
  const rain = guarded("Rainfall chart", stale, () => api("/api/rainfall", query(f))).then((r) => { if (r) renderRain("chart-rain", r.rows); });
  const ranking = guarded("District ranking", stale, () => api("/api/ranking", query(f))).then((r) => { if (r) renderRanking("chart-rank", r.rows); });
  const insights = Promise.all([stats, series]).then(([s, t]) => (s && t) ?
    guarded("Insights", stale, () => api("/api/insights", query(f))).then((i) => { if (i) renderInsights(i); }) : null);

  await Promise.allSettled([loadBoundary(f, stale), loadTiles(f), stats, series, rain, ranking, insights]);
  if (!stale()) $("#apply").disabled = false;
}

async function loadSources() {
  try {
    const { sources } = await api("/api/sources");
    const old = (d) => d && (Date.now() - new Date(d).getTime()) / 864e5 > 45;
    $("#source-chips").replaceChildren(...sources.slice(0, 3).map((s) => {
      const c = document.createElement("span");
      c.className = "chip" + (old(s.latest) ? " stale" : "");
      c.title = s.latest ? `Latest image: ${s.latest}` : s.note;
      const dot = document.createElement("i");
      c.append(dot, s.name);
      return c;
    }));
    $("#hub-list").replaceChildren(...sources.map((s) => {
      const li = document.createElement("li");
      const left = document.createElement("div");
      const n = document.createElement("b"); n.textContent = s.name;
      const small = document.createElement("small"); small.textContent = s.note;
      left.append(n, small);
      const right = document.createElement("span");
      right.textContent = s.latest ? `Latest ${s.latest}` : "Static";
      li.append(left, right);
      return li;
    }));
  } catch (e) { /* freshness chips are optional */ }
}

async function loadDistricts(selected) {
  const f = getFilters();
  const { districts } = await api("/api/districts", { province: f.province });
  setDistricts(districts, selected);
}

// ----------------------------------------------------------------- init
function pickLayer(key) {
  state.layer = key;
  setActiveLayer(key);
  // keep the top nav in step: highlight a view only while its layer is the one on the map
  document.querySelectorAll(".nav-btn[data-view]").forEach((b) => b.classList.toggle("active", VIEW_LAYER[b.dataset.view] === key));
  renderClasses();
  loadTiles(getFilters());
}

function onPickDistrict(name) {
  if (selectDistrict(name)) refreshAll();
}

async function init() {
  initCharts();
  initMap(onPickDistrict);
  document.querySelectorAll(".nav-btn[data-view]").forEach((b) => b.addEventListener("click", () => pickLayer(VIEW_LAYER[b.dataset.view])));
  $("#open-hub").addEventListener("click", () => $("#hub-dialog").showModal());
  $("#hub-close").addEventListener("click", () => $("#hub-dialog").close());
  $("#home-btn").addEventListener("click", fitHome);
  $("#basemap-btn").addEventListener("click", toggleBasemap);
  $("#opacity").addEventListener("input", (e) => setOpacity(e.target.value));

  try {
    state.meta = await api("/api/meta");
    initFilters(state.meta, {
      onApply: refreshAll,
      onProvince: () => loadDistricts().catch((e) => toast(e.message)),
    });
    buildLayerPanel(state.meta.layers, state.layer, pickLayer);
    await loadDistricts();
    loadSources();
    await refreshAll();
  } catch (e) {
    toast(`Cannot reach the backend at ${API_BASE}. ${e.message}`);
  }
}

init();
