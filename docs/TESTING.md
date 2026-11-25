# Testing and validation guide

Three levels: offline tests (no Earth Engine), live checks (needs your Earth Engine login), and scientific validation (needs independent data). Do them in order.

## 1. Offline tests (about 5 seconds)

```bash
pip install flask flask-cors pyjwt python-dotenv       # backend dependencies
python -m unittest discover tests                      # statistics and season logic (12 tests)
python -m unittest tests.test_api                      # request validation and routing, Earth Engine mocked (4 tests)
python tests/smoke_graph.py                            # builds all 41 intelligence layers + 10 helper graphs on a mock
```

What they prove: Mann-Kendall and Sen's slope are correct on known series, PCA weights pick the dominant axis, partial seasons are cut and flagged, custom windows and regions are validated, every layer builder runs without Python errors, and every API route rejects bad input with a clear message.

What they cannot prove: that Earth Engine accepts every expression or that a map is scientifically right. That is what sections 2 and 3 are for.

## 2. Live checks (needs `earthengine authenticate`)

```bash
cd backend
python verify_datasets.py
```

It prints one line per dataset (ID, first and last observation date, missing bands), the `gammainc` argument order used for SPI, and your boundary asset's columns. Fix every `BAD` line before the exhibition. If SMAP moved to another version, set `SMAP_ID` in `.env`.

Then start the server (`python app.py`) and try each module on ONE district first (fast), then a province.

| Module | Call (add `&province=Punjab&district=<name>&year=2024&season=kharif`) | Sanity check |
|---|---|---|
| 1 Vegetation | `/api/tiles?layer=s2_savi`, `ls_ndvi`, `s2_ndmi` | Map draws; irrigated fields are greenest in Kharif. Sentinel-2 and Landsat NDVI should look alike |
| 2 Phenology | `/api/intel/phenology` | SOS before POS before EOS; LOS is a multiple of 16; Kharif POS usually mid-season |
| 3 Rainfall | `/api/intel/rain` | SPI values mostly between -3 and 3; cumulative deficit ends equal to total observed minus total normal |
| 4 Drought | `/api/intel/condition?alpha=0.5` | VCI, TCI, VHI between 0 and 100; VHI = 0.5 VCI + 0.5 TCI exactly for alpha 0.5 |
| 5 Soil | `/api/intel/soil` | Root-zone values about 0.1 to 0.4 m3/m3; year 2019 or earlier returns no anomaly and says why |
| 6 ET | `/api/intel/et` | ET/PET between 0 and about 1; ET and PET in hundreds of mm per season |
| 7 Classification | `/api/intel/classify` with no samples | Returns `enabled: false` with the instruction, never a map |
| 8 ASI | `/api/intel/asi` and `/api/tiles?layer=asi` | ASI between 0 and 1; component weights add to 1; `?pca=1` returns weights and the variance explained |
| 9 Hotspots | `/api/intel/hotspots` | Class shares add to 100%; hot-spot share should be small in a spatially uniform year |
| 10 Districts | `/api/intel/districts?province=Punjab` | Cropland ha is far below district ha in desert districts |
| Methodology | `/api/methodology` | 17 indicators, each with formula and limitations |
| Export | `POST /api/export` (needs `EXPORTS_ENABLED=true`) | Task appears in the Earth Engine Tasks tab; a 10 m export of a province is refused with a clear message |

Consistency checks you can run in the Code Editor or a notebook:
- **SPI reference check.** Over the 1991-2020 reference years, SPI for a pixel should have mean near 0 and standard deviation near 1. A large departure means the gamma fit is failing there.
- **Weights.** Set `w=1,0,0,0,0` (vegetation only): the ASI map should equal the vegetation component map.
- **Partial season.** Choose the current year's running season. The banner must appear and the dates must end about 16 days before today.
- **Missing is not zero.** Export a layer and open it in QGIS: pixels without data must be -9999, not 0.
- **Resolution guard.** Export SMAP root zone at 10 m: the task should report a raised scale of 9000 m.

## 3. Scientific validation (pending: needs independent data)

The dashboard states plainly that ASI, the drought classes and the risk gauge are not validated. To validate them you need data the dashboard does not contain:

1. **Yield or crop-loss records** by district and season (provincial Crop Reporting Services, agriculture departments, FAO and national statistics). Compare detrended yield anomaly with ASI, VCI and VHI by district-season. Report the correlation with its sample size and confidence interval. Do not claim skill from a handful of points.
2. **Drought or flood event records** from the national meteorological and disaster management authorities. Check whether the stress classes rise in documented events and stay low in documented normal seasons.
3. **Ground references for classification.** Collect labelled points yourself (field visits, high-resolution imagery interpretation), split them by area, and use the accuracy the dashboard reports. The numbers describe your samples only.
4. **Soil moisture.** Compare SMAP with any in-situ probes you can access before saying anything about field-level moisture.
5. **Phenology.** Compare SOS and POS dates with local sowing and harvest calendars. Treat a difference under about 16 days as agreement.
6. **Cropland mask.** Use `/api/intel/crop_agreement` to see where WorldCover and Dynamic World disagree, then check those places on high-resolution imagery.

Until this is done, describe ASI as a decision-support composite for prioritising attention.

## Performance notes

- ASI, SMAP, SPI and phenology are the heaviest layers. Pick a district for a quick first run; results are cached for 6 hours.
- Pre-load your demo scenarios (see GISDAY_DEMO.md) before visitors arrive.
- For a public deployment keep `EXPORTS_ENABLED=false` and set a gunicorn timeout of 300 s.
