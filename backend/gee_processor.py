"""Earth Engine logic for AgroWatch v2. Python port of gee/AgroWatch.js (v2).

app.py never touches `ee` directly. Every public get_* function is cached
because Earth Engine calls take seconds.

v2 adds: SPI (gamma-fitted), LST/TCI, VHI, soil-moisture SMCI (ERA5-Land), ESI,
LAI, GPP, NDRE, NDVI trend (Mann-Kendall + Sen's slope), the integrated ADSI,
and a district ranking.
"""
import math
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

import ee

# ----------------------------------------------------------------- settings
BOUNDARY_ASSET = os.getenv("BOUNDARY_ASSET", "projects/semiotic-timer-429307-g6/assets/District_Boundary")
PROVINCE_FIELD = os.getenv("PROVINCE_FIELD") or None
DISTRICT_FIELD = os.getenv("DISTRICT_FIELD") or None
PROVINCE_CANDIDATES = ["PROVINCE", "Prov_Name", "PROV", "ADM1_NAME", "NAME_1", "ADM1_EN"]
DISTRICT_CANDIDATES = ["DISTRICT", "Dist_Name", "DIST", "ADM2_NAME", "NAME_2", "ADM2_EN", "NAME"]

BASE_START, BASE_END = 2001, 2018   # long-term "normal" period
FIRST_YEAR = 2016                   # first selectable season year (MODIS/CHIRPS baseline starts 2001)
CLOUD_MAX = 30

# ADSI weights (equal by default; change for sensitivity tests)
W = {"vci": 0.25, "spi": 0.25, "tci": 0.25, "smci": 0.25}

S2_ID = "COPERNICUS/S2_SR_HARMONIZED"
MODIS_ID = "MODIS/061/MOD13Q1"
MOD11_ID = "MODIS/061/MOD11A2"
MOD16_ID = "MODIS/061/MOD16A2GF"
MOD17_ID = "MODIS/061/MOD17A2H"
MCD15_ID = "MODIS/061/MCD15A3H"
CHIRPS_ID = "UCSB-CHG/CHIRPS/DAILY"
ERA5_ID = "ECMWF/ERA5_LAND/MONTHLY_AGGR"
WORLDCOVER_ID = "ESA/WorldCover/v200"

_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _month_day(text, default):
    """'05-01' -> (5, 1). Falls back to `default` when the text is missing or not a real date."""
    for t in (text, default):
        try:
            m, d = (int(x) for x in str(t).strip().split("-"))
            datetime(2001, m, d)          # 2001 is not a leap year, so Feb 29 is rejected
            return m, d
        except (ValueError, TypeError):
            continue
    raise ValueError("bad default date")


def _season_def(label, start, end):
    (m1, d1), (m2, d2) = start, end
    return {"label": f"{label} ({_MON[m1 - 1]}-{_MON[m2 - 1]})", "m1": m1, "d1": d1, "m2": m2, "d2": d2,
            "crosses": (m2, d2) < (m1, d1)}


# Crop calendars are configurable in .env (KHARIF_START=05-01, KHARIF_END=10-31, RABI_START, RABI_END).
SEASONS = {
    "kharif": _season_def("Kharif", _month_day(os.getenv("KHARIF_START"), "05-01"), _month_day(os.getenv("KHARIF_END"), "10-31")),
    "rabi": _season_def("Rabi", _month_day(os.getenv("RABI_START"), "11-01"), _month_day(os.getenv("RABI_END"), "04-30")),
}

# A custom window is written c<MMDD>_<MMDD>, e.g. c0615_0915. The same month-day window is used for
# every baseline year, so comparisons are always like-for-like.
_CUSTOM = re.compile(r"^c(\d{2})(\d{2})_(\d{2})(\d{2})$")


def season_cfg(season):
    if season in SEASONS:
        return SEASONS[season]
    m = _CUSTOM.match(season or "")
    if not m:
        raise ValueError(f"Unknown season '{season}'. Options: {', '.join(SEASONS)} or a custom window")
    m1, d1, m2, d2 = (int(x) for x in m.groups())
    try:
        datetime(2001, m1, d1)
        datetime(2001, m2, d2)
    except ValueError:
        raise ValueError("Custom season has an invalid month-day")
    return {"label": f"Custom ({d1:02d} {_MON[m1 - 1]} to {d2:02d} {_MON[m2 - 1]})", "m1": m1, "d1": d1,
            "m2": m2, "d2": d2, "crosses": (m2, d2) < (m1, d1)}


def custom_code(m1, d1, m2, d2):
    return f"c{m1:02d}{d1:02d}_{m2:02d}{d2:02d}"


def season_dates(year, season):
    """(first day, last day) of the season as Python dates, both inclusive."""
    cfg = season_cfg(season)
    year = int(year)
    return date(year, cfg["m1"], cfg["d1"]), date(year + (1 if cfg["crosses"] else 0), cfg["m2"], cfg["d2"])


DATA_LAG_DAYS = 16     # newest MODIS / CHIRPS data is about two weeks behind


