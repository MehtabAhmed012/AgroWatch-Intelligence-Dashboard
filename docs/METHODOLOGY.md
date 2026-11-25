# AgroWatch Intelligence: methodology and data quality

Generated from `backend/methodology.py`, the same register the dashboard serves at `/api/methodology`.

## Data types

- **Direct observation:** A calibrated sensor measurement (reflectance, backscatter, brightness temperature) after quality filtering, or a gauge-satellite rainfall estimate.
- **Derived index:** A formula applied to observations (for example NDVI, VCI, TCI). It is only as good as its inputs.
- **Statistical estimate:** A quantity from a fitted distribution or a test (SPI, Mann-Kendall, Gi*). It depends on the reference period and on assumptions that are stated below.
- **Model product:** Output of a land-surface or retrieval model that assimilates observations (SMAP L4, MOD16 ET, ERA5-Land, MODIS LAI/GPP, Dynamic World). It is not a direct measurement.
- **Machine-learning prediction:** A classifier output (Random Forest). It is valid only for the samples and conditions it was trained on.
- **Decision-support indicator:** A composite or rule-based score built to help prioritise attention (ASI, drought classes, risk gauge). Not a universally validated drought measurement.

## Principles

1. All statistics are computed in Earth Engine from the datasets listed. Nothing is typed in, simulated or back-filled. Where a dataset has no valid pixel, the value is shown as missing, never as zero.
2. Seasons are compared like for like. A season that is still running is cut at today minus a 16-day data lag, and the SAME month-day window is used for every baseline year. Incomplete 2026 data are never treated as a full year.
3. Cropland statistics use valid cropland pixels only (ESA WorldCover class 40 by default, configurable). District area and cropland area are reported separately.
4. Native resolutions are respected. SMAP (about 9 km), ERA5-Land (about 9 km) and CHIRPS (about 5.5 km) are never presented as field-scale data, and nothing is downscaled to imply finer independent measurements.
5. Validation status is stated for every composite. ASI, the drought classes and the risk gauge have NOT been validated against independent crop-loss, yield or survey data. Classification accuracy is reported only from samples you supply.

## What is implemented where

- **Fully implemented in Earth Engine:** Vegetation indices, cloud masking, Landsat LST/TCI, Sentinel-1 backscatter, phenology (array-based), SPI-1/3/6 (gamma fit), VCI/TCI/VHI, SMAP anomalies, ET/PET, ASI with manual or PCA weights, Gi* hotspots, district statistics, Mann-Kendall / Sen's slope maps, Random Forest with accuracy metrics, export tasks.
- **Computed in the Python backend:** Mann-Kendall test and Sen's slope for regional series, PCA weights (eigen-decomposition of a 5x5 covariance matrix fetched from Earth Engine), unit-testable statistics, caching, parameter validation.
- **Needs data you provide:** Random Forest classification needs labelled samples (an Earth Engine FeatureCollection). Crop-type classification is only meaningful with crop reference samples. A district boundary asset is required.
- **Not possible with current data:** Direct soil moisture from Sentinel-1 (needs calibration against in-situ data), field-level SMAP, yield or production estimates, and any validation statement for ASI.

## Indicators

### NDVI and EVI

*Data type: Derived index*

- **Formula:** NDVI = (NIR - Red) / (NIR + Red); EVI = 2.5 (NIR - Red) / (NIR + 6 Red - 7.5 Blue + 1)
- **Source dataset:** Sentinel-2 SR Harmonized (B2, B4, B8); MODIS MOD13Q1 v061 (scale 0.0001)
- **Native spatial resolution:** 10 m (Sentinel-2); 250 m, 16-day (MODIS)
- **Temporal resolution:** Seasonal median (S2) or mean (MODIS)
- **Reference period:** MODIS baseline 2001-2018 for the same season
- **Quality filtering:** S2: SCL excludes saturated, cloud shadow, medium/high cloud, cirrus, snow; scenes above 30% cloud dropped. MODIS: SummaryQA <= 1.
- **Processing method:** Per-scene index, then median (Sentinel-2) or mean (MODIS) over the season window.
- **Limitations:** NDVI saturates in dense canopy; clouds in monsoon months can leave Sentinel-2 gaps; mixed pixels at 250 m.

### SAVI, NDMI, NDRE, CIre

*Data type: Derived index*

- **Formula:** SAVI = 1.5 (NIR - Red) / (NIR + Red + 0.5); NDMI = (NIR - SWIR1) / (NIR + SWIR1); NDRE = (B8 - B5) / (B8 + B5); CIre = B8 / B5 - 1
- **Source dataset:** Sentinel-2 SR Harmonized (B4, B5, B8, B11)
- **Native spatial resolution:** 10 m (SAVI); 20 m (NDMI, NDRE, CIre)
- **Temporal resolution:** Seasonal median
- **Reference period:** None (single-season maps)
- **Quality filtering:** Same SCL mask as NDVI.
- **Processing method:** Computed per scene, then median composite.
- **Limitations:** SAVI soil factor L = 0.5 is a general value, not tuned to Pakistani soils. Red-edge bands are 20 m.

