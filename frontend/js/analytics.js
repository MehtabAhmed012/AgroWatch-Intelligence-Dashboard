// View panels: phenology, drought, rainfall, soil moisture, ET, stress index, districts, classification.
// Every number shown here comes from an /api/intel/* response; nothing is typed in.
import { drawChart, C } from "./charts.js";
import { asi, setAsiWeights } from "./filters.js";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmt = (n, d = 2) => (n == null || isNaN(n) ? "--" : Number(n).toFixed(d));
const fmtInt = (n) => (n == null || isNaN(n) ? "--" : Math.round(n).toLocaleString("en-US"));
const ADSI_COLORS = ["#1a9850", "#a6d96a", "#fee08b", "#f46d43", "#a50026"];

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function tiles(items) {
  const row = el("div", "tile-row");
  for (const t of items) {
    const d = el("div", "tile");
    d.dataset.tone = t.tone || "neutral";
    d.append(el("span", "tile-label", t.label), el("b", "tile-val", t.value), el("span", "tile-sub", t.sub || ""));
    row.append(d);
  }
  return row;
}

const note = (text, cls = "") => el("p", "panel-note " + cls, text);

function chartCard(id, title) {
  const card = el("article", "chart-card mini");
  const box = el("div", "chart-box");
  const cv = document.createElement("canvas");
  cv.id = id;
  box.append(cv);
  card.append(el("h3", "", title), box);
  return card;
}

function grid(...cards) {
  const g = el("div", "intel-grid");
  g.append(...cards);
  return g;
}

function table(headers, rows, opts = {}) {
  const t = el("table", "tbl");
  const head = el("tr");
  headers.forEach((h, i) => {
    const th = el("th", opts.sortable ? "sortable" : "", h);
    if (opts.sortable) th.addEventListener("click", () => opts.sortable(i));
    head.append(th);
  });
  const thead = el("thead");
  thead.append(head);
  t.append(thead);
  const body = el("tbody");
  for (const r of rows) {
    const tr = el("tr");
    r.forEach((c) => tr.append(el("td", "", c)));
    if (opts.onRow) tr.addEventListener("click", () => opts.onRow(r));
    body.append(tr);
  }
  t.append(body);
  const wrap = el("div", "tbl-wrap");
  wrap.append(t);
  return wrap;
}

const baseOpts = { responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false } };
const spiTone = (v) => (v == null ? "neutral" : v > -0.5 ? "good" : v > -1 ? "watch" : "bad");
const spiWord = (v) => (v == null ? "no value" : v <= -2 ? "extreme drought" : v <= -1.5 ? "severe drought" : v <= -1 ? "moderate drought" : v < 1 ? "near normal" : "wet");

// ------------------------------------------------------------------ phenology
async function phenology(body, ctx) {
  const d = await ctx.api("/api/intel/phenology", ctx.q());
  const m = d.median;
  body.append(tiles([
    { label: "Start of season (SOS)", value: d.SOS_date || "--", sub: m.SOS == null ? "" : `day ${Math.round(m.SOS)} of the season` },
    { label: "Peak of season (POS)", value: d.POS_date || "--", sub: m.POS == null ? "" : `day ${Math.round(m.POS)}` },
    { label: "End of season (EOS)", value: d.EOS_date || "--", sub: m.EOS == null ? "" : `day ${Math.round(m.EOS)}` },
    { label: "Length of season (LOS)", value: m.LOS == null ? "--" : `${Math.round(m.LOS)} days`, sub: "approximate" },
    { label: "Maximum NDVI", value: fmt(m.NDVImax), sub: "smoothed" },
    { label: "NDVI integral", value: fmt(m.NDVIint, 0), sub: "NDVI x days" },
  ]));
  body.append(grid(chartCard("p-curve", "Region NDVI through the season: selected year vs 2001-2018")));
  const pts = (rows) => rows.filter((r) => r.ndvi != null).map((r) => ({ x: r.off, y: r.ndvi }));
  drawChart("p-curve", { type: "line", data: { datasets: [
    { label: `${d.curve.year}`, data: pts(d.curve.current), borderColor: C.green, backgroundColor: C.green, tension: 0.3, pointRadius: 3, borderWidth: 2.5 },
    { label: "2001-2018 average", data: pts(d.curve.normal), borderColor: C.amber, backgroundColor: C.amber, borderDash: [6, 4], tension: 0.3, pointRadius: 0, borderWidth: 2 },
  ] }, options: { ...baseOpts, scales: { x: { type: "linear", title: { display: true, text: "Days since season start" } }, y: { title: { display: true, text: "NDVI" } } } } });
  body.append(note(`${d.note} Medians are over ${fmtInt(d.pixels)} cropland pixels at 500 m.`, "warn"));
}