def resolve_season(year, season, today=None):
    """Return (season_code, info). A season that is still running is cut at today minus the data
    lag, and the SAME shortened window is used for every baseline year, so partial seasons are never
    compared with full ones."""
    today = today or date.today()
    start, end = season_dates(year, season)
    if start > today:
        raise ValueError(f"{season_cfg(season)['label']} {year} has not started yet (it starts {start:%d %b %Y}).")
    cutoff = today - timedelta(days=DATA_LAG_DAYS)
    info = {"start": start.isoformat(), "end": end.isoformat(), "partial": False, "label": season_cfg(season)["label"]}
    if cutoff >= end:
        return season, info
    if cutoff < start + timedelta(days=30):
        raise ValueError("Fewer than 30 days of observations exist for this season so far. "
                         "Pick an earlier year or wait for more data.")
    cfg = season_cfg(season)
    info.update(partial=True, end=cutoff.isoformat(), full_end=end.isoformat())
    return custom_code(cfg["m1"], cfg["d1"], cutoff.month, cutoff.day), info


def is_monsoon(season):
    cfg = season_cfg(season)
    return 4 <= cfg["m1"] <= 7


_COLORS = ["#1a9850", "#fee08b", "#fdae61", "#d73027", "#7f0000"]

# Main classification: ADSI (0-1, high = worse). Thresholds are provisional.
CLASSES = [
    {"id": 1, "name": "Normal", "range": "ADSI below 0.30", "color": _COLORS[0]},
    {"id": 2, "name": "Mild drought", "range": "ADSI 0.30 to 0.45", "color": _COLORS[1]},
    {"id": 3, "name": "Moderate drought", "range": "ADSI 0.45 to 0.60", "color": _COLORS[2]},
    {"id": 4, "name": "Severe drought", "range": "ADSI 0.60 to 0.75", "color": _COLORS[3]},
    {"id": 5, "name": "Extreme drought", "range": "ADSI 0.75 and above", "color": _COLORS[4]},
]

# Vegetation-only classification (the v1 stress classes)
VCI_CLASSES = [
    {"id": 1, "name": "Normal", "range": "VCI 50 and above", "color": _COLORS[0]},
    {"id": 2, "name": "Low Stress", "range": "VCI 35 to 50", "color": _COLORS[1]},
    {"id": 3, "name": "Moderate stress", "range": "VCI 20 to 35", "color": _COLORS[2]},
    {"id": 4, "name": "Severe stress", "range": "VCI 10 to 20", "color": _COLORS[3]},
    {"id": 5, "name": "Extreme stress", "range": "VCI below 10", "color": _COLORS[4]},
]

NDVI_PAL = ["#d73027", "#fc8d59", "#fee08b", "#d9ef8b", "#91cf60", "#1a9850", "#006837"]
ANOM_PAL = ["#8c510a", "#d8b365", "#f6e8c3", "#f5f5f5", "#c7eae5", "#5ab4ac", "#01665e"]
RAIN_PAL = ["#ffffcc", "#c7e9b4", "#7fcdbb", "#41b6c4", "#2c7fb8", "#253494"]
PCT_PAL = ["#313695", "#1a9850", "#a6d96a", "#ffffbf", "#fee08b", "#f46d43", "#a50026"]
SPI_PAL = ["#8c0000", "#d73027", "#fdae61", "#ffffbf", "#a6d96a", "#1a9850", "#006837"]
TEMP_PAL = ["#313695", "#74add1", "#ffffbf", "#fdae61", "#d73027", "#7f0000"]
COND_PAL = ["#7f0000", "#d73027", "#fdae61", "#ffffbf", "#a6d96a", "#1a9850"]   # low = bad
STRESS_PAL = ["#1a9850", "#ffffbf", "#fdae61", "#d73027", "#7f0000"]            # high = bad
TREND_PAL = ["#b2182b", "#ef8a62", "#f7f7f7", "#67a9cf", "#2166ac"]

# key -> label shown on the map tabs, title, unit, kind
LAYERS = {
    "stress": {"label": "Drought (ADSI)", "title": "Agricultural drought severity (cropland)", "unit": "", "kind": "classes"},
    "adsi": {"label": "ADSI", "title": "ADSI, continuous (0 = none, 1 = extreme)", "unit": "", "kind": "continuous"},
    "vci_stress": {"label": "Veg. stress", "title": "Vegetation stress, VCI classes (cropland)", "unit": "", "kind": "classes"},
    "s2_ndvi": {"label": "Sentinel-2 NDVI", "title": "NDVI, Sentinel-2 10 m", "unit": "", "kind": "continuous"},
    "s2_evi": {"label": "Sentinel-2 EVI", "title": "EVI, Sentinel-2 10 m", "unit": "", "kind": "continuous"},
    "s2_ndre": {"label": "Sentinel-2 NDRE", "title": "NDRE (red edge), Sentinel-2 20 m", "unit": "", "kind": "continuous"},
    "modis_ndvi": {"label": "MODIS NDVI", "title": "NDVI, MODIS 250 m", "unit": "", "kind": "continuous"},
    "anomaly": {"label": "NDVI anomaly", "title": "NDVI anomaly vs 2001-2018", "unit": "", "kind": "continuous"},
    "lai": {"label": "LAI", "title": "Leaf Area Index, MODIS 500 m", "unit": "", "kind": "continuous"},
    "gpp": {"label": "GPP", "title": "Gross primary productivity", "unit": "g C/m2", "kind": "continuous"},
    "vci": {"label": "VCI", "title": "Vegetation Condition Index", "unit": "", "kind": "continuous"},
    "rain": {"label": "Rainfall", "title": "Season rainfall", "unit": "mm", "kind": "continuous"},
    "rain_pct": {"label": "Rain % normal", "title": "Rainfall as % of normal", "unit": "%", "kind": "continuous"},
    "spi": {"label": "SPI", "title": "Standardized Precipitation Index (seasonal)", "unit": "", "kind": "continuous"},
    "lst": {"label": "LST", "title": "Day land surface temperature", "unit": "C", "kind": "continuous"},
    "tci": {"label": "TCI", "title": "Temperature Condition Index", "unit": "", "kind": "continuous"},
    "vhi": {"label": "VHI", "title": "Vegetation Health Index", "unit": "", "kind": "continuous"},
    "smci": {"label": "SMCI", "title": "Soil Moisture Condition Index", "unit": "", "kind": "continuous"},
    "esi_anom": {"label": "ESI anomaly", "title": "Evaporative stress anomaly (ET/PET)", "unit": "", "kind": "continuous"},
    "trend": {"label": "NDVI trend", "title": "NDVI trend, Sen's slope per year (p < 0.05)", "unit": "", "kind": "continuous"},
}


