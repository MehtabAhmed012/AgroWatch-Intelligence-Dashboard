/*****************************************************************
 AgroWatch v2 - Multi-indicator Agricultural Drought Monitoring (Pakistan)
 Question: Which agricultural regions are experiencing vegetation / drought stress?

 HOW TO USE
   1. Open https://code.earthengine.google.com
   2. Scripts tab -> NEW -> Script. Delete the default code, paste this file.
   3. Click Save, then Run. Pick filters in the left panel -> "Run analysis".

 DATA
   Sentinel-2 SR Harmonized (10 m)  -> NDVI / EVI / NDRE detail maps
   MODIS MOD13Q1 v061 (250 m)       -> NDVI/EVI baseline, anomaly, VCI, trend
   MODIS MOD11A2 v061 (1 km)        -> land surface temperature -> TCI
   MODIS MCD15A3H / MOD17A2H        -> LAI, GPP
   MODIS MOD16A2GF v061 (500 m)     -> ET / PET -> ESI
   CHIRPS Daily (5 km)              -> rainfall, anomaly, SPI (gamma-fitted)
   ERA5-Land Monthly (~9 km)        -> soil moisture -> SMCI
   ESA WorldCover v200 (10 m)       -> cropland mask (class 40)
   District_Boundary (your asset)   -> province / district boundaries

 INTEGRATED INDEX
   ADSI = wV*VCI_s + wS*SPI_s + wT*TCI_s + wM*SMCI_s
   (each indicator converted so that 1 = most severe drought, 0 = no stress)
   ADSI is a proposed research index, not an established standard.
*****************************************************************/


// ================================================================
// 1. SETTINGS
// ================================================================
var DEFAULT_PROVINCE = 'Punjab';

var PROVINCE_FIELD = null;
var DISTRICT_FIELD = null;
var PROVINCE_CANDIDATES = ['PROVINCE', 'Prov_Name', 'PROV', 'ADM1_NAME', 'NAME_1', 'ADM1_EN'];
var DISTRICT_CANDIDATES = ['DISTRICT', 'Dist_Name', 'DIST', 'ADM2_NAME', 'NAME_2', 'ADM2_EN', 'NAME'];

var BASE_START = 2001;          // long-term baseline ("normal" conditions)
var BASE_END = 2018;
var CLOUD_MAX = 30;
var YEARS = ['2019', '2020', '2021', '2022', '2023', '2024', '2025', '2026'];

var SEASONS = {
  'Kharif (May-Oct)': {m1: 5, m2: 10, crosses: false},
  'Rabi (Nov-Apr)': {m1: 11, m2: 4, crosses: true}
};

// ADSI weights (equal by default). Change to run a sensitivity test.
var W = {vci: 0.25, spi: 0.25, tci: 0.25, smci: 0.25};

// Class thresholds
//   VCI (0-100, low = bad):  >=50, 35-50, 20-35, 10-20, <10
//   ADSI (0-1, high = bad):  <0.30, 0.30-0.45, 0.45-0.60, 0.60-0.75, >=0.75   (provisional)
var VCI_NAMES = ['Normal (VCI >= 50)', 'Low stress (35-50)', 'Moderate stress (20-35)',
                 'Severe stress (10-20)', 'Extreme stress (< 10)'];
var ADSI_NAMES = ['Normal (ADSI < 0.30)', 'Mild drought (0.30-0.45)', 'Moderate drought (0.45-0.60)',
                  'Severe drought (0.60-0.75)', 'Extreme drought (>= 0.75)'];
var CLASS_COLORS = ['#1a9850', '#fee08b', '#fdae61', '#d73027', '#7f0000'];

var MAP_MODES = ['ADSI (integrated drought)', 'VCI (vegetation only)'];

