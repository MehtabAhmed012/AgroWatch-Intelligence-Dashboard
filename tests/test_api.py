"""API wiring tests with Earth Engine mocked out: python -m unittest tests.test_api (run from the project root)"""
import os, sys, unittest
from unittest.mock import MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("EXPORTS_ENABLED", "false")
fake_ee = MagicMock(); fake_ee.EEException = type("EEException", (Exception,), {})
sys.modules["ee"] = fake_ee
import gee_processor as gp, gee_intel as gi    # noqa: E402
import authentication; authentication.init_earth_engine = lambda: None
gp.list_provinces = lambda: ["Punjab", "Sindh"]
gp.list_districts = lambda p: ["Lahore", "Multan"]
import app as appmod                             # noqa: E402


class Api(unittest.TestCase):
    def setUp(self): self.c = appmod.app.test_client()

    def test_methodology_and_meta(self):
        r = self.c.get("/api/methodology").get_json()
        self.assertTrue(len(r["indicators"]) >= 15 and r["principles"])
        gp.CLASSES, gp.VCI_CLASSES  # exist
        m = self.c.get("/api/meta").get_json()
        self.assertIn("asi_class", m["layers"]); self.assertIn("stress", m["layers"])
        self.assertEqual(m["years"][0], 2016)
        for k, v in m["layers"].items():
            for f in ("dataset", "resolution", "nodata", "category", "group"):
                self.assertIn(f, v, f"{k} missing {f}")

    def test_validation_errors(self):
        for q in ("year=2010", "season=winter", "province=Mars", "district=Atlantis", "district=bbox:1,2,3,4",
                  "year=2030", "season=c1332_0101"):
            r = self.c.get(f"/api/intel/asi?{q}")
            self.assertEqual(r.status_code, 400, q)
        self.assertEqual(self.c.get("/api/intel/asi?w=1,2,3").status_code, 400)
        self.assertEqual(self.c.get("/api/tiles?layer=nope").status_code, 400)
        self.assertEqual(self.c.get("/api/tiles?layer=vhi&alpha=2").status_code, 400)

    def test_export_off_by_default(self):
        r = self.c.post("/api/export", json={"layer": "asi", "year": 2024})
        self.assertEqual(r.status_code, 400); self.assertIn("switched off", r.get_json()["error"])

    def test_custom_region_and_partial_flag(self):
        gi.get_asi = lambda *a: {"asi": 0.2}
        r = self.c.get("/api/intel/asi?year=2024&season=c0601_0915&district=bbox:70,30,71,31&province=Punjab")
        self.assertEqual(r.status_code, 200); self.assertFalse(r.get_json()["season_info"]["partial"])


if __name__ == "__main__":
    unittest.main()