// -------------------------------------------------------------------- drought
async function drought(body, ctx) {
  const [c, t] = await Promise.all([ctx.api("/api/intel/condition", ctx.q()), ctx.api("/api/intel/trend", ctx.q())]);
  const last = c.rows[c.rows.length - 1] || {};
  const tone = (v) => (v == null ? "neutral" : v >= 50 ? "good" : v >= 35 ? "watch" : "bad");
  body.append(tiles([
    { label: "VCI (vegetation)", value: fmt(last.VCI, 0), sub: "0-100, low = poor", tone: tone(last.VCI) },
    { label: "TCI (temperature)", value: fmt(last.TCI, 0), sub: "0-100, low = hot", tone: tone(last.TCI) },
    { label: `VHI (alpha ${fmt(c.alpha, 2)})`, value: fmt(last.VHI, 0), sub: "below 40 = drought", tone: tone(last.VHI) },
  ]));
  body.append(grid(chartCard("d-cond", "Historical drought comparison: VCI, TCI and VHI by season")));
  const labels = c.rows.map((r) => r.year);
  const line = (k, col) => ({ label: k, data: c.rows.map((r) => r[k]), borderColor: col, backgroundColor: col, tension: 0.25, pointRadius: 2, borderWidth: 2, spanGaps: true });
  drawChart("d-cond", { type: "line", data: { labels, datasets: [line("VCI", C.green), line("TCI", C.amber), line("VHI", C.cyan)] },
    options: { ...baseOpts, scales: { y: { min: 0, max: 100 }, x: { ticks: { autoSkip: true, maxTicksLimit: 12 } } } } });
  const rows = Object.entries(t.trend).map(([k, v]) => v.valid
    ? [k, v.direction, `${v.sen_slope >= 0 ? "+" : ""}${v.sen_slope.toPrecision(2)} per year`, v.p < 0.001 ? "<0.001" : fmt(v.p, 3), String(v.n)]
    : [k, "not tested", "--", "--", v.reason]);
  body.append(el("h3", "sub-h", `Mann-Kendall trend and Sen's slope, ${t.years[0]} to ${t.years[1]}`));
  body.append(table(["Series", "Direction (5%)", "Sen's slope", "p", "n / note"], rows));
  body.append(note(t.note));
}

// ------------------------------------------------------------------- rainfall
async function rain(body, ctx) {
  const d = await ctx.api("/api/intel/rain", ctx.q());
  body.append(tiles([1, 3, 6].map((n) => ({ label: `SPI-${n}`, value: fmt(d.spi[`SPI${n}`]), sub: spiWord(d.spi[`SPI${n}`]), tone: spiTone(d.spi[`SPI${n}`]) }))));
  body.append(grid(chartCard("r-anom", "Monthly rainfall anomaly (bars) and cumulative deficit (line), mm"),
                   chartCard("r-obs", "Monthly rainfall: observed vs 2001-2018 normal, mm")));
  const labels = d.rows.map((r) => r.label);
  drawChart("r-anom", { data: { labels, datasets: [
    { type: "bar", label: "Anomaly", data: d.rows.map((r) => r.anomaly), backgroundColor: d.rows.map((r) => (r.anomaly < 0 ? C.orange : C.blue)), borderRadius: 3 },
    { type: "line", label: "Cumulative deficit", data: d.rows.map((r) => r.cum_deficit), borderColor: C.red, backgroundColor: C.red, tension: 0.25, pointRadius: 2 },
  ] }, options: { ...baseOpts, scales: { y: { title: { display: true, text: "mm" } } } } });
  drawChart("r-obs", { data: { labels, datasets: [
    { type: "bar", label: "Observed", data: d.rows.map((r) => r.observed), backgroundColor: C.blue, borderRadius: 3 },
    { type: "line", label: "Normal", data: d.rows.map((r) => r.normal), borderColor: C.amber, backgroundColor: C.amber, tension: 0.3, pointRadius: 2 },
  ] }, options: { ...baseOpts, scales: { y: { title: { display: true, text: "mm" } } } } });
  body.append(note(`SPI windows end ${d.spi_window_end} (last complete month) and use a gamma fit to the ${d.reference[0]}-${d.reference[1]} reference. CHIRPS is about 5.5 km, so regional means hide local rainfall.`));
}

