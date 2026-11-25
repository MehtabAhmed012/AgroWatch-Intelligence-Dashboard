// Leaflet map: basemaps, Earth Engine tile layer, district outlines, legend.
let map, geeLayer, boundaryLayer, onPick;
let opacity = 0.85;
let current = "dark";
const base = {};

export function initMap(pickHandler) {
  onPick = pickHandler;
  map = L.map("map", { zoomControl: true }).setView([30.4, 69.4], 6);
  base.dark = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: "&copy; OpenStreetMap contributors &copy; CARTO", subdomains: "abcd", maxZoom: 19, zIndex: 1,
  });
  base.sat = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    attribution: "Tiles &copy; Esri", maxZoom: 19, zIndex: 1,
  });
  base.dark.addTo(map);
}

export function toggleBasemap() {
  map.removeLayer(base[current]);
  current = current === "dark" ? "sat" : "dark";
  base[current].addTo(map);
}

export function setTiles(url) {
  if (geeLayer) map.removeLayer(geeLayer);
  geeLayer = L.tileLayer(url, { opacity, maxZoom: 19, zIndex: 5 }).addTo(map);
}

export function setOpacity(value) {
  opacity = Number(value);
  if (geeLayer) geeLayer.setOpacity(opacity);
}

export function setBoundary(geojson, selected) {
  if (boundaryLayer) map.removeLayer(boundaryLayer);
  const single = geojson.features.length === 1;
  boundaryLayer = L.geoJSON(geojson, {
    style: (f) => ({
      color: f.properties.name === selected || single ? "#ffffff" : "#6fb1ff",
      weight: f.properties.name === selected || single ? 2.2 : 1,
      fillOpacity: 0,
    }),
    onEachFeature: (f, layer) => {
      const name = f.properties.name;
      if (name) layer.bindTooltip(name, { sticky: true });
      layer.on("mouseover", () => layer.setStyle({ weight: 2.5, color: "#22d3ee" }));
      layer.on("mouseout", () => boundaryLayer.resetStyle(layer));
      layer.on("click", () => name && onPick && onPick(name));
    },
  }).addTo(map);
  boundaryLayer.bringToFront();
  fitHome();
}

export function fitHome() {
  if (boundaryLayer) map.fitBounds(boundaryLayer.getBounds(), { padding: [24, 24] });
}

const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export function getViewBounds() {
  const b = map.getBounds();
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((x) => Math.round(x * 100) / 100);
}

// Legend + layer facts: title, units, class or gradient legend, period, dataset, resolution, no-data.
export function renderLegend(el, info) {
  if (!info) { el.innerHTML = ""; return; }
  const u = info.unit ? ` (${esc(info.unit)})` : "";
  const title = `<div class="legend-title">${esc(info.title)}${info.kind === "classes" ? "" : u}</div>`;
  let body;
  if (info.kind === "classes") {
    body = info.classes.map((c) =>
      `<div class="legend-row"><i style="background:${esc(c.color)}"></i>${esc(c.name)}<span>${esc(c.range)}</span></div>`).join("");
  } else {
    const { min, max, palette } = info.vis;
    body = `<div class="legend-bar" style="background:linear-gradient(90deg,${palette.map(esc).join(",")})"></div>` +
      `<div class="legend-scale"><span>${esc(min)}</span><span>${esc(max)}</span></div>`;
  }
  const i = info.info;
  const facts = i ? `<details class="legend-info"><summary>${esc(i.category)} | ${esc(info.period || "")}</summary>
      <dl><dt>Dataset</dt><dd>${esc(i.dataset)}</dd><dt>Resolution</dt><dd>${esc(i.resolution)}</dd>
      <dt>Period</dt><dd>${esc(info.period)}${i.temporal ? " (" + esc(i.temporal) + ")" : ""}</dd>
      <dt>No data</dt><dd>${esc(i.nodata)}</dd></dl></details>` : "";
  el.innerHTML = title + body + facts;
}
