# AgroWatch Intelligence - From satellite observations to agricultural decision intelligence

Which agricultural regions are experiencing vegetation and drought stress, how bad is it, and where should attention go first?

A Flask API (Python 3.15) runs Google Earth Engine (GEE) analysis. A static dashboard (Leaflet map, Chart.js charts) shows the results. Version 3 adds ten analysis modules, 60 map layers, a composite Agricultural Stress Index, exports, and a methodology register that says what is observed, derived, modelled or only estimated.

```
Browser (frontend)  --fetch-->  Flask API (backend)  --ee.*-->  Google Earth Engine
 map, KPIs, charts              gee_processor.py (v2 core)       Sentinel-1/2, Landsat 8/9, MODIS, CHIRPS,
 11 views, analysis panel       gee_intel.py (v3 modules)        ERA5-Land, SMAP L4, WorldCover, Dynamic World,
 export, methodology            methodology.py (documentation)   your District_Boundary
```

## What changed in v3

| Area | New |
|---|---|
| Module 1 Vegetation | SAVI, NDMI, red-edge CIre, Landsat 8/9 NDVI, Dynamic World crop probability, dataset selector (Sentinel-2, MODIS, Landsat) |
| Module 2 Phenology | SOS, POS, EOS, LOS, maximum NDVI and NDVI integral per pixel, plus a region curve against the 2001-2018 average. Marked approximate |
| Module 3 Meteorological drought | SPI-1, SPI-3, SPI-6 (gamma fit, 1991-2020 reference), SPI drought classes, monthly anomaly, cumulative deficit |
| Module 4 Agricultural drought | VCI, TCI, VHI with a configurable alpha, VHI drought classes, Landsat LST and TCI, historical VCI/TCI/VHI chart, Mann-Kendall and Sen's slope table |
| Module 5 Soil moisture | SMAP L4 surface and root-zone moisture at native 9 km, anomaly, water-stress zones, monthly series |
| Module 6 ET | Seasonal ET and PET (mm), ET/PET, ET anomaly, joint vegetation + ET stress map |
| Module 7 Classification | Random Forest on Sentinel-2 + Sentinel-1 with your samples, spatial block validation, confusion matrix, OA, producer's and user's accuracy, kappa, F1 |
| Module 8 Stress index | ASI from five components, manual or PCA weights, component maps, five classes |
| Module 9 Hotspots and trends | Getis-Ord Gi* hotspots, district ranking, Mann-Kendall and Sen's slope (maps and regional series) |
| Module 10 Districts | Sortable table with cropland-based areas and stress share, CSV download |
| Radar | Sentinel-1 VV, VH, VV/VH and a VV change layer for waterlogging investigation (not soil moisture) |
| Interface | 11 navigation views (incl. Recommendations for policymakers and farmers, rule-based from the live indicators), custom season window, custom region box, partial-season banner, layer facts (dataset, period, resolution, no-data), methodology dialog |
| Exports | Image and district-table export tasks with resolution guard, CRS, folder and name options |
| Quality | Offline unit tests, graph smoke test, dataset verifier, methodology register |

Honest status: the Earth Engine code could not be run against live Earth Engine while this version was written. It is covered by offline tests (statistics, season logic, request validation, and a build of every layer graph against a mock) but you must run `python verify_datasets.py` and the checklist in `docs/TESTING.md` with your own credentials before relying on a number. ASI has not been validated against crop-loss or yield data.

## Project structure

```
agrowatch-dashboard/
├── gee/AgroWatch.js          Code Editor version of the v2 analysis (not yet extended with the v3 modules)
├── frontend/
│   ├── index.html            nav (10 views), filter bar, layer panel + map, KPI cards, charts, analysis panel, dialogs
│   ├── css/style.css
│   ├── js/main.js            API calls, KPI cards, views, banner, export and methodology dialogs
│   ├── js/analytics.js       phenology, drought, rainfall, soil, ET, ASI, district and classification panels
│   ├── js/map.js             Leaflet: basemaps, GEE tiles, outlines, legend with layer facts
│   ├── js/charts.js, filters.js
│   └── assets/favicon.svg
├── backend/
│   ├── app.py                routes, validation, errors
│   ├── gee_processor.py      v2 core: seasons, boundary, VCI/SPI/TCI/SMCI/ADSI, stats, insights
│   ├── gee_intel.py          v3 modules, layer registry, tiles, exports
│   ├── methodology.py        formulas, datasets, limitations (served at /api/methodology)
│   ├── verify_datasets.py    checks dataset IDs, bands, gammainc order, boundary columns
│   ├── authentication.py, requirements.txt, .env.example
├── tests/                    test_stats.py, test_api.py, smoke_graph.py
├── tools/make_docs.py        regenerates docs/METHODOLOGY.md from methodology.py
├── docs/                     METHODOLOGY.md, TESTING.md, GISDAY_DEMO.md
├── .python-version, runtime.txt
└── README.md
```

