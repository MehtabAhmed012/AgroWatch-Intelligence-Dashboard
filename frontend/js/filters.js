// Filter bar + map layer tabs.
const $ = (id) => document.getElementById(id);

function fill(select, items) {
  select.replaceChildren(...items.map(([label, value]) => new Option(label, value)));
}

export function initFilters(meta, handlers) {
  const hasProvince = meta.provinces.length > 0;
  $("province-field").hidden = !hasProvince;
  if (hasProvince) {
    fill($("province"), meta.provinces.map((p) => [p, p]));
    $("province").value = meta.provinces.includes(meta.default_province) ? meta.default_province : meta.provinces[0];
    $("province").addEventListener("change", handlers.onProvince);
  }
  fill($("year"), meta.years.slice().reverse().map((y) => [String(y), String(y)]));
  $("year").value = String(meta.default_year);

  const seg = $("season-seg");
  seg.replaceChildren(...meta.seasons.map((s, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.season = s.id;
    b.textContent = s.label.split(" ")[0];
    b.title = s.label;
    if (i === 0) b.classList.add("on");
    b.addEventListener("click", () => {
      seg.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    });
    return b;
  }));

  $("apply").addEventListener("click", handlers.onApply);
}

export function setDistricts(list, selected) {
  fill($("district"), [["All districts", ""], ...list.map((d) => [d, d])]);
  $("district").value = selected && list.includes(selected) ? selected : "";
}

export function selectDistrict(name) {
  const sel = $("district");
  if ([...sel.options].some((o) => o.value === name)) { sel.value = name; return true; }
  return false;
}

export function getFilters() {
  const on = document.querySelector("#season-seg button.on");
  return {
    province: $("province-field").hidden ? "" : $("province").value,
    district: $("district").value,
    year: $("year").value,
    season: on ? on.dataset.season : "kharif",
    crop: $("crop").checked,
  };
}

// Left-hand layer panel. Layer keys come from the backend (`LAYERS` in gee_processor.py);
// any key not listed here still appears, under "Other".
const LAYER_GROUPS = [
  ["Drought severity", ["stress", "adsi", "vci_stress"]],
  ["Vegetation", ["s2_ndvi", "s2_evi", "s2_ndre", "modis_ndvi", "anomaly", "vci", "lai", "gpp", "trend"]],
  ["Rainfall", ["rain", "rain_pct", "spi"]],
  ["Heat and soil water", ["lst", "tci", "vhi", "smci", "esi_anom"]],
];

export function buildLayerPanel(layers, active, onPick) {
  const wrap = $("layer-list");
  const groups = LAYER_GROUPS.map(([name, keys]) => [name, keys.filter((k) => layers[k])]);
  const used = new Set(groups.flatMap(([, keys]) => keys));
  const rest = Object.keys(layers).filter((k) => !used.has(k));
  if (rest.length) groups.push(["Other", rest]);

  const nodes = [];
  for (const [name, keys] of groups) {
    if (!keys.length) continue;
    const h = document.createElement("div");
    h.className = "layer-group";
    h.textContent = name;
    nodes.push(h);
    for (const key of keys) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "layer-btn" + (key === active ? " active" : "");
      b.dataset.layer = key;
      b.textContent = layers[key].label;
      b.title = layers[key].title;
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", String(key === active));
      b.addEventListener("click", () => onPick(key));
      nodes.push(b);
    }
  }
  wrap.replaceChildren(...nodes);
}

export function setActiveLayer(key) {
  document.querySelectorAll("#layer-list .layer-btn").forEach((b) => {
    const on = b.dataset.layer === key;
    b.classList.toggle("active", on);
    b.setAttribute("aria-checked", String(on));
  });
}