var NDVI_PAL = ['#ca1107', '#fc8d59', '#fee08b', '#d9ef8b', '#91cf60', '#1a9850', '#006837'];
var ANOM_PAL = ['#5ab4ac', '#c7eae5', '#f5f5f5', '#f6e8c3', '#d8b365', '#8c510a', '#01665e'];
var RAIN_PAL = ['#253494', '#ffffcc', '#c7e9b4', '#7fcdbb', '#41b6c4', '#2c7fb8'];
var PCT_PAL =  ['#313695', '#1a9850', '#a6d96a', '#ffffbf', '#fee08b', '#f46d43', '#bb0000'];
var SPI_PAL =  ['#8c0000', '#d73027', '#fdae61', '#ffffbf', '#a6d96a', '#1a9850', '#006837'];
var TEMP_PAL = ['#313695', '#74add1', '#ffffbf', '#fdae61', '#d73027', '#7f0000'];
var COND_PAL = ['#7f0000', '#d73027', '#fdae61', '#ffffbf', '#a6d96a', '#1a9850'];   // low = bad
var STRESS_PAL = ['#1a9850', '#ffffbf', '#fdae61', '#d73027', '#7f0000'];            // high = bad
var TREND_PAL = ['#b2182b', '#ef8a62', '#f7f7f7', '#67a9cf', '#2166ac'];


// ================================================================
// 2. DATASETS
// ================================================================
var District_Boundary = ee.FeatureCollection('projects/semiotic-timer-429307-g6/assets/District_Boundary');

var S2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED');
var MODIS = ee.ImageCollection('MODIS/061/MOD13Q1');
var MOD11 = ee.ImageCollection('MODIS/061/MOD11A2');
var MOD16 = ee.ImageCollection('MODIS/061/MOD16A2GF');
var MOD17 = ee.ImageCollection('MODIS/061/MOD17A2H');
var MCD15 = ee.ImageCollection('MODIS/061/MCD15A3H');
var CHIRPS = ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY');
var ERA5 = ee.ImageCollection('ECMWF/ERA5_LAND/MONTHLY_AGGR');

var cropMask = ee.Image(ee.ImageCollection('ESA/WorldCover/v200').first())
  .select('Map').eq(40).selfMask();


// ================================================================
// 3. HELPER FUNCTIONS
// ================================================================
function seasonRange(year, season) {
  var cfg = SEASONS[season];
  var y = ee.Number(year);
  var endYear = cfg.crosses ? y.add(1) : y;
  return {
    start: ee.Date.fromYMD(y, cfg.m1, 1),
    end: ee.Date.fromYMD(endYear, cfg.m2, 1).advance(1, 'month')
  };
}

// Sentinel-2: SCL cloud/shadow/snow mask + NDVI, EVI, NDRE.
function maskAndIndexS2(img) {
  var scl = img.select('SCL');
  var ok = scl.neq(3).and(scl.neq(8)).and(scl.neq(9)).and(scl.neq(10)).and(scl.neq(11));
  var refl = img.select(['B2', 'B4', 'B5', 'B8']).divide(10000);
  var ndvi = refl.normalizedDifference(['B8', 'B4']).rename('NDVI');
  var ndre = refl.normalizedDifference(['B8', 'B5']).rename('NDRE');
  var evi = refl.expression(
    '2.5 * (NIR - RED) / (NIR + 6.0 * RED - 7.5 * BLUE + 1.0)',
    {NIR: refl.select('B8'), RED: refl.select('B4'), BLUE: refl.select('B2')}
  ).rename('EVI');
  return ndvi.addBands(evi).addBands(ndre).updateMask(ok)
    .copyProperties(img, ['system:time_start']);
}

// ---- Seasonal single-year builders (each returns ONE image for the season) ----

function modisSeason(year, season) {            // bands: NDVI, EVI
  var r = seasonRange(year, season);
  return MODIS.filterDate(r.start, r.end).map(function (img) {
    var good = img.select('SummaryQA').lte(1);
    return img.select(['NDVI', 'EVI']).multiply(0.0001).updateMask(good)
      .copyProperties(img, ['system:time_start']);
  }).mean();
}

function rainSeason(year, season) {             // band: rain (mm)
  var r = seasonRange(year, season);
  return CHIRPS.filterDate(r.start, r.end).select('precipitation').sum().rename('rain');
}

function lstSeason(year, season) {              // band: LST (deg C, daytime)
  var r = seasonRange(year, season);
  return MOD11.filterDate(r.start, r.end).map(function (img) {
    var ok = img.select('QC_Day').bitwiseAnd(3).lte(1);
    return img.select('LST_Day_1km').multiply(0.02).subtract(273.15).updateMask(ok)
      .rename('LST').copyProperties(img, ['system:time_start']);
  }).mean();
}

function smSeason(year, season) {               // band: SM (m3/m3, 0-7 cm)
  var r = seasonRange(year, season);
  return ERA5.filterDate(r.start, r.end).select('volumetric_soil_water_layer_1').mean().rename('SM');
}