def layer_meta():
    return {"layers": LAYERS, "classes": CLASSES, "vci_classes": VCI_CLASSES}


def _vis(layer, season):
    rain_max = 600 if is_monsoon(season) else 200
    return {
        "stress": {"min": 1, "max": 5, "palette": _COLORS},
        "vci_stress": {"min": 1, "max": 5, "palette": _COLORS},
        "adsi": {"min": 0, "max": 1, "palette": STRESS_PAL},
        "s2_ndvi": {"min": 0, "max": 0.9, "palette": NDVI_PAL},
        "s2_evi": {"min": 0, "max": 0.9, "palette": NDVI_PAL},
        "s2_ndre": {"min": 0, "max": 0.6, "palette": NDVI_PAL},
        "modis_ndvi": {"min": 0, "max": 0.8, "palette": NDVI_PAL},
        "anomaly": {"min": -0.2, "max": 0.2, "palette": ANOM_PAL},
        "lai": {"min": 0, "max": 4, "palette": NDVI_PAL},
        "gpp": {"min": 0, "max": 800, "palette": NDVI_PAL},
        "vci": {"min": 0, "max": 100, "palette": COND_PAL},
        "rain": {"min": 0, "max": rain_max, "palette": RAIN_PAL},
        "rain_pct": {"min": 0, "max": 200, "palette": PCT_PAL},
        "spi": {"min": -2.5, "max": 2.5, "palette": SPI_PAL},
        "lst": {"min": 15, "max": 50, "palette": TEMP_PAL},
        "tci": {"min": 0, "max": 100, "palette": COND_PAL},
        "vhi": {"min": 0, "max": 100, "palette": COND_PAL},
        "smci": {"min": 0, "max": 100, "palette": COND_PAL},
        "esi_anom": {"min": -0.2, "max": 0.2, "palette": ANOM_PAL},
        "trend": {"min": -0.01, "max": 0.01, "palette": TREND_PAL},
    }[layer]


# -------------------------------------------------------------------- cache
def ttl_cache(seconds):
    """Tiny in-memory cache. Positional, hashable args only."""

    def deco(fn):
        store, lock = {}, threading.Lock()

        def wrapper(*args):
            now = time.time()
            with lock:
                hit = store.get(args)
                if hit and now - hit[0] < seconds:
                    return hit[1]
            value = fn(*args)
            with lock:
                store[args] = (now, value)
                if len(store) > 200:
                    store.pop(min(store, key=lambda k: store[k][0]))
            return value

        return wrapper

    return deco


# --------------------------------------------------------- boundary / fields
_fields = {}


def _find(props, candidates):
    lower = {p.lower(): p for p in props}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]
    return None


def fields():
    """Column names of the boundary asset (detected once)."""
    if not _fields:
        props = ee.FeatureCollection(BOUNDARY_ASSET).first().propertyNames().getInfo()
        district = DISTRICT_FIELD or _find(props, DISTRICT_CANDIDATES)
        if not district:
            raise RuntimeError(
                f"No district-name column found in {BOUNDARY_ASSET}. Columns: {', '.join(props)}. "
                "Set DISTRICT_FIELD in .env."
            )
        _fields.update(province=PROVINCE_FIELD or _find(props, PROVINCE_CANDIDATES), district=district)
    return _fields


def _base_fc(province):
    f = fields()
    fc = ee.FeatureCollection(BOUNDARY_ASSET).filter(ee.Filter.notNull([f["district"]]))
    if f["province"] and province:
        fc = fc.filter(ee.Filter.eq(f["province"], province))
    return fc


def parse_bbox(text):
    """'bbox:west,south,east,north' -> [w, s, e, n] after sanity checks, else None."""
    if not text or not str(text).startswith("bbox:"):
        return None
    try:
        w, s_, e, n = (float(x) for x in str(text)[5:].split(","))
    except ValueError:
        raise ValueError("Custom region must be four numbers: west,south,east,north")
    if not (60.0 <= w < e <= 78.5 and 23.0 <= s_ < n <= 37.5):
        raise ValueError("Custom region must lie inside Pakistan (lon 60 to 78.5, lat 23 to 37.5) and west < east, south < north")
    if (e - w) * (n - s_) > 90:
        raise ValueError("Custom region is too large (limit about 90 square degrees). Pick a province instead.")
    return [w, s_, e, n]


