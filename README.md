# AgroWatch v2 - Agriculture and drought monitoring

Which agricultural regions are experiencing vegetation and drought stress?

A Flask API (Python 3.15) runs Google Earth Engine (GEE) analysis. A static dashboard (Leaflet map, Chart.js charts) shows the results.

```
Browser (frontend)  --fetch-->  Flask API (backend)  --ee.*-->  Google Earth Engine
 map, KPIs, charts              gee_processor.py                 Sentinel-2, MODIS, CHIRPS, ERA5,
                                                                 WorldCover, your District_Boundary
```

## Project structure

```
agrowatch-dashboard/
├── gee/AgroWatch.js          Code Editor version of the same analysis (v2)
├── frontend/
│   ├── index.html            layout: top nav, filter bar, layer panel + map, KPI cards, side panels, charts
│   ├── css/style.css         dark control-room theme, responsive
│   ├── js/main.js            API calls, KPI cards, insights, wiring
│   ├── js/map.js             Leaflet: basemaps, GEE tiles, district outlines, legend
│   ├── js/charts.js          sparklines, trend, VCI bars, rainfall, donut, district ranking, risk gauge
│   ├── js/filters.js         region/district/year/season filters, left layer panel
│   └── assets/favicon.svg
├── backend/
│   ├── app.py                routes, validation, errors
│   ├── gee_processor.py      all Earth Engine logic + rule-based insights
│   ├── authentication.py     GEE login (service account) + optional dashboard login
│   ├── requirements.txt
│   └── .env.example
├── .python-version           Python 3.15.2 (pyenv, uv, Render)
├── runtime.txt               Python 3.15.2 (Heroku-style hosts)
└── README.md
```

## What the dashboard shows

| Area | Content |
|---|---|
| Left layer panel | All 20 map layers in four groups. Drought severity: ADSI classes, ADSI continuous, VCI stress classes. Vegetation: Sentinel-2 NDVI, EVI and NDRE, MODIS NDVI, NDVI anomaly, VCI, LAI, GPP, NDVI trend. Rainfall: total, % of normal, SPI. Heat and soil water: LST, TCI, VHI, SMCI, ESI anomaly |
| KPI cards | Cropland area, mean ADSI, mean NDVI (with sparkline), crop health index (VCI), season rainfall, % cropland in drought |
| Risk gauge and insights | Low / Moderate / High / Severe drought risk, plus plain-language alerts (rainfall, SPI, heat, soil water, greenness) |
| Drought indicators | Mean SPI, SMCI, TCI, VHI, day LST, ESI, LAI and GPP over cropland |
| Charts | NDVI and EVI by season since 2001, VCI by season, monthly rainfall vs normal, class share, districts ranked by mean ADSI |
| Filters | Region, district (or click a district on the map), season year, Kharif or Rabi, cropland-only mask |

How the numbers work:
- **ADSI** (agricultural drought severity index, 0 to 1, high = worse) = equal-weight mean of four components, each flipped so 1 is most severe: VCI, SPI, TCI and SMCI. Weights are `W` in `gee_processor.py`. ADSI is a proposed research index, not an established standard.
- **Drought classes** (provisional) come from ADSI: below 0.30 normal, 0.30 to 0.45 mild, 0.45 to 0.60 moderate, 0.60 to 0.75 severe, 0.75 and above extreme.
- **VCI** = (NDVI - min) / (max - min) x 100, using MODIS NDVI for the same season in 2001 to 2018. The vegetation-only classes are: 50+ normal, 35 to 50 low stress, 20 to 35 moderate, 10 to 20 severe, below 10 extreme.
- **SPI** is the seasonal rainfall total (CHIRPS) fitted to a gamma distribution per pixel over 2001 to 2018, with zero-rain seasons handled as a mixed distribution.
- **TCI** and **SMCI** use the 2001 to 2018 min and max of daytime LST (MODIS) and surface soil moisture (ERA5-Land). **VHI** = 0.5 VCI + 0.5 TCI. **ESI** = ET / PET (MODIS MOD16).
- **NDVI trend** is the Sen's slope per year, shown only where the Mann-Kendall test gives p < 0.05.
- **Rainfall % of normal** compares the season total with the 2001 to 2018 average.
- **Cropland** is ESA WorldCover class 40. Statistics are sampled at 250 m to 1 km, so treat areas as estimates.
- **Risk level** (rules in `build_insights`): Severe if 40% or more cropland is in moderate-or-worse drought or mean ADSI is 0.60 or more; High at 25% or ADSI 0.45; Moderate at 10% or ADSI 0.30; otherwise Low. Edit the numbers to suit you.
- **Seasons** run Kharif May to Oct and Rabi Nov to Apr (Rabi 2025 ends April 2026). A season that has not finished is flagged as partial.

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
| `POST /api/login` | `username, password` | token (when auth is enabled) |

## Customise

- **Add a map layer:** add an entry to `LAYERS` and `_vis` in `gee_processor.py`, and an image in `_analysis`. It appears in the left layer panel automatically (under "Other" unless you add its key to `LAYER_GROUPS` in `filters.js`).
- **Change ADSI weights or thresholds:** `W` and `CLASSES` in `gee_processor.py`, the same values in `AgroWatch.js`, and `adsiColor` in `charts.js`.
- **Change the baseline:** `BASE_START` and `BASE_END` in `gee_processor.py` (also in `gee/AgroWatch.js`). Keep `gee_processor.py` and `AgroWatch.js` in step: the Python file is a port of the script.
- **Change colours:** variables at the top of `style.css`.
- **Ideas:** flood extent (Sentinel-1), crop type maps, CSV export, alert emails, an AI assistant panel that explains results (needs an LLM API key).

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Cannot reach the backend" | Backend not running, wrong `AGROWATCH_API_BASE`, or CORS origin missing |
| "No district-name column found" | Set `DISTRICT_FIELD` in `.env` to the exact column name |
| Asset not found or access denied | Share the asset with the service account (step 3) |
| Request times out | Pick one district, shorten the wait, raise gunicorn `--timeout` |
| Rainfall looks too low for the newest season | The season is not finished; CHIRPS and MODIS lag a few weeks |
| Blank page | Open http://localhost:5000, not the HTML file directly |
| `pip install` fails or imports break after upgrading Python | Delete the old virtual environment and create a new one with Python 3.15 |
| SPI layer errors in Earth Engine | Check the `gammainc` argument order in the Earth Engine docs (noted in `_spi_season`) |