function esiSeason(year, season) {              // band: ESI = ET / PET
  var r = seasonRange(year, season);
  var c = MOD16.filterDate(r.start, r.end).select(['ET', 'PET']);
  var et = c.select('ET').mean();
  var pet = c.select('PET').mean();
  return et.divide(pet).updateMask(pet.gt(0)).rename('ESI');
}

function laiSeason(year, season) {              // band: LAI
  var r = seasonRange(year, season);
  return MCD15.filterDate(r.start, r.end).select('Lai').mean().multiply(0.1).rename('LAI');
}

function gppSeason(year, season) {              // band: GPP (g C / m2 / season)
  var r = seasonRange(year, season);
  // Gpp scale 0.0001 (kg C/m2 per 8 days) -> x1000 for grams
  return MOD17.filterDate(r.start, r.end).select('Gpp').sum().multiply(0.1).rename('GPP');
}

// One image per baseline year (same season) -> used for mean / min / max.
function baselineCollection(fn, season) {
  return ee.ImageCollection(
    ee.List.sequence(BASE_START, BASE_END).map(function (y) { return fn(y, season); })
  );
}

// Condition index (0-100) from the baseline min/max.
//   invert=false -> (cur - min)/(max - min)  (VCI, SMCI: high = healthy/wet)
//   invert=true  -> (max - cur)/(max - min)  (TCI: high = cool = good)
function condIdx(cur, fn, season, band, invert, outName) {
  var base = baselineCollection(fn, season).select(band);
  var mn = base.min();
  var mx = base.max();
  var num = invert ? mx.subtract(cur) : cur.subtract(mn);
  return num.divide(mx.subtract(mn)).multiply(100).clamp(0, 100).rename(outName);
}

// SPI of the seasonal rainfall total (6-month window for both seasons).
// Gamma distribution fitted per pixel to the baseline totals (Thom's ML approximation),
// with the zero-rainfall probability handled as a mixed distribution:
//   H(x) = q + (1 - q) * G(x);   SPI = Phi^-1(H)
// NOTE: gammainc(a, x) = regularized lower incomplete gamma. If your GEE version
// returns an error here, check the argument order in the API docs (ee.Image.gammainc).
function spiSeason(year, season) {
  var base = baselineCollection(rainSeason, season);
  var cur = rainSeason(year, season);
  var n = BASE_END - BASE_START + 1;

  var q = base.map(function (img) { return img.eq(0).rename('rain'); }).sum().divide(n);
  var pos = base.map(function (img) { return img.updateMask(img.gt(0)); });
  var mean = pos.mean();
  var meanLn = pos.map(function (img) { return img.log(); }).mean();

  var A = mean.log().subtract(meanLn);
  var alpha = A.multiply(4).divide(3).add(1).sqrt().add(1).divide(A.multiply(4));
  var beta = mean.divide(alpha);

  var G = alpha.gammainc(cur.divide(beta));
  var H = q.add(ee.Image(1).subtract(q).multiply(G)).clamp(0.0001, 0.9999);
  return H.multiply(2).subtract(1).erfInv().multiply(Math.SQRT2)
    .updateMask(mean.gt(0).and(A.gt(0))).rename('SPI');
}

function fmtInt(n) {
  if (n === null || n === undefined || isNaN(n)) { return 'n/a'; }
  return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}
function fmtNum(n, d) {
  if (n === null || n === undefined || isNaN(n)) { return 'n/a'; }
  return Number(n).toFixed(d);
}


// ================================================================
// 4. USER INTERFACE
// ================================================================
var statusLabel = ui.Label('', {color: '#637381'});
var statsPanel = ui.Panel();
var chartsPanel = ui.Panel();

var provinceSelect = ui.Select({
  items: ['Loading...'], value: 'Loading...', onChange: loadDistricts,
  style: {stretch: 'horizontal'}
});
var districtSelect = ui.Select({
  items: ['All districts'], value: 'All districts', style: {stretch: 'horizontal'}
});
var yearSelect = ui.Select({items: YEARS, value: '2025', style: {stretch: 'horizontal'}});
var seasonSelect = ui.Select({
  items: Object.keys(SEASONS), value: 'Kharif (May-Oct)', style: {stretch: 'horizontal'}
});
var modeSelect = ui.Select({items: MAP_MODES, value: MAP_MODES[0], style: {stretch: 'horizontal'}});
var cropOnly = ui.Checkbox('Show cropland only (ESA WorldCover)', true);
var runButton = ui.Button({label: 'Run analysis', onClick: runAnalysis, style: {stretch: 'horizontal'}});