// ---------------------------------------------------------------- soil moisture
async function soil(body, ctx) {
  const d = await ctx.api("/api/intel/soil", ctx.q());
  const zt = d.z == null ? "neutral" : d.z > -0.5 ? "good" : d.z > -1 ? "watch" : "bad";
  body.append(tiles([
    { label: "Surface soil moisture", value: d.surface == null ? "--" : `${fmt(d.surface, 3)}`, sub: "m3/m3, 0-5 cm" },
    { label: "Root-zone soil moisture", value: d.rootzone == null ? "--" : `${fmt(d.rootzone, 3)}`, sub: "m3/m3, 0-100 cm" },
    { label: "Root-zone anomaly", value: d.anomaly == null ? "n/a" : `${d.anomaly >= 0 ? "+" : ""}${fmt(d.anomaly, 3)}`, sub: d.baseline_years ? `vs ${d.baseline_years[0]}-${d.baseline_years[1]}` : "needs 5 earlier seasons", tone: zt },
    { label: "Standardised (z)", value: fmt(d.z), sub: "below -1 = water stress", tone: zt },
  ]));
  body.append(grid(chartCard("s-month", "Monthly root-zone soil moisture: selected year vs earlier-year normal")));
  const labels = MONTHS;
  drawChart("s-month", { type: "line", data: { labels, datasets: [
    { label: "Selected year", data: d.rows.map((r) => r.observed), borderColor: C.cyan, backgroundColor: C.cyan, tension: 0.3, pointRadius: 3, borderWidth: 2.5, spanGaps: true },
    { label: "Normal", data: d.rows.map((r) => r.normal), borderColor: C.amber, backgroundColor: C.amber, borderDash: [6, 4], tension: 0.3, pointRadius: 0, borderWidth: 2, spanGaps: true },
  ] }, options: { ...baseOpts, scales: { y: { title: { display: true, text: "m3/m3" } } } } });
  body.append(note(d.resolution_note, "warn"));
}

// ------------------------------------------------------------ evapotranspiration
async function et(body, ctx) {
  const d = await ctx.api("/api/intel/et", ctx.q());
  const m = d.means;
  body.append(tiles([
    { label: "Actual ET", value: fmt(m.ET, 0), sub: "mm per season" },
    { label: "Potential ET", value: fmt(m.PET, 0), sub: "mm per season" },
    { label: "ET / PET", value: fmt(m.ETPET), sub: "low = water-limited", tone: m.ETPET == null ? "neutral" : m.ETPET >= 0.35 ? "good" : m.ETPET >= 0.2 ? "watch" : "bad" },
    { label: "ET anomaly", value: m.ET_ANOM == null ? "--" : `${m.ET_ANOM >= 0 ? "+" : ""}${fmt(m.ET_ANOM, 0)} mm`, sub: "vs 2001-2018" },
    { label: "Possible joint stress", value: `${fmt(d.joint_pct, 1)}%`, sub: `${fmtInt(d.joint_ha)} ha vegetation and ET both low`, tone: d.joint_pct < 10 ? "good" : d.joint_pct < 25 ? "watch" : "bad" },
  ]));
  body.append(table(["Class", "Rule", "Area (ha)", "Share"], d.classes.map((c) => [c.name, c.range, fmtInt(c.ha), `${fmt(c.pct, 1)}%`])));
  body.append(note(d.limitations, "warn"));
}

