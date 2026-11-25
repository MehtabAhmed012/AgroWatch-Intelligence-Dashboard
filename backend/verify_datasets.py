"""Run this once before the exhibition:  python verify_datasets.py

It asks Earth Engine whether every dataset ID exists, whether the bands the code uses are present,
what the data's date range is, which argument order gammainc() uses (for SPI) and what columns your
boundary asset has. Nothing here is assumed: every line is read live from Earth Engine."""
import os
from datetime import datetime, timezone

import ee
from dotenv import load_dotenv

load_dotenv()
import authentication as auth  # noqa: E402
import gee_intel as gi  # noqa: E402
import gee_processor as gp  # noqa: E402

auth.init_earth_engine()

CHECKS = [
    (gp.S2_ID, ["B2", "B3", "B4", "B5", "B8", "B11", "B12", "SCL"]),
    (gp.MODIS_ID, ["NDVI", "EVI", "SummaryQA"]),
    (gp.MOD11_ID, ["LST_Day_1km", "QC_Day"]),
    (gp.MOD16_ID, ["ET", "PET"]),
    (gp.MOD17_ID, ["Gpp"]),
    (gp.MCD15_ID, ["Lai"]),
    (gp.CHIRPS_ID, ["precipitation"]),
    (gp.ERA5_ID, ["volumetric_soil_water_layer_1"]),
    (gi.ERA5_DAILY_ID, ["temperature_2m", "total_precipitation_sum", "volumetric_soil_water_layer_1"]),
    (gi.SMAP_ID, ["sm_surface", "sm_rootzone"]),
    (gi.LS8_ID, ["SR_B4", "SR_B5", "ST_B10", "QA_PIXEL", "QA_RADSAT", "ST_QA"]),
    (gi.LS9_ID, ["SR_B4", "SR_B5", "ST_B10", "QA_PIXEL", "QA_RADSAT", "ST_QA"]),
    (gi.S1_ID, ["VV", "VH"]),
    (gp.DW_ID, ["label", "crops"]),
    (gp.WORLDCOVER_ID, ["Map"]),
]


def when(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if ms else "n/a"


problems = 0
for cid, bands in CHECKS:
    try:
        col = ee.ImageCollection(cid)
        have = col.first().bandNames().getInfo()
        lo = col.aggregate_min("system:time_start").getInfo()
        hi = col.aggregate_max("system:time_start").getInfo()
        miss = [b for b in bands if b not in have]
        status = "OK " if not miss else "BAD"
        problems += bool(miss)
        print(f"{status} {cid:34s} {when(lo)} -> {when(hi)}" + (f"   MISSING BANDS: {miss}" if miss else ""))
    except Exception as e:  # noqa: BLE001
        problems += 1
        print(f"BAD {cid:34s} {str(e)[:110]}")

try:
    first = gi._gammainc_shape_first()
    print(f"OK  gammainc order: {'alpha.gammainc(x)' if first else 'x.gammainc(alpha)'} (handled automatically)")
except Exception as e:  # noqa: BLE001
    problems += 1
    print(f"BAD gammainc test failed: {e}")

try:
    props = ee.FeatureCollection(gp.BOUNDARY_ASSET).first().propertyNames().getInfo()
    print(f"OK  boundary asset columns: {props}")
    print(f"    province column: {gp._find(props, gp.PROVINCE_CANDIDATES)}   district column: {gp._find(props, gp.DISTRICT_CANDIDATES)}")
except Exception as e:  # noqa: BLE001
    problems += 1
    print(f"BAD boundary asset {gp.BOUNDARY_ASSET}: {str(e)[:110]}")

print("\nAll checks passed." if not problems else f"\n{problems} problem(s). Fix them (or set SMAP_ID in .env if SMAP moved) before the demo.")