function lbl(text) { return ui.Label(text, {margin: '8px 0 2px 0', fontSize: '12px', color: '#637381'}); }

var provinceLabel = lbl('Province');
var panel = ui.Panel({style: {width: '440px', padding: '10px'}});
panel.add(ui.Label('AgroWatch', {fontSize: '24px', fontWeight: 'bold', color: '#0b6e4f'}));
panel.add(ui.Label('Multi-indicator agricultural drought monitoring - vegetation, rainfall, thermal and soil-water stress',
  {fontSize: '12px', color: '#637381'}));
panel.add(provinceLabel);
panel.add(provinceSelect);
panel.add(lbl('District'));
panel.add(districtSelect);
panel.add(lbl('Year (season start)'));
panel.add(yearSelect);
panel.add(lbl('Crop season'));
panel.add(seasonSelect);
panel.add(lbl('Main map classification'));
panel.add(modeSelect);
panel.add(cropOnly);
panel.add(runButton);
panel.add(statusLabel);
panel.add(statsPanel);
panel.add(chartsPanel);
ui.root.insert(0, panel);

Map.setOptions('HYBRID');
Map.setCenter(69.5, 30.5, 6);

var legendPanel = null;
function setLegend(title, names) {
  if (legendPanel) { Map.remove(legendPanel); }
  legendPanel = ui.Panel({style: {position: 'bottom-left', padding: '8px 12px'}});
  legendPanel.add(ui.Label(title, {fontWeight: 'bold', margin: '0 0 4px 0'}));
  for (var i = 0; i < names.length; i++) {
    legendPanel.add(ui.Panel([
      ui.Label('', {backgroundColor: CLASS_COLORS[i], padding: '8px', margin: '0 8px 4px 0'}),
      ui.Label(names[i], {margin: '0 0 4px 0'})
    ], ui.Panel.Layout.Flow('horizontal')));
  }
  Map.add(legendPanel);
}

function addRow(name, value, color) {
  var chip = ui.Label('', {backgroundColor: color || '#ffffff', padding: '6px', margin: '2px 6px 2px 0'});
  statsPanel.add(ui.Panel([
    chip,
    ui.Label(name + ':', {fontWeight: 'bold', margin: '3px 6px 2px 0'}),
    ui.Label(value, {margin: '3px 0 2px 0'})
  ], ui.Panel.Layout.Flow('horizontal')));
}


// ================================================================
// 5. READ THE ASSET'S COLUMNS, THEN FILL THE DROPDOWNS
// ================================================================
function findField(props, candidates) {
  for (var i = 0; i < candidates.length; i++) {
    for (var j = 0; j < props.length; j++) {
      if (props[j].toLowerCase() === candidates[i].toLowerCase()) { return props[j]; }
    }
  }
  return null;
}

District_Boundary.first().propertyNames().evaluate(function (props) {
  if (!props) {
    statusLabel.setValue('Could not read District_Boundary. Check the asset path and that you have access.');
    return;
  }
  print('District_Boundary columns:', props);
  PROVINCE_FIELD = PROVINCE_FIELD || findField(props, PROVINCE_CANDIDATES);
  DISTRICT_FIELD = DISTRICT_FIELD || findField(props, DISTRICT_CANDIDATES);
  if (!DISTRICT_FIELD) {
    statusLabel.setValue('No district-name column found. Columns: ' + props.join(', ') +
      ' -> set DISTRICT_FIELD at the top of the script.');
    return;
  }
  if (PROVINCE_FIELD) {
    loadProvinces();
  } else {
    provinceLabel.style().set('shown', false);
    provinceSelect.style().set('shown', false);
    loadDistricts();
  }
});

function loadProvinces() {
  District_Boundary.filter(ee.Filter.notNull([PROVINCE_FIELD]))
    .aggregate_array(PROVINCE_FIELD).distinct().sort()
    .evaluate(function (names) {
      if (!names || names.length === 0) {
        statusLabel.setValue('No values found in column ' + PROVINCE_FIELD + '.');
        return;
      }
      provinceSelect.items().reset(names);
      provinceSelect.setValue(names.indexOf(DEFAULT_PROVINCE) >= 0 ? DEFAULT_PROVINCE : names[0], false);
      loadDistricts();
    });
}

