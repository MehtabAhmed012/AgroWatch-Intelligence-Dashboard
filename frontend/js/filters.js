// Filter bar + map layer tabs.
const $ = (id) => document.getElementById(id);

function fill(select, items) {
  select.replaceChildren(...items.map(([label, value]) => new Option(label, value)));
}

// ASI weight state is shared by the map (tiles) and the Agricultural Stress panel.
export const asi = { keys: [], weights: [], pca: false, custom: false };
export function setAsiWeights(w, pca) { asi.weights = w; asi.pca = !!pca; asi.custom = true; }

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
  asi.keys = meta.asi.keys;
  asi.weights = meta.asi.keys.map((k) => meta.asi.weights[k]);

  const seg = $("season-seg");
  const seasons = [...meta.seasons, { id: "custom", label: "Custom window" }];
  seg.replaceChildren(...seasons.map((s, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.season = s.id;
    b.textContent = s.label.split(" ")[0];
    b.title = s.label;
    if (i === 0) b.classList.add("on");
    b.addEventListener("click", () => {
      seg.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      $("custom-dates").classList.toggle("hidden", s.id !== "custom");
    });
    return b;
  }));
  $("alpha").addEventListener("input", (e) => { $("alpha-val").textContent = Number(e.target.value).toFixed(2); });
  $("apply").addEventListener("click", handlers.onApply);
}

export function setDistricts(list, selected) {
  fill($("district"), [["All districts", ""], ...list.map((d) => [d, d])]);
  $("district").value = selected && list.includes(selected) ? selected : "";
}

export function selectDistrict(name) {
  const sel = $("district");
  if ([...sel.options].some((o) => o.value === name)) { sel.value = name; $("bbox").value = ""; return true; }
  return false;
}

// "2001-05-01" -> "0501"
const md = (iso) => iso.slice(5, 7) + iso.slice(8, 10);

export function parseBbox(text) {
  const parts = text.split(",").map((x) => Number(x.trim()));
  return parts.length === 4 && parts.every((x) => Number.isFinite(x)) ? parts : null;
}

export function getFilters() {
  const on = document.querySelector("#season-seg button.on");
  let season = on ? on.dataset.season : "kharif";
  if (season === "custom") {
    const a = $("cs-start").value, b = $("cs-end").value;
    season = a && b ? `c${md(a)}_${md(b)}` : "kharif";
  }
  const box = $("bbox").value.trim();
  const bbox = box ? parseBbox(box) : null;
  const w = asi.custom ? asi.weights.join(",") : "";
  return {
    province: $("province-field").hidden ? "" : $("province").value,
    district: bbox ? `bbox:${bbox.join(",")}` : $("district").value,
    districtName: $("district").value,
    bboxInvalid: !!box && !bbox,
    year: $("year").value,
    season,
    crop: $("crop").checked,
    alpha: $("alpha").value,
    vegsrc: $("vegsrc").value,
    w, pca: asi.pca ? 1 : 0,
  };
}

// Left-hand layer panel. Groups and layer metadata come from /api/meta.
export function buildLayerPanel(layers, groupOrder, active, onPick) {
  const wrap = $("layer-list");
  const byGroup = new Map(groupOrder.map((g) => [g, []]));
  for (const [key, meta] of Object.entries(layers)) {
    const g = meta.group || "Other";
    if (!byGroup.has(g)) byGroup.set(g, []);
    byGroup.get(g).push(key);
  }
  const nodes = [];
  for (const [name, keys] of byGroup) {
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