## Data types

Every layer in the legend panel is labelled with one of: direct observation, derived index, statistical estimate, model product, machine-learning prediction, or decision-support indicator. Open **Methodology** in the app, or read `docs/METHODOLOGY.md`, for the formula, dataset, native resolution, temporal resolution, reference period, quality filtering, processing and limitations of each indicator.

Rules the code follows:
- Seasons are compared like for like. A running season is cut at today minus 16 days, and the same shortened window is used for every baseline year. A banner says so.
- Cropland statistics use valid cropland pixels only (`CROP_SOURCE` = worldcover, dynamicworld, intersect or union). Missing pixels are never shown or exported as zero.
- Native resolutions are respected: SMAP, ERA5-Land and CHIRPS are not presented as field-scale data.
- Kharif and Rabi dates are configurable in `.env`; any other window can be set in the filter bar.

## Integration checklist

1. **Earth Engine project.** A Google Cloud project registered for Earth Engine, with the Earth Engine API enabled. Put its ID in `GEE_PROJECT`.
2. **Service account** (production). Create it, make a JSON key, and register it for Earth Engine. Give it the Earth Engine Resource Viewer role on the project.
3. **Share the boundary asset.** In the Code Editor Assets tab, share `District_Boundary` with the service account email (Reader). Without this, the backend cannot read your districts.
4. **Boundary columns.** The asset needs a district-name column. A province column is optional. Names are auto-detected; otherwise set `PROVINCE_FIELD` and `DISTRICT_FIELD` in `.env`.
5. **Backend hosting.** Render, Railway, Google Cloud Run or similar. Use HTTPS and a gunicorn timeout of 300 s.
6. **Hosting.** Simplest: deploy the backend and it serves the dashboard too. To host the `frontend/` folder separately (Netlify, GitHub Pages, your own site), set `window.AGROWATCH_API_BASE` in `index.html` to the backend URL.
7. **CORS.** Add the exact frontend origin to `ALLOWED_ORIGINS`.
8. **Website link.** Embed with an iframe, or add the dashboard URL to your site menu.
9. **Login (optional).** Set `AUTH_ENABLED=true`, a long `JWT_SECRET`, `DASH_USER` and `DASH_PASS_HASH`.
10. **Earth Engine quota and billing.** Check your project's commercial or non-commercial use tier. Public dashboards can use a lot of compute.
11. **Basemap terms.** The dark basemap is CARTO and the satellite basemap is Esri. Both need attribution (already shown) and have usage limits. Swap in your own tiles for heavy traffic.

## Run locally (Windows, macOS, Linux)

Requires **Python 3.15** (latest stable is 3.15.2, from python.org). The backend also serves the dashboard, so there is one terminal and one address.

If you have an older `.venv` or `venv` folder (for example Python 3.14), delete it and create a new one, because a virtual environment is tied to the Python version that made it.

```bash
cd backend
python3.15 -m venv venv          # Windows: py -3.15 -m venv venv
venv\Scripts\activate            # macOS/Linux: source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
earthengine authenticate          # one time: sign in with the Google account that owns the asset
python app.py
```

Open **http://localhost:5000**.

`backend/.env` is already filled in with your project (`semiotic-timer-429307-g6`) and asset path. Change it only if you use a different project.

The first load of a province can take 30 to 90 seconds because Earth Engine computes statistics over every season since 2001. Results are cached for 6 hours, so repeats are fast. Pick a single district for quicker results.

## API