// ------------------------------------------------- agricultural stress index (ASI)
async function asiPanel(body, ctx) {
  const form = el("div", "asi-form");
  const inputs = asi.keys.map((k, i) => {
    const lab = el("label", "", ctx.meta.asi.names[k].split(" (")[0]);
    const inp = document.createElement("input");
    inp.type = "number"; inp.min = "0"; inp.max = "1"; inp.step = "0.05"; inp.value = String(asi.weights[i]);
    lab.append(inp);
    return inp;
  });
  const pca = document.createElement("input"); pca.type = "checkbox"; pca.checked = asi.pca;
  const pcaLab = el("label", "inline-check", "Use PCA weights instead "); pcaLab.prepend(pca);
  const go = el("button", "btn-primary", "Apply weights");
  go.addEventListener("click", () => {
    setAsiWeights(inputs.map((x) => Math.max(0, Number(x.value) || 0)), pca.checked);
    ctx.reloadView();
  });
  const reset = el("button", "btn-ghost", "Default weights");
  reset.addEventListener("click", () => { asi.custom = false; asi.pca = false; asi.weights = asi.keys.map((k) => ctx.meta.asi.weights[k]); ctx.reloadView(); });
  form.append(...inputs.map((i) => i.parentElement), pcaLab, go, reset);
  body.append(form);
  const holder = el("div", "asi-out", "Calculating from Earth Engine...");
  body.append(holder);

  const [a, s, h] = await Promise.allSettled([ctx.api("/api/intel/asi", ctx.q()), ctx.api("/api/intel/asi_series", ctx.q()), ctx.api("/api/intel/hotspots", ctx.q())]);
  holder.replaceChildren();
  if (a.status === "rejected") { holder.append(note(`ASI failed: ${a.reason.message}`, "warn")); return; }
  const d = a.value;
  const tone = d.asi == null ? "neutral" : d.asi < 0.15 ? "good" : d.asi < 0.30 ? "watch" : "bad";
  holder.append(tiles([
    { label: "Mean ASI", value: fmt(d.asi), sub: "0 none, 1 severe", tone },
    { label: "Cropland under stress", value: `${fmt(d.stressed_pct, 1)}%`, sub: "moderate stress or worse", tone: d.stressed_pct < 10 ? "good" : d.stressed_pct < 25 ? "watch" : "bad" },
    { label: "Area under stress", value: `${fmtInt(d.stressed_ha)} ha`, sub: `of ${fmtInt(d.monitored_ha)} ha valid cropland` },
    { label: "Weights", value: d.method, sub: d.components.map((c) => `${c.key} ${fmt(c.weight, 2)}`).join(", ") },
  ]));
  holder.append(grid(chartCard("a-comp", "Component stress scores (0 none, 1 maximum)"), chartCard("a-series", "ASI by season (each year vs its own reference)")));
  drawChart("a-comp", { type: "bar", data: { labels: d.components.map((c) => c.name.split(" (")[0]),
    datasets: [{ label: "Score", data: d.components.map((c) => c.score), backgroundColor: d.components.map((c) => ADSI_COLORS[Math.min(4, Math.floor((c.score ?? 0) / 0.2))]), borderRadius: 3 }] },
    options: { ...baseOpts, indexAxis: "y", plugins: { legend: { display: false } }, scales: { x: { min: 0, max: 1 } } } });
  if (s.status === "fulfilled") {
    const rows = s.value.rows;
    drawChart("a-series", { type: "bar", data: { labels: rows.map((r) => r.year), datasets: [{ label: "ASI", data: rows.map((r) => r.ASI),
      backgroundColor: rows.map((r) => ADSI_COLORS[(r.ASI ?? 0) >= 0.70 ? 4 : (r.ASI ?? 0) >= 0.50 ? 3 : (r.ASI ?? 0) >= 0.30 ? 2 : (r.ASI ?? 0) >= 0.15 ? 1 : 0]), borderRadius: 3 }] },
      options: { ...baseOpts, plugins: { legend: { display: false } }, scales: { y: { min: 0, max: 1 } } } });
  }
  holder.append(el("h3", "sub-h", "Components and standardised anomalies"));
  holder.append(table(["Component", "Weight", "Mean score", "Mean z (+ = adverse)"], d.components.map((c) => [c.name, fmt(c.weight, 2), fmt(c.score), fmt(c.z)])));
  holder.append(el("h3", "sub-h", "Cropland area by stress class"));
  holder.append(table(["Class", "Range", "Area (ha)", "Share"], d.classes.map((c) => [c.name, c.range, fmtInt(c.ha), `${fmt(c.pct, 1)}%`])));
  if (h.status === "fulfilled") {
    holder.append(el("h3", "sub-h", `Spatial stress hotspots (Gi*): ${fmt(h.value.hot_pct, 1)}% of cropland in a hot spot`));
    holder.append(table(["Class", "Rule", "Area (ha)", "Share"], h.value.classes.map((c) => [c.name, c.range, fmtInt(c.ha), `${fmt(c.pct, 1)}%`])));
    holder.append(note(h.value.note));
  } else holder.append(note(`Hotspots failed: ${h.reason.message}`, "warn"));
  holder.append(note(d.validation, "warn"));
  holder.append(note("ASI is a decision-support composite, not a universally validated drought measurement. Open Methodology for every formula and assumption."));
}