### Landsat NDVI, LST and TCI

*Data type: Derived index*

- **Formula:** SR = DN x 0.0000275 - 0.2; ST (K) = DN x 0.00341802 + 149.0; TCI = 100 (LSTmax - LST) / (LSTmax - LSTmin)
- **Source dataset:** Landsat 8 and 9 Collection 2 Level 2 (SR_B4, SR_B5, ST_B10, QA_PIXEL, QA_RADSAT, ST_QA)
- **Native spatial resolution:** 30 m optical; thermal is 100 m resampled to 30 m
- **Temporal resolution:** Seasonal mean, 16-day revisit per satellite
- **Reference period:** Earlier Landsat seasons since 2014 (at least 5)
- **Quality filtering:** QA_PIXEL bits 1-5 (cloud, shadow, snow), saturation mask, ST uncertainty at most 5 K.
- **Processing method:** Official scale factors; min and max of earlier seasonal means give TCI.
- **Limitations:** Landsat 8 starts 2013, so the TCI reference period is short. Thermal pixels are coarser than the 30 m grid suggests.

### Sentinel-1 backscatter (VV, VH, VV/VH)

*Data type: Direct observation*

- **Formula:** Linear mean of sigma0, then 10 log10; VV/VH = VV(dB) - VH(dB)
- **Source dataset:** COPERNICUS/S1_GRD, IW mode, VV+VH, 10 m
- **Native spatial resolution:** 10 m
- **Temporal resolution:** Seasonal mean; one orbit direction (the one with more scenes)
- **Reference period:** Change layer: mean of seasons since 2017 (at least 3)
- **Quality filtering:** Instrument mode, polarisation and resolution filters. No terrain correction beyond what GEE ingest applies.
- **Processing method:** Averaging in the linear domain avoids the bias of averaging dB values.
- **Limitations:** Backscatter is NOT soil moisture: it also depends on roughness, crop structure and incidence angle. No calibration to in-situ data is applied. Use the change layer only to investigate possible waterlogging.

### Crop phenology (SOS, POS, EOS, LOS, max NDVI, integral)

*Data type: Derived index*

- **Formula:** Smoothed NDVI s(t) = (x[t-1] + 2 x[t] + x[t+1]) / 4; threshold = min + 0.2 (max - min); SOS = first composite >= threshold; EOS = last composite >= threshold; POS = argmax; LOS = (EOS - SOS + 1) x 16 days; integral = sum(s) x 16
- **Source dataset:** MODIS MOD13Q1 v061
- **Native spatial resolution:** 250 m, 16-day composites
- **Temporal resolution:** Within the selected season window
- **Reference period:** None (per-pixel within the season)
- **Quality filtering:** SummaryQA <= 1. Gaps are filled with the seasonal median before smoothing.
- **Processing method:** Array-image operations in Earth Engine. Day numbers count from the season start.
- **Limitations:** APPROXIMATE: with 16-day composites each date is uncertain by about 16 days, so these are not exact crop development dates. A fixed 20% amplitude threshold is a common but arbitrary choice. Double-cropping inside one season window is not separated. Pixels with an NDVI range below 0.15 are masked.

### SPI-1, SPI-3, SPI-6

*Data type: Statistical estimate*

- **Formula:** H(x) = q + (1 - q) G(x; alpha, beta); SPI = Phi^-1(H). alpha, beta from Thom's ML approximation: A = ln(mean) - mean(ln x); alpha = (1 + sqrt(1 + 4A/3)) / (4A); beta = mean / alpha; q = P(x = 0)
- **Source dataset:** UCSB-CHG/CHIRPS/DAILY (precipitation, mm/day)
- **Native spatial resolution:** 0.05 degrees (about 5.5 km), daily
- **Temporal resolution:** Window of n months ending at the last complete month
- **Reference period:** 1991-2020 (30 years), same calendar window
- **Quality filtering:** Pixels where the gamma fit fails (A <= 0 or no positive totals) are masked.
- **Processing method:** Gamma distribution fitted per pixel with a mixed distribution for zero-rain windows, then the inverse normal transform.
- **Limitations:** Needs a reliable gamma incomplete function: backend/verify_datasets.py checks the argument order once. In very arid pixels, zero-rain totals dominate and SPI is unstable. CHIRPS is a blend of satellite and station data.

### Rainfall anomaly and cumulative deficit

