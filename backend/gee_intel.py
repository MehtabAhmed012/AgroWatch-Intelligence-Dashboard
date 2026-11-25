"""AgroWatch Intelligence: advanced analysis modules on top of gee_processor.py.

Sections follow the project architecture:
  1 configuration      2 pure-Python statistics (testable without Earth Engine)
  3 datasets           4 vegetation / radar / thermal      5 phenology
  6 rainfall + SPI     7 soil moisture (SMAP)               8 evapotranspiration
  9 composite stress index (ASI)                            10 hotspots, trends, districts
  11 classification    12 layer registry + tiles             13 exports

Everything is lazy: functions build Earth Engine objects and nothing is computed until a tile or
statistic is requested. Every public get_* function is cached.
"""
import math
import os
from collections import Counter
from datetime import date, timedelta

import ee

import gee_processor as gp
from gee_processor import (BASE_END, BASE_START, CHIRPS_ID, CLOUD_MAX, MOD16_ID, MODIS_ID, S2_ID, _baseline,
                           _base_fc, _crop_mask, _guard, _lst_season, _modis_season, _rain_season, _region_fc,
                           _rr, fields, season_cfg, season_dates, season_range, ttl_cache)

# ============================================================ 1. configuration
LS8_ID = "LANDSAT/LC08/C02/T1_L2"
LS9_ID = "LANDSAT/LC09/C02/T1_L2"
S1_ID = "COPERNICUS/S1_GRD"
SMAP_ID = os.getenv("SMAP_ID", "NASA/SMAP/SPL4SMGP/008")   # verify with backend/verify_datasets.py
ERA5_DAILY_ID = "ECMWF/ERA5_LAND/DAILY_AGGR"

SMAP_START = 2015            # SMAP L4 starts 31 Mar 2015, so Kharif 2015 is the first full season
SMAP_MIN_BASE = 5            # minimum prior seasons for a SMAP anomaly
SPI_REF = (1991, 2020)       # WMO-style 30-year reference for SPI-1/3/6 (CHIRPS starts 1981)
LS_BASE_START = 2014         # first full Landsat 8 season
LS_MIN_BASE = 5
S1_BASE_START = 2017
S1_MIN_BASE = 3
PHENO_THRESHOLD = float(os.getenv("PHENO_THRESHOLD", "0.2"))    # SOS/EOS at 20% of the seasonal amplitude
PHENO_MIN_AMP = 0.15         # pixels with a smaller seasonal NDVI range are not treated as cropped
Z_MAX = 2.0                  # an adverse anomaly of 2 standard deviations scores 1.0 (maximum stress)

ASI_KEYS = ["veg", "rain", "sm", "temp", "et"]
ASI_NAMES = {"veg": "Vegetation anomaly (MODIS NDVI)", "rain": "Rainfall deficit (CHIRPS)",
             "sm": "Root-zone soil moisture (SMAP L4)", "temp": "Thermal stress (MODIS LST)",
             "et": "Evapotranspiration stress (MOD16 ET/PET)"}
ASI_WEIGHTS = {"veg": 0.25, "rain": 0.20, "sm": 0.25, "temp": 0.15, "et": 0.15}
ASI_BREAKS = [0.15, 0.30, 0.50, 0.70]        # provisional class limits (see methodology)
ASI_COLORS = ["#1a9850", "#a6d96a", "#fee08b", "#f46d43", "#a50026"]
ASI_CLASSES = [
    {"id": 1, "name": "No or minimal stress", "range": "ASI below 0.15", "color": ASI_COLORS[0]},
    {"id": 2, "name": "Low stress", "range": "ASI 0.15 to 0.30", "color": ASI_COLORS[1]},
    {"id": 3, "name": "Moderate stress", "range": "ASI 0.30 to 0.50", "color": ASI_COLORS[2]},
    {"id": 4, "name": "High stress", "range": "ASI 0.50 to 0.70", "color": ASI_COLORS[3]},
    {"id": 5, "name": "Severe stress", "range": "ASI 0.70 and above", "color": ASI_COLORS[4]},
]

SPI_CLASSES = [
    {"id": 1, "name": "Near normal or wet", "range": "SPI above -1.0", "color": "#1a9850"},
    {"id": 2, "name": "Moderate drought", "range": "SPI -1.0 to -1.5", "color": "#fdae61"},
    {"id": 3, "name": "Severe drought", "range": "SPI -1.5 to -2.0", "color": "#d73027"},
    {"id": 4, "name": "Extreme drought", "range": "SPI -2.0 or below", "color": "#7f0000"},
]
VHI_CLASSES = [
    {"id": 1, "name": "No drought", "range": "VHI 40 and above", "color": "#1a9850"},
    {"id": 2, "name": "Mild drought", "range": "VHI 30 to 40", "color": "#fee08b"},
    {"id": 3, "name": "Moderate drought", "range": "VHI 20 to 30", "color": "#fdae61"},
    {"id": 4, "name": "Severe drought", "range": "VHI 10 to 20", "color": "#d73027"},
    {"id": 5, "name": "Extreme drought", "range": "VHI below 10", "color": "#7f0000"},
]
SM_CLASSES = [
    {"id": 1, "name": "No water stress", "range": "Root-zone z above -0.5", "color": "#1a9850"},
    {"id": 2, "name": "Low", "range": "z -0.5 to -1.0", "color": "#a6d96a"},
    {"id": 3, "name": "Moderate", "range": "z -1.0 to -1.5", "color": "#fee08b"},
    {"id": 4, "name": "High", "range": "z -1.5 to -2.0", "color": "#f46d43"},
    {"id": 5, "name": "Severe", "range": "z -2.0 or below", "color": "#a50026"},
]
ETVEG_CLASSES = [
    {"id": 1, "name": "No joint signal", "range": "Neither below -1 SD", "color": "#1a9850"},
    {"id": 2, "name": "Vegetation only", "range": "NDVI z at or below -1", "color": "#fee08b"},
    {"id": 3, "name": "ET only", "range": "ET/PET z at or below -1", "color": "#67a9cf"},
    {"id": 4, "name": "Both (possible stress)", "range": "Both at or below -1", "color": "#d73027"},
]
HOT_CLASSES = [
    {"id": 1, "name": "Cold spot (95%+)", "range": "Gi* z at or below -1.96", "color": "#2166ac"},
    {"id": 2, "name": "Not significant", "range": "-1.96 to 1.65", "color": "#4a5568"},
    {"id": 3, "name": "Hot spot 90%", "range": "Gi* z 1.65 to 1.96", "color": "#fee08b"},
    {"id": 4, "name": "Hot spot 95%", "range": "Gi* z 1.96 to 2.58", "color": "#f46d43"},
    {"id": 5, "name": "Hot spot 99%", "range": "Gi* z 2.58 and above", "color": "#a50026"},
]

NDVI_PAL = gp.NDVI_PAL
ANOM_PAL = gp.ANOM_PAL
COND_PAL = gp.COND_PAL
SPI_PAL = gp.SPI_PAL
TEMP_PAL = gp.TEMP_PAL
RAIN_PAL = gp.RAIN_PAL
SM_PAL = ["#8c510a", "#d8b365", "#f6e8c3", "#c7eae5", "#5ab4ac", "#01665e"]    # dry -> wet
ET_PAL = ["#fff7bc", "#d9f0a3", "#78c679", "#31a354", "#006837"]
SAR_PAL = ["#000004", "#51127c", "#b73779", "#fc8961", "#fcfdbf"]
PHENO_PAL = ["#440154", "#3b528b", "#21908d", "#5dc863", "#fde725"]
CLASSIFY_PAL = ["#e6c229", "#4daf4a", "#2c7fb8", "#d95f02", "#bdbdbd", "#984ea3", "#a65628", "#999999"]


# ============================================ 2. pure-Python statistics (no Earth Engine)
def mann_kendall(years, values, min_n=8):
    """Mann-Kendall test and Sen's slope (per year). Needs at least `min_n` valid seasons."""
    pairs = [(y, v) for y, v in zip(years, values) if v is not None]
    n = len(pairs)
    if n < min_n:
        return {"valid": False, "n": n, "reason": f"Needs at least {min_n} seasons, found {n}."}
    xs = [p[1] for p in pairs]
    s = sum((xs[j] > xs[i]) - (xs[j] < xs[i]) for i in range(n - 1) for j in range(i + 1, n))
    ties = Counter(xs).values()
    var = (n * (n - 1) * (2 * n + 5) - sum(t * (t - 1) * (2 * t + 5) for t in ties)) / 18.0
    if var <= 0:
        return {"valid": False, "n": n, "reason": "All values are identical."}
    z = (s - 1) / math.sqrt(var) if s > 0 else (s + 1) / math.sqrt(var) if s < 0 else 0.0
    p = math.erfc(abs(z) / math.sqrt(2))
    slopes = sorted((pairs[j][1] - pairs[i][1]) / (pairs[j][0] - pairs[i][0])
                    for i in range(n - 1) for j in range(i + 1, n) if pairs[j][0] != pairs[i][0])
    mid = len(slopes) // 2
    sen = slopes[mid] if len(slopes) % 2 else (slopes[mid - 1] + slopes[mid]) / 2
    direction = "none" if p >= 0.05 else ("increasing" if sen > 0 else "decreasing")
    return {"valid": True, "n": n, "tau": s / (n * (n - 1) / 2), "z": z, "p": p, "sen_slope": sen,
            "significant": p < 0.05, "direction": direction}


def dominant_eigen(matrix, iters=300):
    """Largest eigenvalue and its vector for a small symmetric matrix (power iteration)."""
    n = len(matrix)
    v = [1.0 / math.sqrt(n)] * n
    for _ in range(iters):
        w = [sum(matrix[i][j] * v[j] for j in range(n)) for i in range(n)]
        norm = math.sqrt(sum(a * a for a in w)) or 1.0
        v = [a / norm for a in w]
    lam = sum(v[i] * sum(matrix[i][j] * v[j] for j in range(n)) for i in range(n))
    return lam, v


def pca_weights_from_cov(cov, keys=ASI_KEYS):
    """First-principal-component weights: |loading| normalised to sum 1, and variance explained."""
    lam, vec = dominant_eigen(cov)
    trace = sum(cov[i][i] for i in range(len(cov))) or 1.0
    mags = [abs(x) for x in vec]
    tot = sum(mags) or 1.0
    return {k: m / tot for k, m in zip(keys, mags)}, lam / trace