// ---------------------------------------------------------------- district table
const DCOLS = [["District", "district"], ["Cropland (ha)", "crop_ha"], ["% of district", "crop_pct_of_district"], ["NDVI", "ndvi"], ["VCI", "vci"],
  ["Rain (mm)", "rain"], ["Rain % normal", "rain_pct"], ["Root-zone SM", "sm_root"], ["ASI", "asi"], ["% cropland stressed", "stress_pct"], ["Stressed (ha)", "stressed_ha"]];

function csv(rows) {
  const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  return [DCOLS.map((c) => q(c[0])).join(","), ...rows.map((r) => DCOLS.map((c) => q(r[c[1]])).join(","))].join("\n");
}

async function districts(body, ctx) {
  const [d, agree] = await Promise.all([ctx.api("/api/intel/districts", ctx.q()),
    ctx.api("/api/intel/crop_agreement", ctx.q()).catch(() => null)]);
  let sortKey = "asi", asc = false;
  const holder = el("div");
  const draw = () => {
    const rows = d.rows.slice().sort((a, b) => ((a[sortKey] ?? -Infinity) - (b[sortKey] ?? -Infinity)) * (asc ? 1 : -1)
      || String(a.district).localeCompare(String(b.district)));
    const fm = { crop_ha: fmtInt, stressed_ha: fmtInt, rain: (v) => fmt(v, 0), rain_pct: (v) => fmt(v, 0), vci: (v) => fmt(v, 0),
      stress_pct: (v) => fmt(v, 1), crop_pct_of_district: (v) => fmt(v, 1), sm_root: (v) => fmt(v, 3), district: (v) => v };
    const t = table(DCOLS.map((c) => c[0]), rows.map((r) => DCOLS.map((c) => (fm[c[1]] || fmt)(r[c[1]]))),
      { sortable: (i) => { const k = DCOLS[i][1]; asc = k === sortKey ? !asc : k === "district"; sortKey = k; draw(); }, onRow: (r) => ctx.onPickDistrict(r[0]) });
    holder.replaceChildren(t);
  };
  draw();
  const bar = el("div", "tbl-actions");
  const dl = el("button", "btn-ghost", "Download CSV");
  dl.addEventListener("click", () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv(d.rows)], { type: "text/csv" }));
    a.download = `agrowatch_districts_${ctx.f.year}_${ctx.f.season}.csv`; a.click(); URL.revokeObjectURL(a.href);
  });
  bar.append(dl, el("span", "intel-sub", `${d.rows.length} districts. Click a header to sort, a row to open the district.`));
  body.append(bar, holder);
  body.append(note(d.note));
  if (agree && agree.agreement_pct != null) {
    body.append(note(`Cropland mask check: WorldCover and Dynamic World agree on ${fmt(agree.agreement_pct, 0)}% of the cropland either one shows (${fmtInt(agree.both_ha)} ha shared, ${fmtInt(agree.worldcover_only_ha)} ha WorldCover only, ${fmtInt(agree.dynamicworld_only_ha)} ha Dynamic World only). ${agree.note}`));
  }
}