*Data type: Derived index*

- **Formula:** Anomaly = observed - normal; % = 100 (observed - normal) / normal; cumulative deficit = running sum of anomalies across the season
- **Source dataset:** CHIRPS Daily
- **Native spatial resolution:** about 5.5 km
- **Temporal resolution:** Monthly, within the season window
- **Reference period:** 2001-2018 mean for the same day range
- **Quality filtering:** Partial last month is cut at the same day in every year.
- **Processing method:** Region mean of cropland pixels.
- **Limitations:** Regional means hide local variation; percent anomalies are unstable where normal rainfall is small.

### VCI, TCI, VHI

*Data type: Derived index*

- **Formula:** VCI = 100 (NDVI - NDVImin) / (NDVImax - NDVImin); TCI = 100 (LSTmax - LST) / (LSTmax - LSTmin); VHI = a VCI + (1 - a) TCI
- **Source dataset:** MODIS MOD13Q1 v061; MODIS MOD11A2 v061 (LST_Day_1km x 0.02 K)
- **Native spatial resolution:** 250 m NDVI; 1 km LST
- **Temporal resolution:** Season mean
- **Reference period:** 2001-2018, the same season in every year (min and max per pixel)
- **Quality filtering:** NDVI: SummaryQA <= 1; LST: QC_Day mandatory flag <= 1. Zero denominators are guarded with a minimum range.
- **Processing method:** a (alpha) is configurable in the interface, default 0.5. Drought classes use the standard VHI thresholds (10, 20, 30, 40).
- **Limitations:** Min and max over 18 seasons are sensitive to extreme years. VHI thresholds were developed for other regions and are not locally calibrated. LST is clear-sky only.

### SMAP surface and root-zone soil moisture

*Data type: Model product*

- **Formula:** Anomaly = SM - mean(SM earlier seasons); z = anomaly / SD. Water-stress zones: z > -0.5, -1, -1.5, -2
- **Source dataset:** NASA/SMAP/SPL4SMGP (sm_surface, sm_rootzone; set SMAP_ID if the version changes)
- **Native spatial resolution:** about 9 km (native EASE-2), 3-hourly
- **Temporal resolution:** Season mean of the 10:30 UTC overpass (one of 8 daily values)
- **Reference period:** All earlier seasons since 2015 (at least 5)
- **Quality filtering:** Model-assimilation product; no pixel-level quality flag is used.
- **Processing method:** Values stay at native resolution. Region statistics use a 9 km scale.
- **Limitations:** SMAP L4 is a land-surface model constrained by satellite brightness temperature, not a field measurement. At 9 km it cannot resolve fields or small districts, and irrigation is not represented. Anomalies are unavailable before 2020.

### Actual and potential ET, ET/PET, ET anomaly

*Data type: Model product*

- **Formula:** ET_season = sum of 8-day ET (x 0.1 kg/m2 = mm); ET/PET = ET_season / PET_season; ET anomaly = ET - mean(ET 2001-2018)
- **Source dataset:** MODIS MOD16A2GF v061
- **Native spatial resolution:** 500 m, 8-day composites (not daily)
- **Temporal resolution:** Season sum
- **Reference period:** 2001-2018
- **Quality filtering:** Fill values above 32700 are masked (water, barren, non-vegetated).
- **Processing method:** Joint stress map: vegetation (NDVI z) and ET/PET z both at or below -1 SD.
- **Limitations:** MOD16 is a Penman-Monteith model driven by reanalysis meteorology. It is weak over irrigated fields, where ET stays high regardless of rainfall, so low ET is not proof of crop stress.

### LAI and GPP

*Data type: Model product*

- **Formula:** LAI = Lai x 0.1; GPP = sum(Gpp) x 0.1 (g C/m2 per season)
- **Source dataset:** MODIS MCD15A3H and MOD17A2H v061
- **Native spatial resolution:** 500 m, 4-day and 8-day
- **Temporal resolution:** Season mean (LAI) or sum (GPP)
- **Reference period:** None
- **Quality filtering:** Product QA flags as supplied.
- **Processing method:** Season aggregation.
- **Limitations:** Retrieval models; noisy over small fields.

### Cropland mask

*Data type: Model product*

- **Formula:** WorldCover class 40; Dynamic World crops probability >= 0.5 (mean 2021-2023); or intersection / union
- **Source dataset:** ESA/WorldCover/v200; GOOGLE/DYNAMICWORLD/V1
- **Native spatial resolution:** 10 m
- **Temporal resolution:** Static (2021) / multi-year mean
- **Reference period:** None
- **Quality filtering:** Agreement between products is reported by /api/intel/crop_agreement.
- **Processing method:** Configurable with CROP_SOURCE in .env.
- **Limitations:** Both are classifications with errors; fallow fields and small plots are often missed. Agreement is not accuracy.