def normalise_weights(w):
    tot = sum(max(0.0, v) for v in w.values())
    if tot <= 0:
        raise ValueError("ASI weights must add up to more than zero")
    return {k: max(0.0, v) / tot for k, v in w.items()}


def parse_asi_opts(w_text, pca):
    """Query text 'w=0.25,0.2,0.25,0.15,0.15' (order veg,rain,sm,temp,et) -> hashable options."""
    if w_text:
        try:
            vals = [float(x) for x in w_text.split(",")]
        except ValueError:
            raise ValueError("ASI weights must be numbers separated by commas")
        if len(vals) != len(ASI_KEYS) or any(v < 0 for v in vals):
            raise ValueError("Give five non-negative ASI weights: vegetation, rainfall, soil moisture, temperature, ET")
        w = normalise_weights(dict(zip(ASI_KEYS, vals)))
    else:
        w = ASI_WEIGHTS
    return (tuple(round(w[k], 4) for k in ASI_KEYS), bool(pca))


# ======================================================== 3. dataset / season helpers
def _crop(source=None):
    return _crop_mask(source)


def _month_slices(year, season):
    """The calendar months a season covers, with the day range used inside each month."""
    start, end = season_dates(year, season)
    out, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        d1 = start.day if (cur.year, cur.month) == (start.year, start.month) else 1
        d2 = end.day if (cur.year, cur.month) == (end.year, end.month) else (nxt - timedelta(days=1)).day
        if cur.month == 2:
            d1, d2 = min(d1, 28), min(d2, 28)
        out.append({"y": cur.year, "m": cur.month, "d1": d1, "d2": d2})
        cur = nxt
    return out


def spi_end(year, season):
    """(year, month) of the last COMPLETE month of the season, the end of every SPI window."""
    start, end = season_dates(year, season)
    cutoff = date.today() - timedelta(days=gp.DATA_LAG_DAYS)
    last = min(end, cutoff)
    nxt = date(last.year + (last.month == 12), last.month % 12 + 1, 1)
    if last != nxt - timedelta(days=1):                # month not finished: use the one before
        prev = date(last.year, last.month, 1) - timedelta(days=1)
        last = prev
    if last < start:
        raise ValueError("No complete month of data yet for this season.")
    return last.year, last.month


def _smap_col():
    return ee.ImageCollection(SMAP_ID).select(["sm_surface", "sm_rootzone"])


def _smap_between(start, end):
    """SMAP L4 is 3-hourly. Using one overpass hour per day (10:30 UTC) cuts the compute 8-fold; the
    diurnal range of 9 km soil moisture is small next to its seasonal and inter-annual range."""
    col = _smap_col().filterDate(start, end).filter(ee.Filter.calendarRange(10, 11, "hour"))
    return _guard(col, ["sm_surface", "sm_rootzone"]).mean().rename(["SM_SURF", "SM_ROOT"])


def _smap_season(year, season):
    start, end = season_range(year, season)
    return _smap_between(start, end)


def _smap_baseline(year, season):
    years = list(range(SMAP_START, int(year)))
    if len(years) < SMAP_MIN_BASE:
        return None
    return ee.ImageCollection([_smap_season(y, season) for y in years])