// ----------------------------------------------------------- classification
async function classify(body, ctx) {
  body.append(note("Random Forest on Sentinel-2 bands, indices and Sentinel-1 backscatter. No training data is bundled: supply an Earth Engine FeatureCollection with an integer class column. Accuracy is computed only from your samples.", "warn"));
  const form = el("div", "asi-form");
  const mk = (label, ph, val = "") => { const l = el("label", "", label); const i = document.createElement("input"); i.placeholder = ph; i.value = val; l.append(i); form.append(l); return i; };
  const samples = mk("Training samples asset", "projects/<project>/assets/samples");
  const label = mk("Class column", "class", "class");
  const val = mk("Independent validation asset (optional)", "leave empty for a spatial 70/30 block split");
  const run = el("button", "btn-primary", "Run classification");
  form.append(run);
  const out = el("div", "asi-out");
  body.append(form, out);
  run.addEventListener("click", async () => {
    if (!samples.value.trim()) { out.replaceChildren(note("Enter the samples asset path first.", "warn")); return; }
    out.replaceChildren(note("Training and validating in Earth Engine. This can take a minute..."));
    run.disabled = true;
    try {
      const r = await ctx.api("/api/intel/classify", ctx.q({ samples: samples.value.trim(), label_field: label.value.trim() || "class", val_samples: val.value.trim() }));
      ctx.showTiles({ ...r, kind: "classes", title: "Random Forest classification", unit: "", period: "", info: { category: "Machine-learning prediction", dataset: "Sentinel-2 + Sentinel-1, your samples", resolution: "10 m", nodata: "Masked outside the region / cropland mask.", temporal: "Season composite" } });
      const labs = r.labels;
      out.replaceChildren(tiles([
        { label: "Overall accuracy", value: `${fmt(100 * r.overall_accuracy, 1)}%`, sub: `${r.n_val} validation samples` },
        { label: "Kappa", value: fmt(r.kappa, 3), sub: r.split },
        { label: "Training samples", value: String(r.n_train), sub: "after spatial split" },
      ]));
      out.append(el("h3", "sub-h", "Per-class accuracy"));
      out.append(table(["Class", "Producer's accuracy", "User's accuracy", "F1"], labs.map((l, i) => [String(l), fmt(r.producers_accuracy[i], 3), fmt(r.users_accuracy[i], 3), fmt(r.f1[i], 3)])));
      out.append(el("h3", "sub-h", "Confusion matrix (rows = reference, columns = predicted)"));
      out.append(table(["", ...labs.map(String)], r.matrix.map((row, i) => [String(labs[i]), ...row.map(String)])));
      out.append(note(r.disclaimer, "warn"));
    } catch (e) { out.replaceChildren(note(`Classification failed: ${e.message}`, "warn")); }
    finally { run.disabled = false; }
  });
}