| Endpoint | Params | Returns |
|---|---|---|
| `GET /api/meta` | - | provinces, years, seasons, layer list, ADSI and VCI classes |
| `GET /api/districts` | `province` | district names |
| `GET /api/boundary` | `province, district` | simplified GeoJSON outline |
| `GET /api/tiles` | `layer, province, district, year, season, crop` | map tile URL, legend info (20 layers) |
| `GET /api/stats` | `province, district, year, season` | cropland area, ADSI and VCI class areas, mean indices |
| `GET /api/series` | same | NDVI, EVI, VCI, rainfall per season since 2001 |
| `GET /api/rainfall` | `province, district, year` | monthly rainfall vs normal |
| `GET /api/ranking` | `province, year, season` | top 20 districts by mean ADSI over cropland |
| `GET /api/insights` | `province, district, year, season` | risk level, alerts |
| `GET /api/sources` | - | latest image date per source |
| `GET /api/intel/phenology` | filters | SOS, POS, EOS, LOS medians and a season curve |
| `GET /api/intel/rain` | filters | SPI-1/3/6, monthly anomaly, cumulative deficit |
| `GET /api/intel/soil` | filters | SMAP surface and root-zone stats, monthly series |
| `GET /api/intel/et` | filters | ET, PET, ET/PET, anomaly, joint stress area |
| `GET /api/intel/asi` | filters, `w`, `pca` | ASI mean, components, class areas, area under stress |
| `GET /api/intel/asi_series` | filters, `w`, `pca` | ASI for the last 8 seasons |
| `GET /api/intel/hotspots` | filters, `w`, `pca` | Gi* hotspot class areas |
| `GET /api/intel/districts` | filters, `w`, `pca` | district table |
| `GET /api/intel/condition` | filters, `alpha` | VCI, TCI, VHI per season since 2001 |
| `GET /api/intel/trend` | filters | Mann-Kendall and Sen's slope of the regional series |
| `GET /api/intel/crop_agreement` | `province, district` | WorldCover vs Dynamic World cropland agreement |
| `GET /api/intel/classify` | filters, `samples, label_field, val_samples` | Random Forest map and accuracy |
| `GET /api/methodology` | - | indicator documentation |
| `POST /api/export` | JSON body | starts an Earth Engine export task (needs `EXPORTS_ENABLED=true`) |
| `POST /api/login` | `username, password` | token (when auth is enabled) |

Shared filter parameters: `province`, `district` (a name or `bbox:west,south,east,north`), `year`, `season` (`kharif`, `rabi` or `cMMDD_MMDD`), `crop`. `/api/tiles` also accepts `alpha` (VHI) and `w` / `pca` (ASI weights, order veg, rain, soil, temp, ET).

## Customise

- **Add a map layer:** add one `_reg(...)` call in `gee_intel.py` (builder, palette, dataset facts, data type). It appears in the layer panel, in exports and in `/api/meta` automatically.
- **Change ADSI weights or thresholds:** `W` and `CLASSES` in `gee_processor.py`, the same values in `AgroWatch.js`, and `adsiColor` in `charts.js`.
- **Change ASI weights, classes or the stress scale:** `ASI_WEIGHTS`, `ASI_BREAKS`, `Z_MAX` in `gee_intel.py` (weights can also be set live in the Agricultural Stress view, or with PCA).
- **Change the baseline:** `BASE_START` and `BASE_END` in `gee_processor.py` (also in `gee/AgroWatch.js`). Keep `gee_processor.py` and `AgroWatch.js` in step: the Python file is a port of the script.
- **Change colours:** variables at the top of `style.css`.
- **Ideas:** flood extent (Sentinel-1), crop type maps, CSV export, alert emails, an AI assistant panel that explains results (needs an LLM API key).

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Cannot reach the backend" | Backend not running, wrong `AGROWATCH_API_BASE`, or CORS origin missing |
| "No district-name column found" | Set `DISTRICT_FIELD` in `.env` to the exact column name |
| Asset not found or access denied | Share the asset with the service account (step 3) |
| Request times out | Pick one district, shorten the wait, raise gunicorn `--timeout`. ASI, SMAP and SPI layers are the heaviest |
| SMAP layer fails | Run `python verify_datasets.py`; if the collection version moved, set `SMAP_ID` in `.env` |
| "needs at least 5 earlier seasons" | SMAP anomalies and ASI soil moisture need 2020 or later; Landsat TCI needs 2019 or later |
| Export says switched off | Set `EXPORTS_ENABLED=true`. Drive exports go to the Drive of the account that owns the credentials; with a service account set `EXPORT_GCS_BUCKET` instead |
| Rainfall looks too low for the newest season | The season is not finished; CHIRPS and MODIS lag a few weeks |
| Blank page | Open http://localhost:5000, not the HTML file directly |
| `pip install` fails or imports break after upgrading Python | Delete the old virtual environment and create a new one with Python 3.15 |
| SPI layer errors in Earth Engine | Check the `gammainc` argument order in the Earth Engine docs (noted in `_spi_season`) |
