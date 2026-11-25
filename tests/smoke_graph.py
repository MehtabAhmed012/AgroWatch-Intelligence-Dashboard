"""Builds every Earth Engine graph against a recording mock of `ee`. It cannot prove the maps are right
(only a live Earth Engine run can), but it catches typos, wrong arity and bad variable names in every
layer builder before you start the server:  python tests/smoke_graph.py"""
import os, sys
from unittest.mock import MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

class M(MagicMock):
    def map(self, fn, *a, **k):
        fn(M()); return M()
    def getInfo(self):
        return {}
sys.modules["ee"] = M()
import gee_intel as gi, gee_processor as gp   # noqa: E402

gp.fields = lambda: {"province": "PROVINCE", "district": "DISTRICT"}
gi.fields = gp.fields
gi._gammainc_shape_first = lambda: True
opts = ((0.25, 0.2, 0.25, 0.15, 0.15), False)
ok = bad = 0
for key, meta in gi.LAYERS.items():
    ctx = {"province": "Punjab", "district": None, "year": 2025, "season": "kharif", "opts": opts, "alpha": 0.5, "spi_y": 2025, "spi_m": 9}
    try:
        meta["build"](ctx); ok += 1
    except ValueError as e:
        ok += 1; print(f"  {key}: guarded -> {e}")
    except Exception as e:
        bad += 1; print(f"FAIL {key}: {type(e).__name__}: {e}")
for name, call in [("asi_images", lambda: gi.asi_images("Punjab", None, 2025, "kharif", opts)),
                   ("hotspot", lambda: gi.hotspot_image(M(), M())),
                   ("phenology", lambda: gi.phenology_image(2025, "kharif")),
                   ("spi_n", lambda: gi.spi_n(2025, 9, 3)),
                   ("feature_stack", lambda: gi._feature_stack(M(), 2025, "kharif")),
                   ("block_split", lambda: gi._block_split(M(), "class")),
                   ("et_veg", lambda: gi._et_veg_class(2025, "kharif")),
                   ("smap_z", lambda: gi._smap_z(2025, "kharif", "SM_ROOT")),
                   ("s1_change", lambda: gi.s1_vv_change(M(), 2025, "kharif")),
                   ("landsat_tci", lambda: gi.landsat_tci(2025, "kharif", M()))]:
    try:
        call(); ok += 1
    except Exception as e:
        bad += 1; print(f"FAIL {name}: {type(e).__name__}: {e}")
print(f"{ok} graphs built, {bad} failed, {len(gi.LAYERS)} intelligence layers registered")
sys.exit(1 if bad else 0)