// ---------------------------------------------------------------- recommendations
// Rule-based advice. Every card cites the live indicator that triggered it; thresholds follow the
// tone rules used in the KPI cards. This is decision support, not an agronomic prescription.
function buildActions(s, a, dist) {
  const m = s.means || {};
  const sp = s.stressed_pct, spi = m.SPI, rainPct = m.rain_pct, smci = m.SMCI, tci = m.TCI, vci = m.VCI;
  const dry = (spi != null && spi <= -1) || (rainPct != null && rainPct < 75);
  const soilLow = smci != null && smci < 35;
  const hot = tci != null && tci < 35;
  const weak = vci != null && vci < 35;
  const high = sp != null && sp >= 25, some = sp != null && sp >= 10 && sp < 25;
  const ev = (...p) => p.filter(Boolean).join("; ");
  const evRain = (spi != null || rainPct != null) ? `SPI ${fmt(spi)}, rainfall ${fmt(rainPct, 0)}% of normal` : null;
  const evSoil = smci != null ? `soil-moisture condition (SMCI) ${fmt(smci, 0)}` : null;
  const evTemp = tci != null ? `temperature condition (TCI) ${fmt(tci, 0)}` : null;
  const evVeg = vci != null ? `crop health (VCI) ${fmt(vci, 0)}` : null;
  const evStress = sp != null ? `${fmt(sp, 1)}% of cropland (${fmtInt(s.stressed_ha)} ha) under moderate stress or worse` : null;
  const policy = [], farmer = [];

  if (high) policy.push({ tone: "bad", title: "Issue a drought advisory and activate early response",
    text: "Alert district agriculture and disaster offices, pre-position seed, fodder and input support, and start a field-verified damage assessment before relief or compensation decisions.", why: evStress });
  else if (some) policy.push({ tone: "watch", title: "Move to heightened watch",
    text: "Share the district ranking with extension offices and repeat the analysis every 2 to 4 weeks while the season runs.", why: evStress });
  else policy.push({ tone: "good", title: "Keep routine monitoring",
    text: "Stress is limited. Use this quieter period to collect ground observations and crop-loss records so the index can be validated.", why: evStress });
  if (dry) policy.push({ tone: "bad", title: "Review water allocation and storage",
    text: "Check reservoir and canal releases against the rainfall deficit, and prioritise delivery to the stressed districts at critical crop stages.", why: evRain });
  if (soilLow) policy.push({ tone: "watch", title: "Support groundwater-based irrigation",
    text: "Where canal supply is short, consider power and fuel support for pumping and monitor groundwater levels so that support does not cause over-extraction.", why: ev(evSoil, "SMAP is 9 km, so confirm locally") });
  if (hot) policy.push({ tone: "watch", title: "Prepare heat-stress measures",
    text: "Issue heat advisories to farmers and plan for heat-tolerant varieties and adjusted sowing windows in next season's input programmes.", why: evTemp });
  const top = (dist || []).filter((r) => r.asi != null).sort((x, y) => y.asi - x.asi).slice(0, 5);
  if (top.length) policy.push({ tone: "watch", title: "Target the highest-stress districts first",
    text: `Priority list: ${top.map((r) => `${r.district} (ASI ${fmt(r.asi)}, ${fmtInt(r.stressed_ha)} ha)`).join("; ")}.`, why: "district ranking by mean ASI" });
  policy.push({ tone: "neutral", title: "Use for insurance and planning only after validation",
    text: "Seasonal like-for-like comparison is repeatable, but ASI is not yet validated against crop-loss data. Do not trigger payouts from it alone.", why: a && a.validation ? "ASI validation pending" : "" });

  if (dry || soilLow) farmer.push({ tone: "bad", title: "Save water for the critical crop stages",
    text: "Put scarce irrigation into flowering and grain or fruit fill, use mulch or residue cover to hold soil moisture, and avoid sowing extra water-hungry crops this season.", why: ev(evRain, evSoil) });
  if (hot) farmer.push({ tone: "watch", title: "Irrigate in the cooler hours",
    text: "Irrigate early morning or evening to cut evaporation, and consider heat-tolerant varieties or earlier sowing next season.", why: evTemp });
  if (weak && !dry && !soilLow) farmer.push({ tone: "watch", title: "Look beyond water",
    text: "Crops are weak although rainfall and soil moisture are not low. Scout for pests, disease, nutrient shortage or salinity.", why: evVeg });
  else if (weak) farmer.push({ tone: "watch", title: "Scout fields now",
    text: "Weak crop health can come from water stress, pests or nutrients. Check a few fields in each block before spending on inputs.", why: evVeg });
  if (!dry && !soilLow && !hot && !weak) farmer.push({ tone: "good", title: "Keep to your normal schedule",
    text: "Indicators are near or above normal. Keep irrigation and fertiliser on schedule and keep scouting.", why: ev(evRain, evVeg) });
  farmer.push({ tone: "neutral", title: "Check on the ground",
    text: "This map tells you where to look. Soil moisture and rainfall are 5 to 9 km products, so they describe the area, not your field. Ask your extension officer about local advice.", why: "" });
  return { policy, farmer };
}