def _region_fc(province, district):
    box = parse_bbox(district)
    if box:
        return ee.FeatureCollection([ee.Feature(ee.Geometry.Rectangle(box), {fields()["district"]: "Custom region"})])
    fc = _base_fc(province)
    if district:
        fc = fc.filter(ee.Filter.eq(fields()["district"], district))
    return fc


@ttl_cache(3600)
def list_provinces():
    f = fields()
    if not f["province"]:
        return []
    fc = ee.FeatureCollection(BOUNDARY_ASSET).filter(ee.Filter.notNull([f["province"]]))
    return fc.aggregate_array(f["province"]).distinct().sort().getInfo()


@ttl_cache(3600)
def list_districts(province):
    return _base_fc(province).aggregate_array(fields()["district"]).distinct().sort().getInfo()


@ttl_cache(21600)
def get_boundary(province, district):
    """Simplified GeoJSON outline with a `name` property (district name)."""
    name_field = fields()["district"]
    fc = _region_fc(province, district).map(
        lambda f: ee.Feature(f.geometry().simplify(500), {"name": f.get(name_field)})
    )
    return fc.getInfo()


# ------------------------------------------------------------ imagery helpers
DW_ID = "GOOGLE/DYNAMICWORLD/V1"
# Cropland mask is configurable: worldcover (default) | dynamicworld | intersect | union.
# Neither product is perfect, so check /api/intel/crop_agreement before trusting a district figure.
CROP_SOURCE = os.getenv("CROP_SOURCE", "worldcover").lower()
DW_CROP_START = os.getenv("DW_CROP_START", "2021-01-01")
DW_CROP_END = os.getenv("DW_CROP_END", "2024-01-01")
DW_CROP_THRESHOLD = float(os.getenv("DW_CROP_THRESHOLD", "0.5"))


def _worldcover_crop():
    return ee.Image(ee.ImageCollection(WORLDCOVER_ID).first()).select("Map").eq(40).selfMask()


def _dw_crop():
    """Dynamic World: mean 'crops' probability over a multi-year window, kept where >= threshold."""
    col = ee.ImageCollection(DW_ID).filterDate(DW_CROP_START, DW_CROP_END).select("crops")
    return col.mean().gte(DW_CROP_THRESHOLD).selfMask()


def _crop_mask(source=None):
    source = (source or CROP_SOURCE).lower()
    if source == "dynamicworld":
        return _dw_crop()
    if source == "intersect":
        return _worldcover_crop().And(_dw_crop()).selfMask()
    if source == "union":
        return _worldcover_crop().unmask(0).Or(_dw_crop().unmask(0)).selfMask()
    return _worldcover_crop()


def season_range(year, season):
    """Rabi 2025 = 1 Nov 2025 -> 30 Apr 2026 (end date is exclusive, so +1 day)."""
    cfg = season_cfg(season)
    y = ee.Number(year)
    end_year = y.add(1) if cfg["crosses"] else y
    start = ee.Date.fromYMD(y, cfg["m1"], cfg["d1"])
    end = ee.Date.fromYMD(end_year, cfg["m2"], cfg["d2"]).advance(1, "day")
    return start, end


def season_is_complete(year, season):
    """False while the season is still running (results would be partial)."""
    _, last = season_dates(year, season)
    return datetime.now(timezone.utc).date() > last


def _mask_index_s2(img):
    scl = img.select("SCL")
    ok = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
    refl = img.select(["B2", "B4", "B5", "B8"]).divide(10000)
    ndvi = refl.normalizedDifference(["B8", "B4"]).rename("NDVI")
    ndre = refl.normalizedDifference(["B8", "B5"]).rename("NDRE")
    evi = refl.expression(
        "2.5 * (NIR - RED) / (NIR + 6.0 * RED - 7.5 * BLUE + 1.0)",
        {"NIR": refl.select("B8"), "RED": refl.select("B4"), "BLUE": refl.select("B2")},
    ).rename("EVI")
    return ndvi.addBands(evi).addBands(ndre).updateMask(ok).copyProperties(img, ["system:time_start"])


def _guard(col, bands):
    """Add one fully-masked placeholder image so an empty collection (season not finished,
    data lag) still returns an image with the right band names instead of crashing."""
    ph = ee.Image.constant([0] * len(bands)).rename(bands).selfMask()
    return col.merge(ee.ImageCollection([ph]))


# ---- single-season builders (each returns ONE image) ----
def _modis_season(year, season):                      # NDVI, EVI
    start, end = season_range(year, season)

    def prep(img):
        good = img.select("SummaryQA").lte(1)
        return (
            img.select(["NDVI", "EVI"]).multiply(0.0001).updateMask(good)
            .copyProperties(img, ["system:time_start"])
        )

    col = ee.ImageCollection(MODIS_ID).filterDate(start, end).map(prep)
    return _guard(col, ["NDVI", "EVI"]).mean()


def _rain_season(year, season):                       # rain (mm)
    start, end = season_range(year, season)
    col = ee.ImageCollection(CHIRPS_ID).filterDate(start, end).select("precipitation")
    return _guard(col, ["precipitation"]).sum().rename("rain")