### Agricultural Stress Index (ASI)

*Data type: Decision-support indicator*

- **Formula:** z_i = adverse standardised anomaly (+ = worse); s_i = clamp(z_i / 2, 0, 1); ASI = sum(w_i s_i) / sum(w_i) over available components
- **Source dataset:** MODIS NDVI, CHIRPS season rainfall, SMAP root-zone moisture, MODIS LST, MOD16 ET/PET
- **Native spatial resolution:** 250 m to 9 km (mixed; mapped at 1 km)
- **Temporal resolution:** Season
- **Reference period:** Each component uses its own reference: 2001-2018 (NDVI, rain, LST, ET/PET) and earlier seasons since 2015 (SMAP)
- **Quality filtering:** Components outside a pixel's data are dropped and the remaining weights renormalised; at least half the weight must be present.
- **Processing method:** Equal-ish default weights (veg .25, rain .20, soil .25, temp .15, ET .15) or PCA weights from the first principal component of the five z-scores. Only adverse departures count: normal or better scores 0. Classes (provisional): < 0.15, 0.15-0.30, 0.30-0.50, 0.50-0.70, >= 0.70.
- **Limitations:** NOT a validated drought measurement. Weights and class limits are expert choices. PCA weights use absolute loadings, which ignores the sign structure of the component. Components overlap (rain, soil moisture and NDVI are correlated), so the index can double-count one drought. Validation against independent crop-loss or yield data is pending.

### Stress hotspots (Getis-Ord Gi*)

*Data type: Statistical estimate*

- **Formula:** Gi* = (sum_j x_j - Xbar W) / (S sqrt((n W - W^2) / (n - 1))), equal weights in an 11 x 11 pixel window
- **Source dataset:** Derived from ASI
- **Native spatial resolution:** 1 km
- **Temporal resolution:** Season
- **Reference period:** Region mean and SD of ASI
- **Quality filtering:** Cropland pixels only.
- **Processing method:** Classes at |z| of 1.65, 1.96 and 2.58 (90, 95, 99% nominal).
- **Limitations:** Nominal significance only: no correction for multiple testing or the spatial smoothness already present in coarse inputs.

### Mann-Kendall test and Sen's slope

*Data type: Statistical estimate*

- **Formula:** S = sum sign(x_j - x_i); Z with tie-corrected variance; Sen's slope = median of pairwise slopes
- **Source dataset:** MODIS NDVI (map: per pixel; chart: regional seasonal means)
- **Native spatial resolution:** 250 m / region
- **Temporal resolution:** Seasonal series since 2001
- **Reference period:** n/a
- **Quality filtering:** At least 8 valid seasons are required for the regional test; the map layer shows only p < 0.05.
- **Processing method:** Two-sided test at 5%. Maps use Earth Engine reducers; regional series use the Python implementation (unit tested).
- **Limitations:** Assumes independent observations; serial correlation inflates significance. One season per year gives a short series.

### Random Forest land-cover / crop classification

*Data type: Machine-learning prediction*

- **Formula:** 150-tree Random Forest on Sentinel-2 bands (B2, B3, B4, B8, B11, B12), NDVI, NDWI, NDBI, NDMI, SAVI and Sentinel-1 VV/VH
- **Source dataset:** Sentinel-2 SR Harmonized, Sentinel-1 GRD, your labelled samples
- **Native spatial resolution:** 10 m
- **Temporal resolution:** Season composite
- **Reference period:** n/a
- **Quality filtering:** At least 20 samples per class. Validation uses an independent asset if given, otherwise a 70/30 spatial block split (0.1 degree blocks).
- **Processing method:** Confusion matrix, overall accuracy, producer's and user's accuracy, kappa and F1 are computed from the held-out samples.
- **Limitations:** No training data is bundled and no accuracy is assumed. Reported accuracy describes the supplied samples, not the whole region. Crop-type maps are only meaningful when crop reference samples exist.

### District agricultural intelligence

*Data type: Decision-support indicator*

- **Formula:** Cropland ha = sum(pixel area over cropland); stressed share = mean(ASI >= 0.30) over cropland pixels; stressed ha = cropland ha x share
- **Source dataset:** WorldCover, MODIS, CHIRPS, SMAP, ASI
- **Native spatial resolution:** Means at 1 km; areas at 250 m
- **Temporal resolution:** Season
- **Reference period:** As above
- **Quality filtering:** Only valid cropland pixels enter the share. Total district area comes from the boundary geometry.
- **Processing method:** reduceRegions over the boundary asset.
- **Limitations:** Very small districts contain few 1 km pixels, so their means are approximate.
