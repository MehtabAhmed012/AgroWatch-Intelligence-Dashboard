"""Offline tests for the pure-Python statistics (no Earth Engine needed): python -m unittest discover tests"""
import math, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from unittest.mock import MagicMock
sys.modules.setdefault("ee", MagicMock())          # gee_intel imports ee, but these tests never call it
import gee_intel as gi                              # noqa: E402
import gee_processor as gp                          # noqa: E402


class MannKendall(unittest.TestCase):
    def test_increasing_series_is_significant(self):
        yrs = list(range(2001, 2021)); vals = [0.30 + 0.004 * i + (0.002 if i % 2 else 0) for i in range(20)]
        r = gi.mann_kendall(yrs, vals)
        self.assertTrue(r["valid"] and r["significant"] and r["direction"] == "increasing")
        self.assertAlmostEqual(r["sen_slope"], 0.004, places=3)

    def test_flat_noise_is_not_significant(self):
        vals = [0.3, 0.32, 0.29, 0.31, 0.3, 0.33, 0.28, 0.31, 0.3, 0.32]
        r = gi.mann_kendall(list(range(2010, 2020)), vals)
        self.assertFalse(r["significant"])

    def test_known_value(self):                     # classic textbook series: S = 21 for strictly increasing n=7
        r = gi.mann_kendall(list(range(7)), [1, 2, 3, 4, 5, 6, 7], min_n=5)
        self.assertEqual(r["tau"], 1.0)
        self.assertAlmostEqual(r["z"], 20 / math.sqrt(7 * 6 * 19 / 18), places=6)

    def test_too_short_and_constant(self):
        self.assertFalse(gi.mann_kendall([1, 2, 3], [1, 2, 3])["valid"])
        self.assertFalse(gi.mann_kendall(list(range(10)), [5] * 10)["valid"])

    def test_missing_values_skipped(self):
        r = gi.mann_kendall(list(range(12)), [None, 1, 2, 3, None, 5, 6, 7, 8, 9, 10, 11])
        self.assertEqual(r["n"], 10)


class Weights(unittest.TestCase):
    def test_pca_picks_the_dominant_axis(self):
        cov = [[4, 0, 0, 0, 0], [0, 1, 0, 0, 0], [0, 0, 1, 0, 0], [0, 0, 0, 0.5, 0], [0, 0, 0, 0, 0.5]]
        w, explained = gi.pca_weights_from_cov(cov)
        self.assertAlmostEqual(sum(w.values()), 1.0)
        self.assertGreater(w["veg"], 0.99); self.assertAlmostEqual(explained, 4 / 7, places=3)

    def test_parse_and_normalise(self):
        w, pca = gi.parse_asi_opts("1,1,1,1,1", False)
        self.assertEqual(w, (0.2,) * 5); self.assertFalse(pca)
        with self.assertRaises(ValueError): gi.parse_asi_opts("1,2,3", False)
        with self.assertRaises(ValueError): gi.parse_asi_opts("1,1,1,1,-1", False)
        with self.assertRaises(ValueError): gi.parse_asi_opts("0,0,0,0,0", False)
        self.assertEqual(gi.parse_asi_opts(None, True)[1], True)


class Seasons(unittest.TestCase):
    def test_partial_season_is_cut_and_flagged(self):
        from datetime import date
        code, info = gp.resolve_season(2026, "kharif", today=date(2026, 8, 20))
        self.assertTrue(info["partial"]); self.assertEqual(code, "c0501_0804")
        self.assertEqual(gp.season_dates(2026, code)[1], date(2026, 8, 4))

    def test_complete_and_future_seasons(self):
        from datetime import date
        self.assertFalse(gp.resolve_season(2025, "rabi", today=date(2026, 11, 20))[1]["partial"])
        with self.assertRaises(ValueError): gp.resolve_season(2026, "rabi", today=date(2026, 11, 5))
        with self.assertRaises(ValueError): gp.resolve_season(2027, "kharif", today=date(2026, 11, 20))

    def test_custom_window_crossing_year(self):
        cfg = gp.season_cfg("c1115_0215"); self.assertTrue(cfg["crosses"])
        from datetime import date
        self.assertEqual(gp.season_dates(2025, "c1115_0215"), (date(2025, 11, 15), date(2026, 2, 15)))
        with self.assertRaises(ValueError): gp.season_cfg("c1332_0101")
        with self.assertRaises(ValueError): gp.season_cfg("winter")

    def test_bbox_validation(self):
        self.assertEqual(gp.parse_bbox("bbox:70,30,71,31"), [70, 30, 71, 31])
        self.assertIsNone(gp.parse_bbox("Lahore"))
        for bad in ("bbox:70,30,69,31", "bbox:10,10,11,11", "bbox:a,b,c,d", "bbox:61,24,78,37"):
            with self.assertRaises(ValueError): gp.parse_bbox(bad)

    def test_month_slices_rabi(self):
        sl = gi._month_slices(2025, "rabi")
        self.assertEqual([(x["y"], x["m"]) for x in sl][:2], [(2025, 11), (2025, 12)]); self.assertEqual(len(sl), 6)
        self.assertEqual(sl[-1]["d2"], 30)


if __name__ == "__main__":
    unittest.main()