def _lst_season(year, season):                        # LST (deg C, daytime)
    start, end = season_range(year, season)

    def prep(img):
        ok = img.select("QC_Day").bitwiseAnd(3).lte(1)
        return (
            img.select("LST_Day_1km").multiply(0.02).subtract(273.15).updateMask(ok)
            .rename("LST").copyProperties(img, ["system:time_start"])
        )

    col = ee.ImageCollection(MOD11_ID).filterDate(start, end).map(prep)
    return _guard(col, ["LST"]).mean()


def _sm_season(year, season):                         # SM (m3/m3, 0-7 cm)
    start, end = season_range(year, season)
    col = (ee.ImageCollection(ERA5_ID).filterDate(start, end)
           .select("volumetric_soil_water_layer_1").map(lambda i: i.rename("SM")))
    return _guard(col, ["SM"]).mean()


def _esi_season(year, season):                        # ESI = ET / PET
    start, end = season_range(year, season)
    col = ee.ImageCollection(MOD16_ID).filterDate(start, end).select(["ET", "PET"])
    c = _guard(col, ["ET", "PET"])
    et, pet = c.select("ET").mean(), c.select("PET").mean()
    return et.divide(pet).updateMask(pet.gt(0)).rename("ESI")


def _lai_season(year, season):                        # LAI
    start, end = season_range(year, season)
    col = ee.ImageCollection(MCD15_ID).filterDate(start, end).select("Lai")
    return _guard(col, ["Lai"]).mean().multiply(0.1).rename("LAI")


def _gpp_season(year, season):                        # GPP (g C / m2 / season)
    start, end = season_range(year, season)
    col = ee.ImageCollection(MOD17_ID).filterDate(start, end).select("Gpp")
    # Gpp scale 0.0001 kg C/m2 per 8 days -> x1000 for grams
    return _guard(col, ["Gpp"]).sum().multiply(0.1).rename("GPP")


def _baseline(fn, season):
    return ee.ImageCollection(ee.List.sequence(BASE_START, BASE_END).map(lambda y: fn(y, season)))


def _cond_idx(cur, fn, season, band, invert, name):
    """0-100 condition index from the baseline min/max.
    invert=False: (cur-min)/(max-min)  (VCI, SMCI)   invert=True: (max-cur)/(max-min)  (TCI)"""
    base = _baseline(fn, season).select(band)
    mn, mx = base.min(), base.max()
    num = mx.subtract(cur) if invert else cur.subtract(mn)
    return num.divide(mx.subtract(mn)).multiply(100).clamp(0, 100).rename(name)


def _spi_season(year, season):
    """SPI of the seasonal rainfall total (6-month window). Gamma distribution fitted per
    pixel to the baseline totals (Thom's ML approximation) with a mixed distribution for
    zero-rainfall seasons:  H(x) = q + (1 - q) G(x);  SPI = Phi^-1(H).
    Check Image.gammainc argument order in the API docs if EE reports an error here."""
    base = _baseline(_rain_season, season)
    cur = _rain_season(year, season)
    n = BASE_END - BASE_START + 1

    q = base.map(lambda i: i.eq(0).rename("rain")).sum().divide(n)
    pos = base.map(lambda i: i.updateMask(i.gt(0)))
    mean = pos.mean()
    mean_ln = pos.map(lambda i: i.log()).mean()

    a = mean.log().subtract(mean_ln)
    alpha = a.multiply(4).divide(3).add(1).sqrt().add(1).divide(a.multiply(4))
    beta = mean.divide(alpha)

    g = alpha.gammainc(cur.divide(beta))
    h = q.add(ee.Image(1).subtract(q).multiply(g)).clamp(0.0001, 0.9999)
    return (h.multiply(2).subtract(1).erfInv().multiply(math.sqrt(2))
            .updateMask(mean.gt(0).And(a.gt(0))).rename("SPI"))