function baseFc() {
  var fc = District_Boundary.filter(ee.Filter.notNull([DISTRICT_FIELD]));
  if (PROVINCE_FIELD) {
    fc = fc.filter(ee.Filter.eq(PROVINCE_FIELD, provinceSelect.getValue()));
  }
  return fc;
}

function loadDistricts() {
  if (!DISTRICT_FIELD) { return; }
  districtSelect.items().reset(['Loading...']);
  districtSelect.setValue('Loading...', false);
  baseFc().aggregate_array(DISTRICT_FIELD).distinct().sort().evaluate(function (names) {
    districtSelect.items().reset(['All districts'].concat(names || []));
    districtSelect.setValue('All districts', false);
  });
}

function getRegion() {
  var d = districtSelect.getValue();
  var fc = baseFc();
  if (d && d !== 'All districts' && d !== 'Loading...') {
    fc = fc.filter(ee.Filter.eq(DISTRICT_FIELD, d));
  }
  return fc;
}


// ================================================================
// 6. MAIN ANALYSIS
// ================================================================
function runAnalysis() {
  if (!DISTRICT_FIELD) {
    statusLabel.setValue('Boundary columns not ready yet - wait a moment or check the Console.');
    return;
  }
  var year = parseInt(yearSelect.getValue(), 10);
  var season = seasonSelect.getValue();
  var useAdsi = modeSelect.getValue() === MAP_MODES[0];
  var regionFc = getRegion();
  var geom = regionFc.geometry();
  var r = seasonRange(year, season);
  var label = season + ' ' + year + (SEASONS[season].crosses ? '/' + String(year + 1).slice(2) : '');

  var warn = '';
  if (SEASONS[season].crosses && year >= 2026) {
    warn = ' WARNING: Rabi 2026/27 is not finished yet - results are partial.';
  }
  statusLabel.setValue('Building layers for ' + label + '...' + warn);
  statsPanel.clear();
  statsPanel.add(ui.Label('Computing statistics...', {color: '#637381'}));
  chartsPanel.clear();

  // ---- 6a. Sentinel-2 (10-20 m): season median NDVI / EVI / NDRE
  var s2 = S2.filterBounds(geom).filterDate(r.start, r.end)
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', CLOUD_MAX))
    .map(maskAndIndexS2).median();

  // ---- 6b. Vegetation: MODIS NDVI/EVI, anomaly, VCI
  var cur = modisSeason(year, season);
  var ndvi = cur.select('NDVI');
  var evi = cur.select('EVI');
  var bMean = baselineCollection(modisSeason, season).select('NDVI').mean();
  var anomaly = ndvi.subtract(bMean).rename('anomaly');
  var vci = condIdx(ndvi, modisSeason, season, 'NDVI', false, 'VCI');

  var lai = laiSeason(year, season);
  var gpp = gppSeason(year, season);

  // ---- 6c. Rainfall: total, % of normal, SPI
  var rainNow = rainSeason(year, season);
  var rainBase = baselineCollection(rainSeason, season).mean();
  var rainPct = rainNow.divide(rainBase).multiply(100)
    .updateMask(rainBase.gt(10)).rename('rain_pct');
  var rainMax = season.indexOf('Kharif') === 0 ? 600 : 200;
  var spi = spiSeason(year, season);

  // ---- 6d. Thermal: LST, TCI, VHI
  var lst = lstSeason(year, season);
  var tci = condIdx(lst, lstSeason, season, 'LST', true, 'TCI');
  var vhi = vci.multiply(0.5).add(tci.multiply(0.5)).rename('VHI');

  // ---- 6e. Soil moisture (SMCI) and evaporative stress (ESI)
  var sm = smSeason(year, season);
  var smci = condIdx(sm, smSeason, season, 'SM', false, 'SMCI');
  var esi = esiSeason(year, season);
  var esiMean = baselineCollection(esiSeason, season).select('ESI').mean();
  var esiAnom = esi.subtract(esiMean).rename('ESI_anom');

  // ---- 6f. ADSI: all components oriented so that 1 = most severe
  var vciS = ee.Image(1).subtract(vci.divide(100));
  var spiS = ee.Image(2).subtract(spi).divide(4).clamp(0, 1);   // SPI +2 -> 0, SPI -2 -> 1
  var tciS = ee.Image(1).subtract(tci.divide(100));
  var smciS = ee.Image(1).subtract(smci.divide(100));
  var wSum = W.vci + W.spi + W.tci + W.smci;
  var adsi = vciS.multiply(W.vci).add(spiS.multiply(W.spi))
    .add(tciS.multiply(W.tci)).add(smciS.multiply(W.smci))
    .divide(wSum).rename('ADSI');

  // ---- 6g. Classification
  var vciClass = ee.Image(0)
    .where(vci.gte(50), 1)
    .where(vci.gte(35).and(vci.lt(50)), 2)
    .where(vci.gte(20).and(vci.lt(35)), 3)
    .where(vci.gte(10).and(vci.lt(20)), 4)
    .where(vci.lt(10), 5)
    .updateMask(vci.gte(0)).rename('stress');

  var adsiClass = ee.Image(0)
    .where(adsi.lt(0.30), 1)
    .where(adsi.gte(0.30).and(adsi.lt(0.45)), 2)
    .where(adsi.gte(0.45).and(adsi.lt(0.60)), 3)
    .where(adsi.gte(0.60).and(adsi.lt(0.75)), 4)
    .where(adsi.gte(0.75), 5)
    .updateMask(adsi.gte(0)).rename('stress');

  var stress = useAdsi ? adsiClass : vciClass;
  var classNames = useAdsi ? ADSI_NAMES : VCI_NAMES;
  setLegend(useAdsi ? 'Agricultural drought severity (ADSI)' : 'Vegetation stress (VCI)', classNames);

  // ---- 6h. NDVI trend (Mann-Kendall + Sen's slope), baseline start -> selected year
  var trendCol = ee.ImageCollection(ee.List.sequence(BASE_START, year).map(function (y) {
    y = ee.Number(y);
    return ee.Image.constant(y).float().rename('year')
      .addBands(modisSeason(y, season).select('NDVI'));
  }));
  var sen = trendCol.reduce(ee.Reducer.sensSlope());
  var mk = trendCol.reduce(ee.Reducer.kendallsCorrelation());
  var trendSig = sen.select('slope').updateMask(mk.select('p-value').lt(0.05)).rename('NDVI_trend');

  // ---- 6i. Map layers
  function crop(img) { return cropOnly.getValue() ? img.updateMask(cropMask) : img; }
  var outline = ee.Image().byte().paint({featureCollection: regionFc, color: 1, width: 2});

  Map.layers().reset([]);
  Map.addLayer(crop(s2.select('NDVI')).clip(geom),
    {min: 0, max: 0.9, palette: NDVI_PAL}, 'Sentinel-2 NDVI (10 m)', false);
  Map.addLayer(crop(s2.select('EVI')).clip(geom),
    {min: 0, max: 0.9, palette: NDVI_PAL}, 'Sentinel-2 EVI (10 m)', false);
  Map.addLayer(crop(s2.select('NDRE')).clip(geom),
    {min: 0, max: 0.6, palette: NDVI_PAL}, 'Sentinel-2 NDRE (20 m)', false);
  Map.addLayer(crop(ndvi).clip(geom),
    {min: 0, max: 0.8, palette: NDVI_PAL}, 'MODIS NDVI (250 m)', false);
  Map.addLayer(crop(anomaly).clip(geom),
    {min: -0.2, max: 0.2, palette: ANOM_PAL}, 'NDVI anomaly vs ' + BASE_START + '-' + BASE_END, false);
  Map.addLayer(crop(lai).clip(geom),
    {min: 0, max: 4, palette: NDVI_PAL}, 'LAI (500 m)', false);
  Map.addLayer(crop(gpp).clip(geom),
    {min: 0, max: 800, palette: NDVI_PAL}, 'GPP (g C/m2/season)', false);
  Map.addLayer(crop(vci).clip(geom),
    {min: 0, max: 100, palette: COND_PAL}, 'VCI (0-100)', false);
  Map.addLayer(crop(rainNow).clip(geom),
    {min: 0, max: rainMax, palette: RAIN_PAL}, 'Rainfall total (mm)', false);
  Map.addLayer(crop(rainPct).clip(geom),
    {min: 0, max: 200, palette: PCT_PAL}, 'Rainfall % of normal', false);
  Map.addLayer(crop(spi).clip(geom),
    {min: -2.5, max: 2.5, palette: SPI_PAL}, 'SPI (seasonal, 6-month)', false);
  Map.addLayer(crop(lst).clip(geom),
    {min: 15, max: 50, palette: TEMP_PAL}, 'LST day (deg C)', false);
  Map.addLayer(crop(tci).clip(geom),
    {min: 0, max: 100, palette: COND_PAL}, 'TCI (0-100)', false);
  Map.addLayer(crop(vhi).clip(geom),
    {min: 0, max: 100, palette: COND_PAL}, 'VHI (0-100)', false);
  Map.addLayer(crop(smci).clip(geom),
    {min: 0, max: 100, palette: COND_PAL}, 'SMCI (0-100)', false);
  Map.addLayer(crop(esiAnom).clip(geom),
    {min: -0.2, max: 0.2, palette: ANOM_PAL}, 'ESI anomaly (ET/PET)', false);
  Map.addLayer(crop(trendSig).clip(geom),
    {min: -0.01, max: 0.01, palette: TREND_PAL}, 'NDVI trend, Sen slope/yr (p<0.05)', false);
  Map.addLayer(crop(adsi).clip(geom),
    {min: 0, max: 1, palette: STRESS_PAL}, 'ADSI continuous (0-1)', false);
  Map.addLayer(crop(stress).clip(geom),
    {min: 1, max: 5, palette: CLASS_COLORS},
    useAdsi ? 'Drought severity (ADSI classes)' : 'Vegetation stress (VCI classes)', true);
  Map.addLayer(outline, {palette: '000000'}, 'Boundary');
  Map.centerObject(geom);

  // ---- 6j. Statistics over cropland
  function rr(img, reducer, scale) {
    return img.reduceRegion({
      reducer: reducer, geometry: geom, scale: scale,
      maxPixels: 1e13, bestEffort: true, tileScale: 8
    });
  }
  var areaHa = ee.Image.pixelArea().divide(10000);

  var stats = ee.Dictionary({
    groups: rr(areaHa.addBands(stress).updateMask(cropMask),
      ee.Reducer.sum().group({groupField: 1, groupName: 'class'}), 250).get('groups'),
    totalHa: rr(areaHa.updateMask(cropMask), ee.Reducer.sum(), 250).get('area'),
    means: rr(ndvi.addBands([evi, vci, rainNow, rainPct, spi, lst, tci, vhi, smci, esi, lai, gpp, adsi])
      .updateMask(cropMask), ee.Reducer.mean(), 1000)
  });

  stats.evaluate(function (s, err) {
    statsPanel.clear();
    if (err || !s) {
      statsPanel.add(ui.Label('Statistics failed: ' + (err || 'no data') +
        '. Try a single district or a completed season.', {color: 'red'}));
      statusLabel.setValue('');
      return;
    }
    var m = s.means || {};
    var byClass = {};
    var monitored = 0;
    (s.groups || []).forEach(function (g) {
      byClass[g['class']] = g.sum;
      monitored += g.sum;
    });
    var stressedHa = (byClass[3] || 0) + (byClass[4] || 0) + (byClass[5] || 0);
    var stressedPct = monitored > 0 ? 100 * stressedHa / monitored : 0;

    statsPanel.add(ui.Label('Results: ' + label, {fontWeight: 'bold', fontSize: '15px', margin: '12px 0 4px 0'}));
    addRow('Cropland area', fmtInt(s.totalHa) + ' ha');
    addRow('Mean ADSI (0-1)', fmtNum(m.ADSI, 2));
    addRow('Mean NDVI / EVI', fmtNum(m.NDVI, 2) + ' / ' + fmtNum(m.EVI, 2));
    addRow('Mean VCI / TCI / VHI',
      fmtNum(m.VCI, 0) + ' / ' + fmtNum(m.TCI, 0) + ' / ' + fmtNum(m.VHI, 0));
    addRow('Mean SMCI', fmtNum(m.SMCI, 0) + ' / 100');
    addRow('Mean SPI', fmtNum(m.SPI, 2));
    addRow('Season rainfall', fmtNum(m.rain, 0) + ' mm (' + fmtNum(m.rain_pct, 0) + '% of normal)');
    addRow('Mean day LST', fmtNum(m.LST, 1) + ' C');
    addRow('Mean ESI (ET/PET)', fmtNum(m.ESI, 2));
    addRow('Mean LAI / GPP', fmtNum(m.LAI, 2) + ' / ' + fmtNum(m.GPP, 0) + ' gC/m2');
    addRow('Cropland under ' + (useAdsi ? 'drought' : 'stress') + ' (moderate or worse)',
      fmtInt(stressedHa) + ' ha (' + stressedPct.toFixed(1) + '%)', '#fdae61');
    statsPanel.add(ui.Label('Cropland area by class', {fontWeight: 'bold', margin: '10px 0 2px 0'}));
    for (var c = 1; c <= 5; c++) {
      var ha = byClass[c] || 0;
      var pct = monitored > 0 ? 100 * ha / monitored : 0;
      addRow(classNames[c - 1], fmtInt(ha) + ' ha (' + pct.toFixed(1) + '%)', CLASS_COLORS[c - 1]);
    }
    statusLabel.setValue('Done. Use the Layers menu (top right of the map) to switch layers.' + warn);
  });

  // ---- 6k. Chart A: district ranking by mean ADSI (cropland) - the "where is the stress" answer
  var ranked = adsi.updateMask(cropMask).reduceRegions({
    collection: baseFc(), reducer: ee.Reducer.mean(), scale: 1000, tileScale: 8
  }).filter(ee.Filter.notNull(['mean'])).sort('mean', false).limit(20);
  chartsPanel.add(ui.Chart.feature.byFeature(ranked, DISTRICT_FIELD, ['mean'])
    .setChartType('BarChart')
    .setOptions({
      title: 'Top 20 districts by mean ADSI (cropland) - ' + label,
      hAxis: {title: 'ADSI (0 = no stress, 1 = extreme)', viewWindow: {min: 0, max: 1}},
      legend: {position: 'none'}, colors: ['#d73027']
    }));

  // ---- 6l. Chart B: seasonal crop health since baseline start
  var yrs = ee.List.sequence(BASE_START, year);
  var seasonFc = ee.FeatureCollection(yrs.map(function (y) {
    var d = rr(modisSeason(y, season).updateMask(cropMask), ee.Reducer.mean(), 1000);
    return ee.Feature(null, {year: ee.Number(y).format('%d'), NDVI: d.get('NDVI'), EVI: d.get('EVI')});
  }));
  chartsPanel.add(ui.Chart.feature.byFeature(seasonFc, 'year', ['NDVI', 'EVI'])
    .setChartType('LineChart')
    .setOptions({
      title: 'Seasonal crop health - ' + season + ' (mean over cropland)',
      hAxis: {title: 'Season start year', slantedText: true},
      vAxis: {title: 'Index value'},
      lineWidth: 2, pointSize: 4, colors: ['#1a9850', '#2c7fb8']
    }));

  // ---- 6m. Chart C: monthly rainfall, selected year vs normal
  var monthNames = ee.List(['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']);
  var rainFc = ee.FeatureCollection(ee.List.sequence(1, 12).map(function (mo) {
    mo = ee.Number(mo);
    var ms = ee.Date.fromYMD(year, mo, 1);
    var obs = CHIRPS.filterDate(ms, ms.advance(1, 'month')).select('precipitation').sum();
    var clim = ee.ImageCollection(ee.List.sequence(BASE_START, BASE_END).map(function (by) {
      var bs = ee.Date.fromYMD(by, mo, 1);
      return CHIRPS.filterDate(bs, bs.advance(1, 'month')).select('precipitation').sum();
    })).mean();
    return ee.Feature(null, {
      month: monthNames.get(mo.subtract(1)),
      Observed: rr(obs, ee.Reducer.mean(), 5566).get('precipitation'),
      Normal: rr(clim, ee.Reducer.mean(), 5566).get('precipitation')
    });
  }));
  chartsPanel.add(ui.Chart.feature.byFeature(rainFc, 'month', ['Observed', 'Normal'])
    .setChartType('ComboChart')
    .setOptions({
      title: 'Monthly rainfall (mm) - ' + year + ' vs ' + BASE_START + '-' + BASE_END + ' normal',
      vAxis: {title: 'mm'},
      seriesType: 'bars',
      series: {0: {color: '#2c7fb8'}, 1: {type: 'line', color: '#d73027', lineWidth: 2}}
    }));
}