function recCol(title, sub, items) {
  const col = el("section", "rec-col");
  col.append(el("h3", "", title), el("p", "rec-sub", sub));
  for (const r of items) {
    const c = el("article", "rec");
    c.dataset.tone = r.tone;
    c.append(el("b", "", r.title), el("p", "", r.text));
    if (r.why) c.append(el("span", "rec-why", `Based on: ${r.why}`));
    col.append(c);
  }
  return col;
}

async function actions(body, ctx) {
  const [st, as, di, ins] = await Promise.allSettled([ctx.api("/api/stats", ctx.q()), ctx.api("/api/intel/asi", ctx.q()),
    ctx.api("/api/intel/districts", ctx.q()), ctx.api("/api/insights", ctx.q())]);
  if (st.status === "rejected") { body.append(note(`Recommendations need the regional statistics: ${st.reason.message}`, "warn")); return; }
  const { policy, farmer } = buildActions(st.value, as.status === "fulfilled" ? as.value : null, di.status === "fulfilled" ? di.value.rows : null);
  const risk = ins.status === "fulfilled" ? ins.value.risk : null;
  body.append(tiles([
    { label: "Drought risk", value: risk ? `${risk}` : "--", sub: "from ADSI and stressed area", tone: risk === "Low" ? "good" : risk === "Moderate" ? "watch" : risk ? "bad" : "neutral" },
    { label: "Cropland under stress", value: `${fmt(st.value.stressed_pct, 1)}%`, sub: `${fmtInt(st.value.stressed_ha)} ha`, tone: st.value.stressed_pct < 10 ? "good" : st.value.stressed_pct < 25 ? "watch" : "bad" },
  ]));
  const g = el("div", "rec-grid");
  g.append(recCol("For policymakers", "Planning, allocation and response", policy), recCol("For farmers", "On-farm decisions this season", farmer));
  body.append(g);
  body.append(note("These suggestions are generated from the indicators for the region, season and filters currently selected, so they change when you press Update. They are decision support and are not a substitute for local agronomic or official advice.", "warn"));
}

export const PANELS = {
  phenology: ["Crop phenology intelligence", "Approximate dates from 16-day MODIS composites", phenology],
  drought: ["Drought intelligence", "VCI, TCI, VHI and long-term trend", drought],
  rain: ["Rainfall monitoring", "SPI-1/3/6, monthly anomaly, cumulative deficit", rain],
  soil: ["Soil moisture intelligence", "SMAP Level 4, native 9 km", soil],
  et: ["Evapotranspiration and crop water stress", "MOD16 500 m model product", et],
  asi: ["Agricultural Stress Index", "Decision-support composite, validation pending", asiPanel],
  districts: ["District agricultural intelligence", "Cropland-based statistics for every district", districts],
  actions: ["Recommendations", "Suggested actions for policymakers and farmers, from the current indicators", actions],
  classify: ["Agricultural land and crop classification", "Machine-learning prediction from your samples", classify],
};

export async function loadPanel(name, ctx) {
  const [title, sub, fn] = PANELS[name];
  const panel = document.getElementById("intel-panel"), body = document.getElementById("intel-body");
  panel.classList.remove("hidden");
  document.getElementById("intel-title").textContent = title;
  document.getElementById("intel-sub").textContent = sub;
  const stamp = (ctx.token = (ctx.token || 0) + 1);
  // The new container is attached first so Chart.js can find its canvases by id. A newer request
  // replaces it, which discards anything an older request is still drawing.
  const fresh = el("div");
  const loading = note("Loading from Earth Engine...");
  fresh.append(loading);
  body.replaceChildren(fresh);
  try {
    await fn(fresh, ctx);
  } catch (e) {
    if (stamp === ctx.token) fresh.append(note(`${e.message}`, "warn"));
  }
  loading.remove();
}

export function hidePanel() {
  document.getElementById("intel-panel").classList.add("hidden");
}