def _analysis(province, district, year, season):
    """Build every analysis image (lazy - nothing is computed until requested)."""
    region_fc = _region_fc(province, district)
    geom = region_fc.geometry()
    start, end = season_range(year, season)

    s2 = (
        ee.ImageCollection(S2_ID).filterBounds(geom).filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", CLOUD_MAX))
        .map(_mask_index_s2).median()
    )

    # vegetation
    cur = _modis_season(year, season)
    ndvi, evi = cur.select("NDVI"), cur.select("EVI")
    base = _baseline(_modis_season, season).select("NDVI")
    b_mean, b_min, b_max = base.mean(), base.min(), base.max()
    anomaly = ndvi.subtract(b_mean).rename("anomaly")
    vci = ndvi.subtract(b_min).divide(b_max.subtract(b_min)).multiply(100).clamp(0, 100).rename("VCI")
    lai, gpp = _lai_season(year, season), _gpp_season(year, season)

    # rainfall
    rain = _rain_season(year, season)
    rain_base = _baseline(_rain_season, season).mean()
    rain_pct = rain.divide(rain_base).multiply(100).updateMask(rain_base.gt(10)).rename("rain_pct")
    spi = _spi_season(year, season)

    # thermal
    lst = _lst_season(year, season)
    tci = _cond_idx(lst, _lst_season, season, "LST", True, "TCI")
    vhi = vci.multiply(0.5).add(tci.multiply(0.5)).rename("VHI")

    # soil moisture and evaporative stress
    sm = _sm_season(year, season)
    smci = _cond_idx(sm, _sm_season, season, "SM", False, "SMCI")
    esi = _esi_season(year, season)
    esi_mean = _baseline(_esi_season, season).select("ESI").mean()
    esi_anom = esi.subtract(esi_mean).rename("ESI_anom")

    # ADSI: every component oriented so 1 = most severe
    vci_s = ee.Image(1).subtract(vci.divide(100))
    spi_s = ee.Image(2).subtract(spi).divide(4).clamp(0, 1)      # SPI +2 -> 0, SPI -2 -> 1
    tci_s = ee.Image(1).subtract(tci.divide(100))
    smci_s = ee.Image(1).subtract(smci.divide(100))
    w_sum = sum(W.values())
    adsi = (vci_s.multiply(W["vci"]).add(spi_s.multiply(W["spi"]))
            .add(tci_s.multiply(W["tci"])).add(smci_s.multiply(W["smci"]))
            .divide(w_sum).rename("ADSI"))

    stress = (
        ee.Image(0)
        .where(adsi.lt(0.30), 1)
        .where(adsi.gte(0.30).And(adsi.lt(0.45)), 2)
        .where(adsi.gte(0.45).And(adsi.lt(0.60)), 3)
        .where(adsi.gte(0.60).And(adsi.lt(0.75)), 4)
        .where(adsi.gte(0.75), 5)
        .updateMask(adsi.gte(0)).rename("stress")
    )
    vci_stress = (
        ee.Image(0)
        .where(vci.gte(50), 1)
        .where(vci.gte(35).And(vci.lt(50)), 2)
        .where(vci.gte(20).And(vci.lt(35)), 3)
        .where(vci.gte(10).And(vci.lt(20)), 4)
        .where(vci.lt(10), 5)
        .updateMask(vci.gte(0)).rename("stress")
    )

    return {
        "geom": geom, "ndvi": ndvi, "evi": evi, "vci": vci, "stress": stress, "vci_stress": vci_stress,
        "rain": rain, "rain_pct": rain_pct, "spi": spi, "lst": lst, "tci": tci, "vhi": vhi,
        "smci": smci, "esi": esi, "lai": lai, "gpp": gpp, "adsi": adsi,
        "b_min": b_min, "b_max": b_max,
        "images": {
            "stress": stress, "vci_stress": vci_stress, "adsi": adsi,
            "s2_ndvi": s2.select("NDVI"), "s2_evi": s2.select("EVI"), "s2_ndre": s2.select("NDRE"),
            "modis_ndvi": ndvi, "anomaly": anomaly, "lai": lai, "gpp": gpp, "vci": vci,
            "rain": rain, "rain_pct": rain_pct, "spi": spi, "lst": lst, "tci": tci, "vhi": vhi,
            "smci": smci, "esi_anom": esi_anom,
        },
    }


def _trend_image(year, season):
    """Sen's slope of seasonal NDVI (per year), shown only where Mann-Kendall p < 0.05."""
    imgs = [
        ee.Image.constant(y).float().rename("year").addBands(_modis_season(y, season).select("NDVI"))
        for y in range(BASE_START, int(year) + 1)
    ]
    col = ee.ImageCollection(imgs)
    sen = col.reduce(ee.Reducer.sensSlope())
    mk = col.reduce(ee.Reducer.kendallsCorrelation())
    return sen.select("slope").updateMask(mk.select("NDVI_p-value").lt(0.05)).rename("NDVI_trend")


def _rr(img, reducer, geom, scale):
    return img.reduceRegion(reducer=reducer, geometry=geom, scale=scale,
                            maxPixels=1e13, bestEffort=True, tileScale=8)


# ---------------------------------------------------------------- public API
@ttl_cache(1800)
def get_tiles(layer, province, district, year, season, crop):
    a = _analysis(province, district, year, season)
    img = _trend_image(year, season) if layer == "trend" else a["images"][layer]
    if crop:
        img = img.updateMask(_crop_mask())
    vis = _vis(layer, season)
    ee_vis = {**vis, "palette": [c.lstrip("#") for c in vis["palette"]]}
    url = img.clip(a["geom"]).getMapId(ee_vis)["tile_fetcher"].url_format
    classes = VCI_CLASSES if layer == "vci_stress" else CLASSES
    return {"url": url, "vis": vis, **LAYERS[layer], "classes": classes}


def _class_table(groups, defs):
    by_class = {int(g["class"]): g["sum"] for g in (groups or [])}
    monitored = sum(by_class.values())
    rows = []
    for c in defs:
        ha = by_class.get(c["id"], 0.0)
        rows.append({**c, "ha": ha, "pct": 100 * ha / monitored if monitored else 0})
    stressed = sum(by_class.get(i, 0.0) for i in (3, 4, 5))
    return rows, monitored, stressed