# ================================================ 4. vegetation, radar and thermal
def s2_composite(geom, start, end):
    """Cloud-masked Sentinel-2 median with NDVI, EVI, SAVI, NDMI, NDRE and CIre."""
    def prep(img):
        scl = img.select("SCL")
        ok = scl.neq(1).And(scl.neq(3)).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
        r = img.select(["B2", "B4", "B5", "B8", "B11"]).divide(10000)
        nir, red, blue, re1 = r.select("B8"), r.select("B4"), r.select("B2"), r.select("B5")
        ndvi = r.normalizedDifference(["B8", "B4"]).rename("NDVI")
        evi = r.expression("2.5 * (N - R) / (N + 6.0 * R - 7.5 * B + 1.0)", {"N": nir, "R": red, "B": blue}).rename("EVI")
        savi = r.expression("1.5 * (N - R) / (N + R + 0.5)", {"N": nir, "R": red}).rename("SAVI")
        ndmi = r.normalizedDifference(["B8", "B11"]).rename("NDMI")
        ndre = r.normalizedDifference(["B8", "B5"]).rename("NDRE")
        cire = nir.divide(re1).subtract(1).rename("CIRE")
        return (ndvi.addBands([evi, savi, ndmi, ndre, cire]).updateMask(ok)
                .copyProperties(img, ["system:time_start"]))

    col = (ee.ImageCollection(S2_ID).filterBounds(geom).filterDate(start, end)
           .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", CLOUD_MAX)).map(prep))
    return _guard(col, ["NDVI", "EVI", "SAVI", "NDMI", "NDRE", "CIRE"]).median()


def _ls_prep(img):
    """Landsat Collection 2 Level 2: official scale factors, QA_PIXEL cloud/shadow/snow mask,
    saturation mask and a surface-temperature uncertainty filter (ST_QA <= 5 K)."""
    qa = img.select("QA_PIXEL")
    clear = qa.bitwiseAnd(int("111110", 2)).eq(0).And(img.select("QA_RADSAT").eq(0))
    sr = img.select(["SR_B4", "SR_B5"]).multiply(0.0000275).add(-0.2)
    ndvi = sr.normalizedDifference(["SR_B5", "SR_B4"]).rename("NDVI")
    st = img.select("ST_B10").multiply(0.00341802).add(149.0).subtract(273.15)
    st = st.updateMask(img.select("ST_QA").multiply(0.01).lte(5)).rename("LST")
    return ndvi.addBands(st).updateMask(clear).copyProperties(img, ["system:time_start"])


def ls_season(year, season, geom):
    """Landsat 8 + 9 seasonal mean NDVI and daytime LST (deg C)."""
    start, end = season_range(year, season)
    col = (ee.ImageCollection(LS8_ID).merge(ee.ImageCollection(LS9_ID))
           .filterBounds(geom).filterDate(start, end).filter(ee.Filter.lt("CLOUD_COVER", 60)).map(_ls_prep))
    return _guard(col, ["NDVI", "LST"]).mean()


def landsat_tci(year, season, geom):
    years = list(range(LS_BASE_START, int(year)))
    if len(years) < LS_MIN_BASE:
        raise ValueError(f"Landsat TCI needs at least {LS_MIN_BASE} earlier seasons since {LS_BASE_START}. "
                         f"Choose {LS_BASE_START + LS_MIN_BASE} or later, or use the MODIS-based TCI layer.")
    base = ee.ImageCollection([ls_season(y, season, geom).select("LST") for y in years])
    mn, mx = base.min(), base.max()
    cur = ls_season(year, season, geom).select("LST")
    return mx.subtract(cur).divide(mx.subtract(mn).max(0.5)).multiply(100).clamp(0, 100).rename("TCI_LS")


def s1_season(geom, start, end):
    """Sentinel-1 IW GRD, VV+VH, one orbit direction (the one with more scenes). Averaged in the
    linear domain, then returned in dB. These are backscatter values, NOT soil moisture."""
    col = (ee.ImageCollection(S1_ID).filterBounds(geom).filterDate(start, end)
           .filter(ee.Filter.eq("instrumentMode", "IW"))
           .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
           .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
           .filter(ee.Filter.eq("resolution_meters", 10)))
    asc = col.filter(ee.Filter.eq("orbitProperties_pass", "ASCENDING"))
    desc = col.filter(ee.Filter.eq("orbitProperties_pass", "DESCENDING"))
    use = ee.ImageCollection(ee.Algorithms.If(asc.size().gte(desc.size()), asc, desc)).select(["VV", "VH"])
    use = _guard(use, ["VV", "VH"])
    lin = use.map(lambda i: ee.Image(10).pow(i.divide(10)).copyProperties(i, ["system:time_start"])).mean()
    db = lin.log10().multiply(10)
    return db.addBands(db.select("VV").subtract(db.select("VH")).rename("RATIO"))


def s1_vv_change(geom, year, season):
    years = list(range(S1_BASE_START, int(year)))
    if len(years) < S1_MIN_BASE:
        raise ValueError(f"The Sentinel-1 change layer needs at least {S1_MIN_BASE} earlier seasons since {S1_BASE_START}.")
    base = ee.ImageCollection([s1_season(geom, *season_range(y, season)).select("VV") for y in years]).mean()
    cur = s1_season(geom, *season_range(year, season)).select("VV")
    return cur.subtract(base).rename("VV_CHANGE")


def dw_crops(geom, start, end):
    col = ee.ImageCollection(gp.DW_ID).filterBounds(geom).filterDate(start, end).select("crops")
    return _guard(col, ["crops"]).mean().rename("crops_prob")


def _z(cur, fn, season, band, floor):
    """Standardised anomaly (cur - baseline mean) / baseline SD, using the same season each year."""
    base = _baseline(fn, season).select(band)
    sd = base.reduce(ee.Reducer.stdDev()).max(floor)
    return cur.subtract(base.mean()).divide(sd).rename("z")


# ================================================================ 5. phenology
@ttl_cache(21600)
def _n_composites(year, season):
    start, end = season_range(year, season)
    return int(ee.ImageCollection(MODIS_ID).filterDate(start, end).size().getInfo())


def _modis_ndvi_col(start, end):
    def prep(img):
        ndvi = img.select("NDVI").multiply(0.0001).updateMask(img.select("SummaryQA").lte(1))
        return ndvi.rename("NDVI").copyProperties(img, ["system:time_start"])
    return ee.ImageCollection(MODIS_ID).filterDate(start, end).sort("system:time_start").map(prep)


def phenology_image(year, season):
    """Per-pixel SOS, POS, EOS, LOS, NDVImax and NDVI integral from the 16-day MOD13Q1 series.

    Steps: quality mask -> fill gaps with the seasonal median -> 1-2-1 smoothing -> amplitude
    threshold (SOS/EOS = first/last composite at or above min + 20% of the amplitude) -> day offsets.
    Dates are days since the season start and are approximate (16-day composites, +/- 16 days)."""
    start, end = season_range(year, season)
    col = _modis_ndvi_col(start, end)
    med = col.median()
    filled = col.map(lambda i: i.unmask(med).copyProperties(i, ["system:time_start"]))
    n = filled.size()
    a = filled.toArray().arrayProject([0])
    prev = a.arraySlice(0, 0, 1).arrayCat(a.arraySlice(0, 0, -1), 0)
    nxt = a.arraySlice(0, 1).arrayCat(a.arraySlice(0, -1), 0)
    sm = prev.add(a.multiply(2)).add(nxt).divide(4)
    mx = sm.arrayReduce(ee.Reducer.max(), [0]).arrayGet([0])
    mn = sm.arrayReduce(ee.Reducer.min(), [0]).arrayGet([0])
    amp = mx.subtract(mn)
    thr = mn.add(amp.multiply(PHENO_THRESHOLD))
    idx = ee.Image(ee.Array(ee.List.sequence(0, n.subtract(1))))
    above = sm.gte(thr)
    sos_i = idx.add(above.Not().multiply(1000)).arrayReduce(ee.Reducer.min(), [0]).arrayGet([0])
    eos_i = idx.multiply(above).arrayReduce(ee.Reducer.max(), [0]).arrayGet([0])
    pos_i = sm.arrayArgmax().arrayGet([0])
    off0 = ee.Number(ee.Image(col.first()).date().difference(start, "day"))
    to_day = lambda i: i.multiply(16).add(off0)
    integral = sm.arrayReduce(ee.Reducer.sum(), [0]).arrayGet([0]).multiply(16)
    out = ee.Image.cat([to_day(sos_i).rename("SOS"), to_day(pos_i).rename("POS"), to_day(eos_i).rename("EOS"),
                        eos_i.subtract(sos_i).add(1).multiply(16).rename("LOS"), mx.rename("NDVImax"),
                        integral.rename("NDVIint")])
    return out.updateMask(amp.gte(PHENO_MIN_AMP))


@ttl_cache(21600)
def get_phenology(province, district, year, season):
    if _n_composites(year, season) < 4:
        raise ValueError("Fewer than four MODIS composites exist for this season, so phenology cannot be estimated yet.")
    geom = _region_fc(province, district).geometry()
    img = phenology_image(year, season).updateMask(_crop())
    d = ee.Reducer.median().combine(ee.Reducer.count(), sharedInputs=True)
    raw = _rr(img, d, geom, 500).getInfo()
    start, _ = season_dates(year, season)
    out = {"median": {}, "approximate": True,
           "note": "16-day MODIS composites: dates are approximate (about +/- 16 days), not crop development dates.",
           "start": start.isoformat()}
    for k in ["SOS", "POS", "EOS", "LOS", "NDVImax", "NDVIint"]:
        v = raw.get(f"{k}_median")
        out["median"][k] = v
        if k in ("SOS", "POS", "EOS") and v is not None:
            out[k + "_date"] = (start + timedelta(days=round(v))).isoformat()
    out["pixels"] = raw.get("SOS_count")
    return out


@ttl_cache(21600)
def get_phenology_curve(province, district, year, season):
    """Region-mean NDVI at every composite: selected year against the 2001-2018 average."""
    geom = _region_fc(province, district).geometry()
    crop = _crop()

    def year_features(y):
        start, end = season_range(y, season)
        return ee.FeatureCollection(_modis_ndvi_col(start, end).map(lambda img: ee.Feature(None, {
            "off": img.date().difference(start, "day"),
            "ndvi": img.updateMask(crop).reduceRegion(ee.Reducer.mean(), geom, 1000, None, None, True, 1e13, 8).get("NDVI"),
        })))

    cur = year_features(int(year)).getInfo()["features"]
    base = ee.FeatureCollection([year_features(y) for y in range(BASE_START, BASE_END + 1)]).flatten().getInfo()["features"]
    buckets = {}
    for f in base:
        p = f["properties"]
        if p.get("ndvi") is not None:
            buckets.setdefault(round(p["off"] / 16), []).append(p["ndvi"])
    return {
        "current": [{"off": f["properties"]["off"], "ndvi": f["properties"].get("ndvi")} for f in cur],
        "normal": [{"off": k * 16, "ndvi": sum(v) / len(v)} for k, v in sorted(buckets.items())],
        "year": int(year),
    }


# ============================================== 6. rainfall: SPI-1/3/6, anomalies, deficit
@ttl_cache(10 ** 9)
def _gammainc_shape_first():
    """Earth Engine documents gammainc loosely, so test it once: P(a=2, x=1) must be 0.2642."""
    img = ee.Image.constant(2).gammainc(ee.Image.constant(1))
    v = img.reduceRegion(ee.Reducer.first(), ee.Geometry.Point([69.0, 30.0]), 1000).values().get(0)
    v = float(ee.Number(v).getInfo())
    if abs(v - 0.2642) < 0.01:
        return True
    if abs(v - 0.8647) < 0.01:
        return False
    raise RuntimeError(f"Unexpected gammainc test value {v}. Run backend/verify_datasets.py")


def _window_total(y, end_month, scale):
    end = ee.Date.fromYMD(y, end_month, 1).advance(1, "month")
    start = end.advance(-scale, "month")
    col = ee.ImageCollection(CHIRPS_ID).filterDate(start, end).select("precipitation")
    return _guard(col, ["precipitation"]).sum().rename("rain")


def spi_n(end_year, end_month, scale):
    """SPI-n: gamma distribution fitted per pixel to the 1991-2020 totals of the SAME n-month window
    (Thom's maximum-likelihood approximation), zero-rain probability handled as a mixed distribution,
    then SPI = Phi^-1(H) with H = q + (1 - q) G(x)."""
    n_years = SPI_REF[1] - SPI_REF[0] + 1
    base = ee.ImageCollection([_window_total(y, end_month, scale) for y in range(SPI_REF[0], SPI_REF[1] + 1)])
    cur = _window_total(end_year, end_month, scale)
    q = base.map(lambda i: i.eq(0).rename("rain")).sum().divide(n_years)
    pos = base.map(lambda i: i.updateMask(i.gt(0)))
    mean = pos.mean()
    mean_ln = pos.map(lambda i: i.log()).mean()
    a = mean.log().subtract(mean_ln)
    alpha = a.multiply(4).divide(3).add(1).sqrt().add(1).divide(a.multiply(4))
    beta = mean.divide(alpha)
    x = cur.divide(beta)
    g = alpha.gammainc(x) if _gammainc_shape_first() else x.gammainc(alpha)
    h = q.add(ee.Image(1).subtract(q).multiply(g)).clamp(0.0001, 0.9999)
    return (h.multiply(2).subtract(1).erfInv().multiply(math.sqrt(2))
            .updateMask(mean.gt(0).And(a.gt(0))).rename(f"SPI{scale}"))


def _season_rain_pair(year, season):
    return _rain_season(year, season), _baseline(_rain_season, season).mean()


@ttl_cache(21600)
def get_rain_intel(province, district, year, season):
    """SPI-1/3/6 means, monthly anomaly and cumulative deficit across the season."""
    geom = _region_fc(province, district).geometry()
    crop = _crop()
    ey, em = spi_end(year, season)
    spi_img = ee.Image.cat([spi_n(ey, em, s) for s in (1, 3, 6)]).updateMask(crop)
    slices = _month_slices(year, season)
    bands = []
    for i, sl in enumerate(slices):
        def total(y):
            s0 = ee.Date.fromYMD(y, sl["m"], sl["d1"])
            e0 = ee.Date.fromYMD(y, sl["m"], sl["d2"]).advance(1, "day")
            col = ee.ImageCollection(CHIRPS_ID).filterDate(s0, e0).select("precipitation")
            return _guard(col, ["precipitation"]).sum()
        shift = sl["y"] - int(year)
        obs = total(sl["y"]).rename(f"obs_{i}")
        norm = ee.ImageCollection([total(b + shift) for b in range(BASE_START, BASE_END + 1)]).mean().rename(f"norm_{i}")
        bands += [obs, norm]
    spi_stats = _rr(spi_img, ee.Reducer.mean(), geom, 5566)
    rain_stats = _rr(ee.Image.cat(bands).updateMask(crop), ee.Reducer.mean(), geom, 5566)
    both = ee.Dictionary({"spi": spi_stats, "rain": rain_stats}).getInfo()
    rows, cum_o, cum_n = [], 0.0, 0.0
    for i, sl in enumerate(slices):
        o, nrm = both["rain"].get(f"obs_{i}"), both["rain"].get(f"norm_{i}")
        if o is None or nrm is None:
            continue
        cum_o, cum_n = cum_o + o, cum_n + nrm
        rows.append({"label": f"{gp._MON[sl['m'] - 1]} {sl['y']}", "observed": o, "normal": nrm, "anomaly": o - nrm,
                     "anomaly_pct": (100 * (o - nrm) / nrm) if nrm > 1 else None,
                     "cum_observed": cum_o, "cum_normal": cum_n, "cum_deficit": cum_o - cum_n})
    sp = both["spi"]
    return {"spi": {"SPI1": sp.get("SPI1"), "SPI3": sp.get("SPI3"), "SPI6": sp.get("SPI6")},
            "spi_window_end": f"{ey}-{em:02d}", "rows": rows, "reference": list(SPI_REF)}


# ================================================== 7. soil moisture (SMAP Level 4)
def _smap_z(year, season, band):
    base = _smap_baseline(year, season)
    if base is None:
        raise ValueError(f"SMAP anomalies need at least {SMAP_MIN_BASE} earlier seasons since {SMAP_START}. "
                         f"Choose {SMAP_START + SMAP_MIN_BASE} or later.")
    b = base.select(band)
    cur = _smap_season(year, season).select(band)
    sd = b.reduce(ee.Reducer.stdDev()).max(0.005)
    return cur.subtract(b.mean()).divide(sd).rename("z"), cur.subtract(b.mean()).rename("anom")


@ttl_cache(21600)
def get_soil(province, district, year, season):
    """Seasonal SMAP stats and a monthly root-zone series against the earlier-year monthly normal."""
    geom = _region_fc(province, district).geometry()
    crop = _crop()
    cur = _smap_season(year, season)
    base = _smap_baseline(year, season)
    imgs = [cur]
    if base is not None:
        b = base.select("SM_ROOT")
        imgs += [cur.select("SM_ROOT").subtract(b.mean()).rename("ANOM"),
                 cur.select("SM_ROOT").subtract(b.mean()).divide(b.reduce(ee.Reducer.stdDev()).max(0.005)).rename("Z")]
    seas = _rr(ee.Image.cat(imgs).updateMask(crop), ee.Reducer.mean(), geom, 9000)

    prior = list(range(SMAP_START, int(year)))
    bands = []
    for m in range(1, 13):
        def month_img(y):
            s0 = ee.Date.fromYMD(y, m, 1)
            return _smap_between(s0, s0.advance(1, "month")).select("SM_ROOT")
        bands.append(month_img(int(year)).rename(f"obs_{m}"))
        if len(prior) >= 3:
            bands.append(ee.ImageCollection([month_img(y) for y in prior]).mean().rename(f"norm_{m}"))
    mon = _rr(ee.Image.cat(bands).updateMask(crop), ee.Reducer.mean(), geom, 9000)
    both = ee.Dictionary({"seasonal": seas, "monthly": mon}).getInfo()
    sd = both["seasonal"] or {}
    rows = [{"month": m, "observed": both["monthly"].get(f"obs_{m}"), "normal": both["monthly"].get(f"norm_{m}")}
            for m in range(1, 13)]
    return {"surface": sd.get("SM_SURF"), "rootzone": sd.get("SM_ROOT"), "anomaly": sd.get("ANOM"), "z": sd.get("Z"),
            "rows": rows, "baseline_years": [SMAP_START, int(year) - 1] if base is not None else None,
            "resolution_note": "SMAP L4 is a ~9 km model-assimilation product. District means are indicative; "
                               "it cannot resolve individual fields."}


# ======================================================= 8. evapotranspiration
def _et_season(year, season):
    """Seasonal ET and PET (mm) from the 8-day MOD16A2GF totals, plus ET/PET."""
    start, end = season_range(year, season)
    col = ee.ImageCollection(MOD16_ID).filterDate(start, end).select(["ET", "PET"])
    col = col.map(lambda i: i.updateMask(i.select("ET").lte(32700).And(i.select("PET").lte(32700)))
                  .copyProperties(i, ["system:time_start"]))
    c = _guard(col, ["ET", "PET"])
    et = c.select("ET").sum().multiply(0.1).rename("ET")
    pet = c.select("PET").sum().multiply(0.1).rename("PET")
    ratio = et.divide(pet).updateMask(pet.gt(0)).rename("ETPET")
    return et.addBands([pet, ratio])


def _et_veg_class(year, season):
    z_ndvi = _z(_modis_season(year, season).select("NDVI"), _modis_season, season, "NDVI", 0.02)
    z_et = _z(_et_season(year, season).select("ETPET"), _et_season, season, "ETPET", 0.02)
    v, e = z_ndvi.lte(-1), z_et.lte(-1)
    cls = ee.Image(1).where(v, 2).where(e, 3).where(v.And(e), 4)
    return cls.updateMask(z_ndvi.mask().And(z_et.mask())).rename("class")


@ttl_cache(21600)
def get_et(province, district, year, season):
    geom = _region_fc(province, district).geometry()
    crop = _crop()
    cur = _et_season(year, season)
    base = _baseline(_et_season, season)
    anom = cur.select("ET").subtract(base.select("ET").mean()).rename("ET_ANOM")
    z = _z(cur.select("ETPET"), _et_season, season, "ETPET", 0.02).rename("ETPET_Z")
    means = _rr(cur.addBands([anom, z]).updateMask(crop), ee.Reducer.mean(), geom, 1000)
    area_ha = ee.Image.pixelArea().divide(10000)
    groups = _rr(area_ha.addBands(_et_veg_class(year, season)).updateMask(crop),
                 ee.Reducer.sum().group(groupField=1, groupName="class"), geom, 500).get("groups")
    raw = ee.Dictionary({"means": means, "groups": groups}).getInfo()
    classes, monitored, _ = gp._class_table(raw.get("groups"), ETVEG_CLASSES)
    both = next((c for c in classes if c["id"] == 4), {"ha": 0, "pct": 0})
    return {"means": raw["means"] or {}, "classes": classes, "joint_ha": both["ha"], "joint_pct": both["pct"],
            "limitations": "MOD16 ET is a 500 m model estimate driven by MODIS vegetation and reanalysis "
                           "meteorology. It is weak over irrigated fields, where ET is high regardless of rainfall."}


# ==================================== 9. composite Agricultural Stress Index (ASI)
def _score(z_adverse):
    return z_adverse.divide(Z_MAX).clamp(0, 1)


def _asi_parts(year, season):
    """The five components. `z` is the standardised anomaly, signed so that + means adverse
    (lower NDVI, less rain, drier root zone, hotter, lower ET/PET). `score` is z scaled to 0-1."""
    parts = {}
    z = _z(_modis_season(year, season).select("NDVI"), _modis_season, season, "NDVI", 0.02).multiply(-1)
    parts["veg"] = z
    rain_cur = _rain_season(year, season)
    parts["rain"] = _z(rain_cur, _rain_season, season, "rain", 5).multiply(-1)
    base = _smap_baseline(year, season)
    if base is None:
        parts["sm"] = ee.Image.constant(0).selfMask().rename("z")
    else:
        parts["sm"] = _smap_z(year, season, "SM_ROOT")[0].multiply(-1)
    parts["temp"] = _z(_lst_season(year, season), _lst_season, season, "LST", 0.3)
    parts["et"] = _z(_et_season(year, season).select("ETPET"), _et_season, season, "ETPET", 0.02).multiply(-1)
    return {k: {"z": v.clamp(-3, 3).rename(f"z_{k}"), "score": _score(v).rename(f"s_{k}")} for k, v in parts.items()}


def _asi_combine(parts, weights):
    """Weighted mean of the available components. Where a component is missing (masked), its weight
    is dropped for that pixel, and at least half of the total weight must be present."""
    num, den = ee.Image(0), ee.Image(0)
    for k in ASI_KEYS:
        s, w = parts[k]["score"], weights[k]
        num = num.add(s.unmask(0).multiply(w))
        den = den.add(s.mask().multiply(w))
    total = sum(weights.values())
    return num.divide(den).updateMask(den.gte(0.5 * total)).rename("ASI")


def _asi_class(asi):
    cls = ee.Image(1)
    for i, b in enumerate(ASI_BREAKS):
        cls = cls.where(asi.gte(b), i + 2)
    return cls.updateMask(asi.mask()).rename("class")


@ttl_cache(21600)
def get_pca_weights(province, district, year, season):
    """Data-driven weights: |loadings| of the first principal component of the five adverse z-scores
    over cropland. A simplification (PC1 signs can differ), so the result is shown, not hidden."""
    geom = _region_fc(province, district).geometry()
    parts = _asi_parts(year, season)
    img = ee.Image.cat([parts[k]["z"] for k in ASI_KEYS]).rename(ASI_KEYS).updateMask(_crop())
    cov = img.reduceRegion(ee.Reducer.covariance(), geom, 1000, None, None, True, 1e13, 8).get("array").getInfo()
    if not cov or len(cov) != len(ASI_KEYS):
        raise ValueError("PCA weights are unavailable here (a component has no valid pixels). Use manual weights.")
    w, explained = pca_weights_from_cov(cov)
    return {"weights": w, "explained": explained, "method": "PCA (first component)"}


def _weights(opts, province, district, year, season):
    w_tuple, use_pca = opts
    if use_pca:
        return get_pca_weights(province, district, year, season)["weights"]
    return dict(zip(ASI_KEYS, w_tuple))


def asi_images(province, district, year, season, opts):
    w = _weights(opts, province, district, year, season)
    parts = _asi_parts(year, season)
    asi = _asi_combine(parts, w)
    return {"parts": parts, "asi": asi, "class": _asi_class(asi), "weights": w}


@ttl_cache(21600)
def get_asi(province, district, year, season, opts):
    geom = _region_fc(province, district).geometry()
    crop = _crop()
    a = asi_images(province, district, year, season, opts)
    stack = a["asi"].addBands([a["parts"][k]["score"] for k in ASI_KEYS]).addBands([a["parts"][k]["z"] for k in ASI_KEYS])
    area_ha = ee.Image.pixelArea().divide(10000)
    raw = ee.Dictionary({
        "means": _rr(stack.updateMask(crop), ee.Reducer.mean(), geom, 1000),
        "groups": _rr(area_ha.addBands(a["class"]).updateMask(crop),
                      ee.Reducer.sum().group(groupField=1, groupName="class"), geom, 500).get("groups"),
        "crop_ha": _rr(area_ha.updateMask(crop), ee.Reducer.sum(), geom, 500).get("area"),
    }).getInfo()
    classes, monitored, _ = gp._class_table(raw.get("groups"), ASI_CLASSES)
    stressed = sum(c["ha"] for c in classes if c["id"] >= 3)
    m = raw["means"] or {}
    return {
        "asi": m.get("ASI"), "classes": classes, "monitored_ha": monitored, "crop_ha": raw.get("crop_ha"),
        "stressed_ha": stressed, "stressed_pct": 100 * stressed / monitored if monitored else 0,
        "components": [{"key": k, "name": ASI_NAMES[k], "score": m.get(f"s_{k}"), "z": m.get(f"z_{k}"),
                        "weight": a["weights"][k]} for k in ASI_KEYS],
        "weights": a["weights"], "method": "PCA" if opts[1] else "Manual / default",
        "validation": "Pending. ASI has not been validated against independent crop-loss or yield data.",
    }


@ttl_cache(21600)
def get_asi_series(province, district, year, season, opts, n_years=8):
    """Mean ASI per season for the last `n_years`, built with each year's own baseline."""
    geom = _region_fc(province, district).geometry()
    years = list(range(max(gp.FIRST_YEAR, int(year) - n_years + 1), int(year) + 1))
    w = _weights(opts, province, district, year, season)
    bands = [_asi_combine(_asi_parts(y, season), w).rename(f"ASI_{y}") for y in years]
    d = _rr(ee.Image.cat(bands).updateMask(_crop()), ee.Reducer.mean(), geom, 1000).getInfo()
    return {"rows": [{"year": y, "ASI": d.get(f"ASI_{y}")} for y in years]}


# =================================== 10. hotspots, trends and district intelligence
def hotspot_image(asi, geom, radius=5):
    """Getis-Ord Gi* on ASI aggregated to 1 km, with a square (2r+1) neighbourhood of equal weights.
    Gi* = (sum_w x - Xbar W) / (S sqrt((n W - W^2) / (n - 1)))."""
    proj = ee.Projection("EPSG:4326").atScale(1000)
    x = asi.updateMask(_crop()).reduceResolution(ee.Reducer.mean(), True, 1024).reproject(proj)
    x1 = x.rename("x")
    one = lambda red: ee.Number(x1.reduceRegion(red, geom, 1000, None, None, True, 1e13, 8).get("x"))
    mean, sd, n = one(ee.Reducer.mean()), one(ee.Reducer.stdDev()), one(ee.Reducer.count())
    kernel = ee.Kernel.square(radius, "pixels", False)
    sum_x = x.unmask(0).reduceNeighborhood(ee.Reducer.sum(), kernel)
    w = x.mask().reduceNeighborhood(ee.Reducer.sum(), kernel)
    num = sum_x.subtract(w.multiply(mean))
    den = w.multiply(n.subtract(w)).divide(n.subtract(1)).sqrt().multiply(sd)
    return num.divide(den).updateMask(x.mask()).rename("Gi_z")


def hotspot_class(z):
    cls = (ee.Image(2).where(z.lte(-1.96), 1).where(z.gte(1.65), 3).where(z.gte(1.96), 4).where(z.gte(2.58), 5))
    return cls.updateMask(z.mask()).rename("class")


@ttl_cache(21600)
def get_hotspots(province, district, year, season, opts):
    geom = _region_fc(province, district).geometry()
    a = asi_images(province, district, year, season, opts)
    cls = hotspot_class(hotspot_image(a["asi"], geom))
    area_ha = ee.Image.pixelArea().divide(10000)
    groups = _rr(area_ha.addBands(cls).updateMask(_crop()),
                 ee.Reducer.sum().group(groupField=1, groupName="class"), geom, 1000).get("groups").getInfo()
    rows, monitored, _ = gp._class_table(groups, HOT_CLASSES)
    hot = sum(r["ha"] for r in rows if r["id"] >= 3)
    return {"classes": rows, "hot_ha": hot, "hot_pct": 100 * hot / monitored if monitored else 0,
            "note": "Gi* on ASI at 1 km with an 11 x 11 km neighbourhood. Significance is nominal: spatial "
                    "autocorrelation and multiple testing are not corrected."}


def trend_from_series(series_rows, extra=None):
    """Mann-Kendall + Sen's slope on regional seasonal series (needs 8+ seasons)."""
    years = [r["year"] for r in series_rows]
    out = {}
    for key in ("NDVI", "EVI", "VCI", "rain"):
        out[key] = mann_kendall(years, [r.get(key) for r in series_rows])
    if extra:
        out.update(extra)
    return out


@ttl_cache(21600)
def get_districts(province, year, season, opts):
    """District table: area-weighted cropland area, vegetation, rainfall, soil moisture, ASI and the
    share of valid CROPLAND pixels (not district area) under moderate or worse stress."""
    crop = _crop()
    name_field = fields()["district"]
    fc = _base_fc(province)
    ndvi = _modis_season(year, season).select("NDVI")
    base = _baseline(_modis_season, season).select("NDVI")
    vci = ndvi.subtract(base.min()).divide(base.max().subtract(base.min())).multiply(100).clamp(0, 100).rename("VCI")
    rain, rain_base = _season_rain_pair(year, season)
    rain_pct = rain.divide(rain_base).multiply(100).updateMask(rain_base.gt(10)).rename("rain_pct")
    sm = _smap_season(year, season).select("SM_ROOT")
    a = asi_images(province, None, year, season, opts)
    flag = a["asi"].gte(ASI_BREAKS[1]).updateMask(a["asi"].mask()).rename("stress_frac")
    stack = ndvi.addBands([vci, rain.rename("rain"), rain_pct, sm, a["asi"], flag]).updateMask(crop)
    keep = [name_field, "NDVI", "VCI", "rain", "rain_pct", "SM_ROOT", "ASI", "stress_frac"]
    means = (stack.reduceRegions(collection=fc.select([name_field]), reducer=ee.Reducer.mean(), scale=1000, tileScale=8)
             .select(keep, None, False))
    area_ha = ee.Image.pixelArea().divide(10000).updateMask(crop).rename("crop_ha")
    areas = (area_ha.reduceRegions(collection=fc.select([name_field]), reducer=ee.Reducer.sum(), scale=250, tileScale=8)
             .select([name_field, "sum"], None, False))
    totals = fc.map(lambda f: ee.Feature(None, {name_field: f.get(name_field),
                                                "total_ha": f.geometry().area(200).divide(10000)}))
    raw = ee.Dictionary({"m": means, "a": areas, "t": totals}).getInfo()
    rows = {}
    for f in raw["m"]["features"]:
        p = f["properties"]
        rows[p.get(name_field)] = {"district": p.get(name_field), "ndvi": p.get("NDVI"), "vci": p.get("VCI"),
                                   "rain": p.get("rain"), "rain_pct": p.get("rain_pct"), "sm_root": p.get("SM_ROOT"),
                                   "asi": p.get("ASI"), "stress_frac": p.get("stress_frac")}
    for f in raw["a"]["features"]:
        p = f["properties"]
        if p.get(name_field) in rows:
            rows[p[name_field]]["crop_ha"] = p.get("sum")
    for f in raw["t"]["features"]:
        p = f["properties"]
        if p.get(name_field) in rows:
            rows[p[name_field]]["total_ha"] = p.get("total_ha")
    out = []
    for r in rows.values():
        frac = r.get("stress_frac")
        crop_ha = r.get("crop_ha") or 0
        r["stress_pct"] = None if frac is None else 100 * frac
        r["stressed_ha"] = None if frac is None else crop_ha * frac
        r["crop_pct_of_district"] = (100 * crop_ha / r["total_ha"]) if r.get("total_ha") else None
        out.append(r)
    out.sort(key=lambda r: (r["asi"] is None, -(r["asi"] or 0)))
    return {"rows": out, "weights": a["weights"],
            "note": "Cropland area and the stressed share come from valid cropland pixels only. Means are taken "
                    "at 1 km, so very small districts are approximate."}


@ttl_cache(21600)
def get_condition_series(province, district, year, season, alpha):
    """VCI, TCI and VHI (alpha*VCI + (1-alpha)*TCI) for every season since 2001."""
    geom = _region_fc(province, district).geometry()
    years = list(range(BASE_START, int(year) + 1))
    n_base = _baseline(_modis_season, season).select("NDVI")
    n_min, n_max = n_base.min(), n_base.max()
    l_base = _baseline(_lst_season, season).select("LST")
    l_min, l_max = l_base.min(), l_base.max()
    bands = []
    for y in years:
        ndvi = _modis_season(y, season).select("NDVI")
        lst = _lst_season(y, season)
        vci = ndvi.subtract(n_min).divide(n_max.subtract(n_min).max(0.01)).multiply(100).clamp(0, 100)
        tci = l_max.subtract(lst).divide(l_max.subtract(l_min).max(0.5)).multiply(100).clamp(0, 100)
        vhi = vci.multiply(alpha).add(tci.multiply(1 - alpha))
        bands += [vci.rename(f"VCI_{y}"), tci.rename(f"TCI_{y}"), vhi.rename(f"VHI_{y}")]
    d = _rr(ee.Image.cat(bands).updateMask(_crop()), ee.Reducer.mean(), geom, 1000).getInfo()
    return {"rows": [{"year": y, "VCI": d.get(f"VCI_{y}"), "TCI": d.get(f"TCI_{y}"), "VHI": d.get(f"VHI_{y}")} for y in years],
            "alpha": alpha}


@ttl_cache(21600)
def get_crop_agreement(province, district):
    """Area where WorldCover and Dynamic World agree on cropland. Use it to judge the mask."""
    geom = _region_fc(province, district).geometry()
    wc, dw = gp._worldcover_crop().unmask(0), gp._dw_crop().unmask(0)
    code = wc.add(dw.multiply(2)).rename("code")
    area_ha = ee.Image.pixelArea().divide(10000)
    g = _rr(area_ha.addBands(code), ee.Reducer.sum().group(groupField=1, groupName="code"), geom, 500).get("groups").getInfo()
    by = {int(x["code"]): x["sum"] for x in g or []}
    both, wc_only, dw_only = by.get(3, 0.0), by.get(1, 0.0), by.get(2, 0.0)
    union = both + wc_only + dw_only
    return {"both_ha": both, "worldcover_only_ha": wc_only, "dynamicworld_only_ha": dw_only,
            "agreement_pct": 100 * both / union if union else None, "active_source": gp.CROP_SOURCE,
            "note": "Agreement is not accuracy. Neither product has been validated against ground samples "
                    "for your districts."}


# ============================================ 11. random-forest classification
def _feature_stack(geom, year, season):
    start, end = season_range(year, season)
    col = (ee.ImageCollection(S2_ID).filterBounds(geom).filterDate(start, end)
           .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", CLOUD_MAX)))

    def prep(img):
        scl = img.select("SCL")
        ok = scl.neq(1).And(scl.neq(3)).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
        return img.select(["B2", "B3", "B4", "B8", "B11", "B12"]).divide(10000).updateMask(ok)

    med = _guard(col.map(prep), ["B2", "B3", "B4", "B8", "B11", "B12"]).median()
    ndvi = med.normalizedDifference(["B8", "B4"]).rename("NDVI")
    ndwi = med.normalizedDifference(["B3", "B8"]).rename("NDWI")
    ndbi = med.normalizedDifference(["B11", "B8"]).rename("NDBI")
    ndmi = med.normalizedDifference(["B8", "B11"]).rename("NDMI")
    savi = med.expression("1.5 * (N - R) / (N + R + 0.5)", {"N": med.select("B8"), "R": med.select("B4")}).rename("SAVI")
    s1 = s1_season(geom, start, end).select(["VV", "VH"])
    return med.addBands([ndvi, ndwi, ndbi, ndmi, savi, s1])


def _block_split(fc, label_field):
    """Spatially independent split: samples are grouped in 0.1-degree blocks (about 10 km) and whole
    blocks go to either training (70%) or validation (30%), so validation points are never next to
    training points."""
    def tag(f):
        c = f.geometry().centroid(1).coordinates()
        blk = ee.Number(c.get(0)).multiply(10).floor().multiply(1000).add(ee.Number(c.get(1)).multiply(10).floor())
        return f.set("blk", blk.mod(10))
    tagged = fc.map(tag)
    return tagged.filter(ee.Filter.gte("blk", 3)), tagged.filter(ee.Filter.lt("blk", 3))


def _flat(a):
    """Confusion-matrix accuracy arrays come back as Nx1 or 1xN nested lists; return a flat list."""
    out = []
    for x in a or []:
        out.extend(x if isinstance(x, list) else [x])
    return out


def classification_enabled():
    return {"lulc": True, "crop_types": True,
            "note": "Supply your own reference samples (an Earth Engine FeatureCollection asset). "
                    "No training data are bundled and no accuracy is ever assumed."}


def _check_asset_id(text):
    if not text or not (text.startswith("projects/") or text.startswith("users/")) or len(text) > 200:
        raise ValueError("Samples must be an Earth Engine asset path such as projects/<project>/assets/<name>")
    return text


@ttl_cache(21600)
def run_classification(province, district, year, season, samples, label_field, val_samples, crop):
    """Random Forest on Sentinel-2 bands, indices and Sentinel-1 backscatter.

    `samples`: asset of labelled points/polygons with an integer `label_field`.
    `val_samples`: optional independent asset. If omitted, a spatial block split is used."""
    _check_asset_id(samples)
    geom = _region_fc(province, district).geometry()
    stack = _feature_stack(geom, year, season)
    bands = stack.bandNames()
    fc = ee.FeatureCollection(samples).filterBounds(geom).filter(ee.Filter.notNull([label_field]))
    if val_samples:
        _check_asset_id(val_samples)
        train_fc = fc
        val_fc = ee.FeatureCollection(val_samples).filterBounds(geom).filter(ee.Filter.notNull([label_field]))
        split = "independent validation asset"
    else:
        train_fc, val_fc = _block_split(fc, label_field)
        split = "spatial block split (70/30, 0.1 degree blocks)"
    classes = sorted(int(c) for c in fc.aggregate_array(label_field).distinct().getInfo())
    if len(classes) < 2:
        raise ValueError("Need at least two classes in the sample asset.")
    counts = ee.Dictionary(fc.aggregate_histogram(label_field)).getInfo()
    if min(counts.values()) < 20:
        raise ValueError(f"Every class needs at least 20 samples. Counts: {counts}")
    train = stack.sampleRegions(collection=train_fc, properties=[label_field], scale=10, tileScale=4, geometries=False)
    val = stack.sampleRegions(collection=val_fc, properties=[label_field], scale=10, tileScale=4, geometries=False)
    rf = ee.Classifier.smileRandomForest(numberOfTrees=150, seed=42).train(train, label_field, bands)
    cm = val.classify(rf).errorMatrix(label_field, "classification", classes)
    expl = rf.explain().get("importance")
    res = ee.Dictionary({
        "matrix": cm.array(), "oa": cm.accuracy(), "kappa": cm.kappa(), "producers": cm.producersAccuracy(),
        "users": cm.consumersAccuracy(), "f1": cm.fscore(1), "importance": expl,
        "n_train": train.size(), "n_val": val.size(),
    }).getInfo()
    flat = _flat
    img = stack.classify(rf).rename("class")
    if crop:
        img = img.updateMask(_crop())
    pal = [CLASSIFY_PAL[i % len(CLASSIFY_PAL)] for i in range(len(classes))]
    remap = img.remap(classes, list(range(1, len(classes) + 1))).rename("class")
    url = remap.clip(geom).getMapId({"min": 1, "max": len(classes), "palette": [c.lstrip("#") for c in pal]})["tile_fetcher"].url_format
    return {"url": url, "classes": [{"id": c, "name": f"Class {c}", "color": pal[i], "range": f"label {c}"} for i, c in enumerate(classes)],
            "matrix": res["matrix"], "overall_accuracy": res["oa"], "kappa": res["kappa"],
            "producers_accuracy": flat(res["producers"]), "users_accuracy": flat(res["users"]), "f1": flat(res["f1"]),
            "importance": res["importance"], "n_train": res["n_train"], "n_val": res["n_val"],
            "sample_counts": counts, "split": split, "labels": classes,
            "disclaimer": "Accuracy is computed only from the samples you supplied and describes those samples, "
                          "not the whole district. It is machine-learning output, not an observation."}


# ================================================ 12. layer registry + map tiles
LAYERS = {}


def _reg(key, group, label, title, unit, vis, build, dataset, res, temporal, nodata, category, classes=None, scale=1000):
    LAYERS[key] = {"group": group, "label": label, "title": title, "unit": unit,
                   "kind": "classes" if classes else "continuous", "vis": vis, "classes": classes, "build": build,
                   "dataset": dataset, "resolution": res, "temporal": temporal, "nodata": nodata,
                   "category": category, "scale": scale}


OBS, DER, EST, MOD, DSS = ("Direct observation", "Derived index", "Statistical estimate",
                           "Model product", "Decision-support indicator")
_NO_CLOUD = "Masked where cloud, shadow or snow was detected (SCL), or no clear scene exists in the window."
_NO_MODIS = "Masked where MODIS quality flags exclude the pixel or the pixel is not cropland."
_cp = lambda pal: {"palette": pal}


def _ctx_geom(c):
    return _region_fc(c["province"], c["district"]).geometry()


def _comp(c):
    return s2_composite(_ctx_geom(c), *season_range(c["year"], c["season"]))


def _vis(mn, mx, pal):
    return {"min": mn, "max": mx, "palette": pal}


G_VEG, G_PHE, G_RAD, G_RAIN, G_SM, G_ET, G_DRO, G_ASI = (
    "Vegetation", "Crop phenology", "Radar (Sentinel-1)", "Rainfall", "Soil moisture", "Evapotranspiration",
    "Drought indicators", "Agricultural stress")

_reg("s2_savi", G_VEG, "Sentinel-2 SAVI", "SAVI, Sentinel-2 10 m", "", _vis(0, 0.9, NDVI_PAL),
     lambda c: _comp(c).select("SAVI"), "Sentinel-2 SR Harmonized", "10 m", "Season median", _NO_CLOUD, DER, scale=10)
_reg("s2_ndmi", G_VEG, "Sentinel-2 NDMI", "NDMI (canopy moisture), Sentinel-2 20 m", "", _vis(-0.2, 0.6, SM_PAL),
     lambda c: _comp(c).select("NDMI"), "Sentinel-2 SR Harmonized (B8, B11)", "20 m", "Season median", _NO_CLOUD, DER, scale=20)
_reg("s2_cire", G_VEG, "Red-edge CIre", "Red-edge chlorophyll index (B8/B5 - 1), Sentinel-2 20 m", "", _vis(0, 6, NDVI_PAL),
     lambda c: _comp(c).select("CIRE"), "Sentinel-2 SR Harmonized (B5, B8)", "20 m", "Season median", _NO_CLOUD, DER, scale=20)
_reg("ls_ndvi", G_VEG, "Landsat NDVI", "NDVI, Landsat 8/9 30 m", "", _vis(0, 0.9, NDVI_PAL),
     lambda c: ls_season(c["year"], c["season"], _ctx_geom(c)).select("NDVI"), "Landsat 8/9 Collection 2 Level 2",
     "30 m", "Season mean", "Masked by QA_PIXEL (cloud, shadow, snow) and saturation.", DER, scale=30)
_reg("dw_crops", G_VEG, "Dynamic World crops", "Dynamic World crop probability", "0 to 1", _vis(0, 1, ET_PAL),
     lambda c: dw_crops(_ctx_geom(c), *season_range(c["year"], c["season"])), "Dynamic World V1", "10 m",
     "Season mean", "Masked where no clear Dynamic World scene exists.", MOD, scale=10)

_PH_NOTE = "Shown only where the seasonal NDVI range is at least 0.15 (cropped or seasonally active pixels)."
for _k, _lab, _t, _mx in [("SOS", "Start of season", "Start of season (days since season start)", 120),
                          ("POS", "Peak of season", "Peak of season (days since season start)", 180),
                          ("EOS", "End of season", "End of season (days since season start)", 200),
                          ("LOS", "Length of season", "Length of growing season (days)", 200)]:
    _reg("pheno_" + _k.lower(), G_PHE, _lab, _t, "days", _vis(0, _mx, PHENO_PAL),
         (lambda k: lambda c: phenology_image(c["year"], c["season"]).select(k))(_k),
         "MODIS MOD13Q1 v061", "250 m, 16-day", "Smoothed seasonal series", _PH_NOTE + " Approximate (16-day composites).", DER, scale=250)
_reg("pheno_max", G_PHE, "Maximum NDVI", "Maximum smoothed NDVI in the season", "", _vis(0.2, 0.9, NDVI_PAL),
     lambda c: phenology_image(c["year"], c["season"]).select("NDVImax"), "MODIS MOD13Q1 v061", "250 m, 16-day",
     "Smoothed seasonal series", _PH_NOTE, DER, scale=250)
_reg("pheno_int", G_PHE, "NDVI integral", "Seasonal NDVI integral (NDVI x days)", "NDVI-days", _vis(0, 120, NDVI_PAL),
     lambda c: phenology_image(c["year"], c["season"]).select("NDVIint"), "MODIS MOD13Q1 v061", "250 m, 16-day",
     "Smoothed seasonal series", _PH_NOTE + " A greenness proxy, not a yield estimate.", DER, scale=250)

_s1_note = "Backscatter in dB, NOT soil moisture. Masked where no Sentinel-1 scene exists in the window."
_reg("s1_vv", G_RAD, "S1 VV", "Sentinel-1 VV backscatter", "dB", _vis(-25, 0, SAR_PAL),
     lambda c: s1_season(_ctx_geom(c), *season_range(c["year"], c["season"])).select("VV"),
     "Sentinel-1 GRD (IW, VV+VH)", "10 m", "Season mean (linear)", _s1_note, OBS, scale=10)
_reg("s1_vh", G_RAD, "S1 VH", "Sentinel-1 VH backscatter", "dB", _vis(-30, -5, SAR_PAL),
     lambda c: s1_season(_ctx_geom(c), *season_range(c["year"], c["season"])).select("VH"),
     "Sentinel-1 GRD (IW, VV+VH)", "10 m", "Season mean (linear)", _s1_note, OBS, scale=10)
_reg("s1_ratio", G_RAD, "S1 VV/VH", "VV/VH ratio (VV dB minus VH dB)", "dB", _vis(0, 12, SAR_PAL),
     lambda c: s1_season(_ctx_geom(c), *season_range(c["year"], c["season"])).select("RATIO"),
     "Sentinel-1 GRD (IW, VV+VH)", "10 m", "Season mean (linear)", _s1_note, DER, scale=10)
_reg("s1_change", G_RAD, "S1 VV change", "VV change vs earlier seasons (waterlogging investigation)", "dB",
     _vis(-4, 4, ANOM_PAL), lambda c: s1_vv_change(_ctx_geom(c), c["year"], c["season"]),
     "Sentinel-1 GRD (IW, VV)", "10 m", "Season vs mean of seasons since 2017",
     _s1_note + " A large drop can mean standing water, but it needs field checking.", DER, scale=10)

_reg("spi1", G_RAIN, "SPI-1", "SPI-1 (last complete month)", "", _vis(-2.5, 2.5, SPI_PAL), lambda c: spi_n(c["spi_y"], c["spi_m"], 1),
     "CHIRPS Daily", "~5.5 km", "1-month window, 1991-2020 reference", "Masked where the gamma fit fails (arid, almost no rain).", EST, scale=5566)
_reg("spi3", G_RAIN, "SPI-3", "SPI-3 (3 months to the last complete month)", "", _vis(-2.5, 2.5, SPI_PAL), lambda c: spi_n(c["spi_y"], c["spi_m"], 3),
     "CHIRPS Daily", "~5.5 km", "3-month window, 1991-2020 reference", "Masked where the gamma fit fails (arid, almost no rain).", EST, scale=5566)
_reg("spi6", G_RAIN, "SPI-6", "SPI-6 (6 months to the last complete month)", "", _vis(-2.5, 2.5, SPI_PAL), lambda c: spi_n(c["spi_y"], c["spi_m"], 6),
     "CHIRPS Daily", "~5.5 km", "6-month window, 1991-2020 reference", "Masked where the gamma fit fails (arid, almost no rain).", EST, scale=5566)


def _spi_class_img(c):
    spi = spi_n(c["spi_y"], c["spi_m"], 3)
    return ee.Image(1).where(spi.lte(-1.0), 2).where(spi.lte(-1.5), 3).where(spi.lte(-2.0), 4).updateMask(spi.mask())


_reg("spi_class", G_RAIN, "SPI-3 drought class", "Meteorological drought class (SPI-3, McKee et al.)", "", _vis(1, 4, [x["color"] for x in SPI_CLASSES]),
     _spi_class_img, "CHIRPS Daily", "~5.5 km", "3-month window, 1991-2020 reference", "Masked where SPI cannot be fitted.", EST,
     classes=SPI_CLASSES, scale=5566)


def _deficit(c):
    cur, base = _season_rain_pair(c["year"], c["season"])
    return cur.subtract(base).rename("deficit")


_reg("rain_deficit", G_RAIN, "Rain deficit", "Season rainfall minus 2001-2018 normal", "mm", _vis(-200, 200, SPI_PAL), _deficit,
     "CHIRPS Daily", "~5.5 km", "Season total vs 2001-2018 mean", "No-data over water.", DER, scale=5566)

_reg("sm_surf", G_SM, "SMAP surface", "SMAP L4 surface soil moisture (0-5 cm)", "m3/m3", _vis(0.02, 0.4, SM_PAL),
     lambda c: _smap_season(c["year"], c["season"]).select("SM_SURF"), "SMAP SPL4SMGP", "~9 km (native)", "Season mean of daily 10:30 UTC",
     "Masked where SMAP has no model value (e.g. frozen ground, water).", MOD, scale=9000)
_reg("sm_root", G_SM, "SMAP root zone", "SMAP L4 root-zone soil moisture (0-100 cm)", "m3/m3", _vis(0.05, 0.4, SM_PAL),
     lambda c: _smap_season(c["year"], c["season"]).select("SM_ROOT"), "SMAP SPL4SMGP", "~9 km (native)", "Season mean of daily 10:30 UTC",
     "Masked where SMAP has no model value.", MOD, scale=9000)
_reg("sm_anom", G_SM, "Root-zone anomaly", "Root-zone soil moisture anomaly vs earlier seasons", "m3/m3", _vis(-0.06, 0.06, SM_PAL),
     lambda c: _smap_z(c["year"], c["season"], "SM_ROOT")[1], "SMAP SPL4SMGP", "~9 km (native)", "Season vs mean of seasons since 2015",
     "Needs 5 earlier seasons, so it is unavailable before 2020.", DER, scale=9000)


def _sm_stress(c):
    z = _smap_z(c["year"], c["season"], "SM_ROOT")[0]
    return ee.Image(1).where(z.lte(-0.5), 2).where(z.lte(-1.0), 3).where(z.lte(-1.5), 4).where(z.lte(-2.0), 5).updateMask(z.mask())


_reg("sm_stress", G_SM, "Water-stress zones", "Agricultural water-stress zones (root-zone z-score)", "", _vis(1, 5, [x["color"] for x in SM_CLASSES]),
     _sm_stress, "SMAP SPL4SMGP", "~9 km (native)", "Season vs mean of seasons since 2015", "Needs 5 earlier seasons.", DSS,
     classes=SM_CLASSES, scale=9000)

_et_nd = "Masked over water, barren and non-vegetated land and where MOD16 quality fill values occur."
_reg("et", G_ET, "Actual ET", "Seasonal actual evapotranspiration", "mm", _vis(0, 800, ET_PAL),
     lambda c: _et_season(c["year"], c["season"]).select("ET"), "MODIS MOD16A2GF v061", "500 m, 8-day composites summed",
     "Season sum", _et_nd, MOD, scale=500)
_reg("pet", G_ET, "Potential ET", "Seasonal potential evapotranspiration", "mm", _vis(0, 1500, TEMP_PAL),
     lambda c: _et_season(c["year"], c["season"]).select("PET"), "MODIS MOD16A2GF v061", "500 m, 8-day composites summed",
     "Season sum", _et_nd, MOD, scale=500)
_reg("et_pet", G_ET, "ET/PET ratio", "ET / PET ratio (low = water stress)", "", _vis(0, 0.8, COND_PAL),
     lambda c: _et_season(c["year"], c["season"]).select("ETPET"), "MODIS MOD16A2GF v061", "500 m", "Season sum ratio", _et_nd, DER, scale=500)
_reg("et_anom", G_ET, "ET anomaly", "Seasonal ET anomaly vs 2001-2018", "mm", _vis(-100, 100, ANOM_PAL),
     lambda c: _et_season(c["year"], c["season"]).select("ET").subtract(_baseline(_et_season, c["season"]).select("ET").mean()),
     "MODIS MOD16A2GF v061", "500 m", "Season vs 2001-2018 mean", _et_nd, DER, scale=500)
_reg("et_veg", G_ET, "ET + vegetation stress", "Joint vegetation and ET stress (each at or below -1 SD)", "",
     _vis(1, 4, [x["color"] for x in ETVEG_CLASSES]), lambda c: _et_veg_class(c["year"], c["season"]),
     "MODIS MOD13Q1 + MOD16A2GF", "250-500 m", "Season vs 2001-2018", _et_nd, DSS, classes=ETVEG_CLASSES, scale=500)

_reg("ls_lst", G_DRO, "Landsat LST", "Land surface temperature, Landsat 8/9 30 m", "C", _vis(15, 55, TEMP_PAL),
     lambda c: ls_season(c["year"], c["season"], _ctx_geom(c)).select("LST"), "Landsat 8/9 ST_B10 (Collection 2 L2)", "30 m (100 m native thermal)",
     "Season mean", "Masked by QA_PIXEL, saturation and ST_QA > 5 K.", DER, scale=100)
_reg("ls_tci", G_DRO, "Landsat TCI", "Temperature Condition Index, Landsat 30 m", "0-100", _vis(0, 100, COND_PAL),
     lambda c: landsat_tci(c["year"], c["season"], _ctx_geom(c)), "Landsat 8/9 ST_B10", "30 m", "Season vs earlier seasons since 2014",
     "Needs 5 earlier seasons, so it is unavailable before 2019.", DER, scale=100)
_reg("vhi", G_DRO, "VHI", "Vegetation Health Index (alpha x VCI + (1 - alpha) x TCI)", "0-100", _vis(0, 100, COND_PAL),
     lambda c: _vhi_img(c), "MODIS MOD13Q1 + MOD11A2", "250 m / 1 km", "Season vs 2001-2018 range", _NO_MODIS, DER, scale=1000)


def _vhi_img(c):
    y, s, a = c["year"], c["season"], c["alpha"]
    n = _baseline(_modis_season, s).select("NDVI")
    ndvi = _modis_season(y, s).select("NDVI")
    vci = ndvi.subtract(n.min()).divide(n.max().subtract(n.min()).max(0.01)).multiply(100).clamp(0, 100)
    l = _baseline(_lst_season, s).select("LST")
    tci = l.max().subtract(_lst_season(y, s)).divide(l.max().subtract(l.min()).max(0.5)).multiply(100).clamp(0, 100)
    return vci.multiply(a).add(tci.multiply(1 - a)).rename("VHI")


def _vhi_class(c):
    v = _vhi_img(c)
    return ee.Image(1).where(v.lt(40), 2).where(v.lt(30), 3).where(v.lt(20), 4).where(v.lt(10), 5).updateMask(v.mask())


_reg("vhi_class", G_DRO, "VHI drought class", "Agricultural drought class from VHI (Kogan thresholds)", "", _vis(1, 5, [x["color"] for x in VHI_CLASSES]),
     _vhi_class, "MODIS MOD13Q1 + MOD11A2", "250 m / 1 km", "Season vs 2001-2018 range", _NO_MODIS, DSS, classes=VHI_CLASSES, scale=1000)

_reg("asi", G_ASI, "ASI (continuous)", "Agricultural Stress Index, 0 (none) to 1 (severe)", "0 to 1", _vis(0, 1, ASI_COLORS),
     lambda c: asi_images(c["province"], c["district"], c["year"], c["season"], c["opts"])["asi"],
     "MODIS NDVI/LST/ET + CHIRPS + SMAP L4", "250 m to 9 km (mixed)", "Season vs reference period of each component",
     "Masked where fewer than half of the weighted components are available.", DSS, scale=1000)
_reg("asi_class", G_ASI, "ASI classes", "Agricultural Stress Index, five stress classes", "", _vis(1, 5, ASI_COLORS),
     lambda c: asi_images(c["province"], c["district"], c["year"], c["season"], c["opts"])["class"],
     "MODIS NDVI/LST/ET + CHIRPS + SMAP L4", "250 m to 9 km (mixed)", "Season vs reference period of each component",
     "Masked where fewer than half of the weighted components are available.", DSS, classes=ASI_CLASSES, scale=1000)
for _k in ASI_KEYS:
    _reg("asi_" + _k, G_ASI, "Component: " + _k, "ASI component score: " + ASI_NAMES[_k], "0 to 1", _vis(0, 1, ASI_COLORS),
         (lambda k: lambda c: _asi_parts(c["year"], c["season"])[k]["score"])(_k),
         ASI_NAMES[_k], "see component", "Season vs reference period", "Masked where the component has no valid data.", DER, scale=1000)
LAYERS["asi_veg"]["label"], LAYERS["asi_rain"]["label"], LAYERS["asi_sm"]["label"] = "Vegetation score", "Rainfall score", "Soil moisture score"
LAYERS["asi_temp"]["label"], LAYERS["asi_et"]["label"] = "Thermal score", "ET score"
_reg("hotspot", G_ASI, "Stress hotspots", "Stress hotspots (Getis-Ord Gi* on ASI)", "", _vis(1, 5, [x["color"] for x in HOT_CLASSES]),
     lambda c: hotspot_class(hotspot_image(asi_images(c["province"], c["district"], c["year"], c["season"], c["opts"])["asi"], _ctx_geom(c))),
     "Derived from ASI", "1 km", "Season", "Masked outside cropland.", EST, classes=HOT_CLASSES, scale=1000)

# Metadata for the layers defined in gee_processor.py (their builders stay there).
LEGACY_INFO = {
    "stress": ("MODIS NDVI/LST + CHIRPS + ERA5-Land", "250 m to 9 km", "Season vs 2001-2018", _NO_MODIS, DSS),
    "adsi": ("MODIS NDVI/LST + CHIRPS + ERA5-Land", "250 m to 9 km", "Season vs 2001-2018", _NO_MODIS, DSS),
    "vci_stress": ("MODIS MOD13Q1 v061", "250 m", "Season vs 2001-2018", _NO_MODIS, DSS),
    "s2_ndvi": ("Sentinel-2 SR Harmonized", "10 m", "Season median", _NO_CLOUD, DER),
    "s2_evi": ("Sentinel-2 SR Harmonized", "10 m", "Season median", _NO_CLOUD, DER),
    "s2_ndre": ("Sentinel-2 SR Harmonized (B5, B8)", "20 m", "Season median", _NO_CLOUD, DER),
    "modis_ndvi": ("MODIS MOD13Q1 v061", "250 m, 16-day", "Season mean", _NO_MODIS, DER),
    "anomaly": ("MODIS MOD13Q1 v061", "250 m", "Season vs 2001-2018 mean", _NO_MODIS, DER),
    "lai": ("MODIS MCD15A3H v061", "500 m, 4-day", "Season mean", "Masked where retrievals failed.", MOD),
    "gpp": ("MODIS MOD17A2H v061", "500 m, 8-day", "Season sum", "Masked over non-vegetated land.", MOD),
    "vci": ("MODIS MOD13Q1 v061", "250 m", "Season vs 2001-2018 min-max", _NO_MODIS, DER),
    "rain": ("CHIRPS Daily", "~5.5 km", "Season total", "No-data over water.", OBS),
    "rain_pct": ("CHIRPS Daily", "~5.5 km", "Season vs 2001-2018 mean", "Masked where normal rainfall is below 10 mm.", DER),
    "spi": ("CHIRPS Daily", "~5.5 km", "Season total vs 2001-2018", "Masked where the gamma fit fails.", EST),
    "lst": ("MODIS MOD11A2 v061", "1 km, 8-day", "Season mean", "Masked where QC flags exclude the pixel.", OBS),
    "tci": ("MODIS MOD11A2 v061", "1 km", "Season vs 2001-2018 min-max", "Masked where QC flags exclude the pixel.", DER),
    "smci": ("ERA5-Land monthly (layer 1, 0-7 cm)", "~9 km", "Season vs 2001-2018 min-max", "No-data over water.", MOD),
    "esi_anom": ("MODIS MOD16A2GF v061", "500 m", "Season vs 2001-2018 mean", _et_nd, DER),
    "trend": ("MODIS MOD13Q1 v061", "250 m", "2001 to selected year", "Shown only where Mann-Kendall p < 0.05.", EST),
}
LEGACY_GROUPS = {"stress": "Drought indicators", "adsi": "Drought indicators", "vci_stress": "Drought indicators",
                 "s2_ndvi": G_VEG, "s2_evi": G_VEG, "s2_ndre": G_VEG, "modis_ndvi": G_VEG, "anomaly": G_VEG, "vci": G_VEG,
                 "lai": G_VEG, "gpp": G_VEG, "trend": G_VEG, "rain": G_RAIN, "rain_pct": G_RAIN, "spi": G_RAIN,
                 "lst": G_DRO, "tci": G_DRO, "smci": G_SM, "esi_anom": G_ET}
GROUP_ORDER = ["Drought indicators", G_VEG, G_PHE, G_RAD, G_RAIN, G_SM, G_ET, G_DRO, G_ASI]


def layer_meta():
    """Everything the front end needs to build the layer panel (no Earth Engine calls)."""
    out = {}
    for k, v in gp.LAYERS.items():
        if k in LAYERS:
            continue
        ds, res, tmp, nd, cat = LEGACY_INFO.get(k, ("", "", "", "", DER))
        out[k] = {**v, "group": LEGACY_GROUPS.get(k, "Other"), "dataset": ds, "resolution": res, "temporal": tmp,
                  "nodata": nd, "category": cat}
    for k, v in LAYERS.items():
        out[k] = {x: v[x] for x in ("group", "label", "title", "unit", "kind", "dataset", "resolution", "temporal",
                                    "nodata", "category")}
    return out, GROUP_ORDER


def layer_period(layer, year, season, spi=None):
    """Human-readable analysis period for the legend panel."""
    start, end = season_dates(year, season)
    if layer.startswith("spi") and spi:
        n = {"spi1": 1, "spi3": 3, "spi6": 6, "spi_class": 3}[layer]
        ey, em = spi
        e = date(ey, em, 1)
        s_m = em - n + 1
        s_y = ey
        while s_m < 1:
            s_m, s_y = s_m + 12, s_y - 1
        return f"{date(s_y, s_m, 1):%b %Y} to {e:%b %Y}"
    return f"{start:%d %b %Y} to {end:%d %b %Y}"


@ttl_cache(1800)
def get_tiles(layer, province, district, year, season, crop, opts, alpha, crop_source):
    meta = LAYERS[layer]
    ctx = {"province": province, "district": district, "year": year, "season": season, "opts": opts, "alpha": alpha}
    if layer.startswith("spi"):
        ctx["spi_y"], ctx["spi_m"] = spi_end(year, season)
    img = meta["build"](ctx)
    if crop:
        img = img.updateMask(_crop(crop_source))
    vis = meta["vis"]
    ee_vis = {**vis, "palette": [c.lstrip("#") for c in vis["palette"]]}
    geom = _region_fc(province, district).geometry()
    url = img.clip(geom).getMapId(ee_vis)["tile_fetcher"].url_format
    info = {k: meta[k] for k in ("dataset", "resolution", "temporal", "nodata", "category")}
    return {"url": url, "vis": vis, "label": meta["label"], "title": meta["title"], "unit": meta["unit"],
            "kind": meta["kind"], "classes": meta["classes"] or [], "info": info,
            "period": layer_period(layer, year, season, (ctx.get("spi_y"), ctx.get("spi_m")) if "spi_y" in ctx else None)}


# ======================================================================= 13. exports
EXPORT_BUCKET = os.getenv("EXPORT_GCS_BUCKET") or None
EXPORT_MAX_PIXELS = 4e8


def _export_scale(layer, requested):
    native = LAYERS[layer]["scale"] if layer in LAYERS else 250
    scale = max(float(requested or native), 1.0)
    adjusted = scale < native
    return (native if adjusted else scale), adjusted, native


def start_export(kind, layer, province, district, year, season, crop, opts, alpha, crop_source,
                 scale=None, crs="EPSG:4326", description=None, folder="AgroWatch", region=None):
    """Start an Export.image.toDrive / Export.table.toDrive task (or Cloud Storage if a bucket is set).
    NOTE: Drive exports land in the Drive of the account that owns the Earth Engine credentials. With a
    service account that is the service account's Drive, so use your own credentials or a GCS bucket."""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (description or f"AgroWatch_{kind}_{layer}_{year}_{season}"))[:100]
    folder = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in (folder or "AgroWatch"))[:60]
    if not str(crs).startswith("EPSG:"):
        raise ValueError("CRS must look like EPSG:4326 or EPSG:32642")
    if kind == "table":
        rows = get_districts(province, year, season, opts)["rows"]
        fc = ee.FeatureCollection([ee.Feature(None, {k: (v if v is not None else -9999) for k, v in r.items() if k != "stress_frac"}) for r in rows])
        task = (ee.batch.Export.table.toCloudStorage(collection=fc, description=safe, bucket=EXPORT_BUCKET, fileNamePrefix=f"{folder}/{safe}", fileFormat="CSV")
                if EXPORT_BUCKET else ee.batch.Export.table.toDrive(collection=fc, description=safe, folder=folder, fileFormat="CSV"))
        task.start()
        return {"task": task.id, "description": safe, "destination": "Cloud Storage" if EXPORT_BUCKET else "Google Drive"}
    if layer not in LAYERS and layer not in gp.LAYERS:
        raise ValueError(f"Unknown layer '{layer}'")
    sc, adjusted, native = _export_scale(layer, scale)
    geom = _region_fc(province, district).geometry()
    if region:
        w, s, e, n = region
        geom = ee.Geometry.Rectangle([w, s, e, n])
        area_m2 = (e - w) * 111320 * math.cos(math.radians((s + n) / 2)) * (n - s) * 110574
    else:
        area_m2 = float(geom.area(500).getInfo())
    if area_m2 / (sc * sc) > EXPORT_MAX_PIXELS:
        raise ValueError(f"This export would be about {area_m2 / (sc * sc) / 1e6:,.0f} million pixels at {sc:g} m. "
                         "Choose a district or a coarser scale.")
    if layer in LAYERS:
        ctx = {"province": province, "district": district, "year": year, "season": season, "opts": opts, "alpha": alpha}
        if layer.startswith("spi"):
            ctx["spi_y"], ctx["spi_m"] = spi_end(year, season)
        img = LAYERS[layer]["build"](ctx)
    else:
        a = gp._analysis(province, district, year, season)
        img = gp._trend_image(year, season) if layer == "trend" else a["images"][layer]
    if crop:
        img = img.updateMask(_crop(crop_source))
    # -9999 is written where there is no data, so missing pixels are never exported as zero.
    img = img.toFloat().unmask(-9999).clip(geom)
    kw = dict(image=img, description=safe, region=geom, scale=sc, crs=crs, maxPixels=1e10)
    task = (ee.batch.Export.image.toCloudStorage(bucket=EXPORT_BUCKET, fileNamePrefix=f"{folder}/{safe}", **kw)
            if EXPORT_BUCKET else ee.batch.Export.image.toDrive(folder=folder, **kw))
    task.start()
    return {"task": task.id, "description": safe, "scale": sc, "native_scale": native, "scale_adjusted": adjusted,
            "nodata": -9999, "destination": "Cloud Storage" if EXPORT_BUCKET else "Google Drive"}