@ttl_cache(21600)
def get_stats(province, district, year, season):
    a = _analysis(province, district, year, season)
    geom, crop = a["geom"], _crop_mask()
    area_ha = ee.Image.pixelArea().divide(10000)
    group = ee.Reducer.sum().group(groupField=1, groupName="class")
    raw = ee.Dictionary({
        "groups": _rr(area_ha.addBands(a["stress"]).updateMask(crop), group, geom, 250).get("groups"),
        "vci_groups": _rr(area_ha.addBands(a["vci_stress"]).updateMask(crop), group, geom, 250).get("groups"),
        "totalHa": _rr(area_ha.updateMask(crop), ee.Reducer.sum(), geom, 250).get("area"),
        "means": _rr(
            a["ndvi"].addBands([a["evi"], a["vci"], a["rain"], a["rain_pct"], a["spi"], a["lst"],
                                a["tci"], a["vhi"], a["smci"], a["esi"], a["lai"], a["gpp"], a["adsi"]])
            .updateMask(crop), ee.Reducer.mean(), geom, 1000),
    }).getInfo()

    classes, monitored, stressed = _class_table(raw.get("groups"), CLASSES)
    vci_classes, v_mon, v_stressed = _class_table(raw.get("vci_groups"), VCI_CLASSES)
    return {
        "total_ha": raw.get("totalHa"), "monitored_ha": monitored, "classes": classes,
        "stressed_ha": stressed, "stressed_pct": 100 * stressed / monitored if monitored else 0,
        "vci_classes": vci_classes,
        "vci_stressed_ha": v_stressed, "vci_stressed_pct": 100 * v_stressed / v_mon if v_mon else 0,
        "means": raw.get("means") or {},
        "complete": season_is_complete(year, season),
    }


@ttl_cache(21600)
def get_ranking(province, year, season, limit=20):
    """Districts of the province ranked by mean ADSI over cropland (worst first)."""
    a = _analysis(province, None, year, season)
    fc = (a["adsi"].updateMask(_crop_mask())
          .reduceRegions(collection=_base_fc(province), reducer=ee.Reducer.mean(),
                         scale=1000, tileScale=8)
          .filter(ee.Filter.notNull(["mean"])).sort("mean", False).limit(limit))
    name_field = fields()["district"]
    feats = fc.select([name_field, "mean"]).getInfo()["features"]
    return {"rows": [{"name": f["properties"].get(name_field), "adsi": f["properties"]["mean"]}
                     for f in feats]}


@ttl_cache(21600)
def get_series(province, district, year, season):
    """Seasonal mean NDVI / EVI / VCI / rainfall for every year from 2001 to `year`.

    Every year is stacked as bands of one image so Earth Engine computes everything in a
    single pass (the baseline min/max is built once, not once per year)."""
    a = _analysis(province, district, year, season)
    geom, crop = a["geom"], _crop_mask()
    years = list(range(BASE_START, int(year) + 1))

    ndvi_b, evi_b, rain_b = [], [], []
    for y in years:
        cur = _modis_season(y, season)
        ndvi_b.append(cur.select("NDVI").rename(f"NDVI_{y}"))
        evi_b.append(cur.select("EVI").rename(f"EVI_{y}"))
        rain_b.append(_rain_season(y, season).rename(f"rain_{y}"))

    ndvi = ee.Image.cat(ndvi_b)
    vci = (ndvi.subtract(a["b_min"]).divide(a["b_max"].subtract(a["b_min"]))
           .multiply(100).clamp(0, 100).rename([f"VCI_{y}" for y in years]))

    m = _rr(ee.Image.cat([ndvi, ee.Image.cat(evi_b), vci]).updateMask(crop),
            ee.Reducer.mean(), geom, 1000).getInfo()
    r = _rr(ee.Image.cat(rain_b), ee.Reducer.mean(), geom, 5566).getInfo()

    rows = [{"year": y, "NDVI": m.get(f"NDVI_{y}"), "EVI": m.get(f"EVI_{y}"),
             "VCI": m.get(f"VCI_{y}"), "rain": r.get(f"rain_{y}")} for y in years]
    return {"rows": rows, "base": [BASE_START, BASE_END]}


@ttl_cache(21600)
def get_rain_monthly(province, district, year):
    """Monthly rainfall for `year` against the 2001-2018 monthly normal (mm)."""
    geom = _region_fc(province, district).geometry()
    chirps = ee.ImageCollection(CHIRPS_ID).select("precipitation")

    def month_sum(y, mo):
        s0 = ee.Date.fromYMD(y, mo, 1)
        return _guard(chirps.filterDate(s0, s0.advance(1, "month")), ["precipitation"]).sum()

    bands = []
    for mo in range(1, 13):
        obs = month_sum(int(year), mo).rename(f"obs_{mo}")
        normal = ee.ImageCollection(
            [month_sum(by, mo) for by in range(BASE_START, BASE_END + 1)]).mean().rename(f"normal_{mo}")
        bands += [obs, normal]

    d = _rr(ee.Image.cat(bands), ee.Reducer.mean(), geom, 5566).getInfo()
    rows = [{"month": mo, "observed": d.get(f"obs_{mo}"), "normal": d.get(f"normal_{mo}")}
            for mo in range(1, 13)]
    return {"rows": rows}


@ttl_cache(3600)
def get_sources():
    """Latest observation date per satellite source (freshness indicator)."""
    now = ee.Date(int(time.time() * 1000))

    def latest(coll_id, days):
        try:
            col = ee.ImageCollection(coll_id).filterDate(now.advance(-days, "day"), now)
            ms = col.aggregate_max("system:time_start").getInfo()
            return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if ms else None
        except Exception:
            return None

    return [
        {"id": "s2", "name": "Sentinel-2", "note": "10-20 m optical imagery, NDVI, EVI and NDRE maps", "latest": latest(S2_ID, 30)},
        {"id": "modis", "name": "MODIS NDVI", "note": "250 m, 16-day NDVI and EVI, 2001 baseline", "latest": latest(MODIS_ID, 90)},
        {"id": "lst", "name": "MODIS LST", "note": "1 km land surface temperature, TCI", "latest": latest(MOD11_ID, 60)},
        {"id": "et", "name": "MODIS ET / GPP / LAI", "note": "500 m evapotranspiration, productivity, leaf area", "latest": latest(MOD16_ID, 90)},
        {"id": "chirps", "name": "CHIRPS", "note": "5 km daily rainfall, SPI", "latest": latest(CHIRPS_ID, 90)},
        {"id": "era5", "name": "ERA5-Land", "note": "~9 km monthly soil moisture, SMCI", "latest": latest(ERA5_ID, 120)},
        {"id": "worldcover", "name": "WorldCover", "note": "ESA 2021 cropland mask (static)", "latest": None},
        {"id": "boundary", "name": "Districts", "note": "Your District_Boundary asset", "latest": None},
    ]


# ------------------------------------------------------------------ insights
def build_insights(stats, series):
    """Rule-based drought risk and plain-language alerts (no ML, easy to tune)."""
    m = stats.get("means") or {}
    vci, rain_pct, adsi = m.get("VCI"), m.get("rain_pct"), m.get("ADSI")
    spi, tci, smci = m.get("SPI"), m.get("TCI"), m.get("SMCI")
    sp = stats.get("stressed_pct") or 0.0

    if sp >= 40 or (adsi is not None and adsi >= 0.60):
        level = "Severe"
    elif sp >= 25 or (adsi is not None and adsi >= 0.45):
        level = "High"
    elif sp >= 10 or (adsi is not None and adsi >= 0.30):
        level = "Moderate"
    else:
        level = "Low"

    msgs = []
    if stats.get("complete") is False:
        msgs.append({"level": "info", "title": "Season still running",
                     "text": "This season has not ended yet, so the results are partial."})

    ha = f"{stats.get('stressed_ha') or 0:,.0f}"
    if sp >= 25:
        msgs.append({"level": "alert", "title": "Widespread agricultural drought",
                     "text": f"{sp:.1f}% of cropland ({ha} ha) is in moderate to extreme drought (ADSI)."})
    elif sp >= 10:
        msgs.append({"level": "warn", "title": "Local drought stress",
                     "text": f"{sp:.1f}% of cropland ({ha} ha) is in moderate or worse drought."})
    else:
        msgs.append({"level": "ok", "title": "Crops in good condition",
                     "text": f"Only {sp:.1f}% of cropland is in moderate or worse drought."})

    if rain_pct is not None:
        if rain_pct < 60:
            msgs.append({"level": "alert", "title": "Rainfall deficit",
                         "text": f"Season rainfall is {rain_pct:.0f}% of the {BASE_START}-{BASE_END} normal."})
        elif rain_pct < 85:
            msgs.append({"level": "warn", "title": "Below-normal rainfall",
                         "text": f"Season rainfall is {rain_pct:.0f}% of normal."})
        elif rain_pct > 130:
            msgs.append({"level": "warn", "title": "Above-normal rainfall",
                         "text": f"Season rainfall is {rain_pct:.0f}% of normal. Check for waterlogging."})
        else:
            msgs.append({"level": "ok", "title": "Rainfall near normal",
                         "text": f"Season rainfall is {rain_pct:.0f}% of normal."})

    if spi is not None and spi <= -1.0:
        msgs.append({"level": "alert" if spi <= -1.5 else "warn", "title": "Meteorological drought",
                     "text": f"Mean seasonal SPI is {spi:.2f} (below -1 means a dry season)."})
    if tci is not None and tci < 35:
        msgs.append({"level": "warn", "title": "Heat stress",
                     "text": f"Mean TCI is {tci:.0f}/100. Surface temperatures are near the warm end of the record."})
    if smci is not None and smci < 35:
        msgs.append({"level": "warn", "title": "Dry soils",
                     "text": f"Mean soil moisture condition is {smci:.0f}/100."})

    rows = (series or {}).get("rows") or []
    base_vals = [r["NDVI"] for r in rows if r.get("NDVI") is not None and r["year"] <= BASE_END]
    cur = rows[-1].get("NDVI") if rows else None
    if cur is not None and base_vals:
        delta = cur - sum(base_vals) / len(base_vals)
        if delta <= -0.05:
            msgs.append({"level": "warn", "title": "Greenness below normal",
                         "text": f"Mean NDVI is {abs(delta):.2f} lower than the {BASE_START}-{BASE_END} average."})
        elif delta >= 0.05:
            msgs.append({"level": "ok", "title": "Greenness above normal",
                         "text": f"Mean NDVI is {delta:.2f} higher than the {BASE_START}-{BASE_END} average."})
        else:
            msgs.append({"level": "info", "title": "Greenness near normal",
                         "text": f"Mean NDVI is within 0.05 of the {BASE_START}-{BASE_END} average."})

    worst = sum(c["ha"] for c in stats.get("classes", []) if c["id"] >= 4)
    if worst > 0:
        msgs.append({"level": "info", "title": "Where to look",
                     "text": f"Severe or extreme drought covers {worst:,.0f} ha. Open the Drought layer and zoom in."})

    return {"risk": level, "stressed_pct": sp, "health_index": vci, "adsi": adsi, "messages": msgs}