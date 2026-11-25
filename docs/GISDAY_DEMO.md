# GIS Day exhibition: demonstration workflow

Project identity: **AgroWatch Intelligence, from satellite observations to agricultural decision intelligence.**

## The day before

1. Run `python verify_datasets.py` and fix every `BAD` line.
2. Run the offline tests (`python -m unittest discover tests`).
3. Choose three demo scenarios and open each once so the 6-hour cache is warm (do it again in the morning):
   - A whole province, latest complete Kharif season.
   - One district inside it, same season.
   - The same district in the latest complete Rabi season.
4. Open every navigation view once for the scenario you will present, so each panel is cached.
5. Record a backup screen video of the full tour (you already have one) and keep screenshots of each view.
6. Charge the laptop, bring a phone hotspot, and print the poster and the one-page handout from the README.
7. Decide whether visitors may use exports. Leave `EXPORTS_ENABLED=false` for open use.

## Ten-minute guided tour

| Time | View | What to do | What to say |
|---|---|---|---|
| 0:00 | Overview | Province, latest complete Kharif, cropland only. Read the six KPI cards and the risk gauge | "Which farmland is under stress? Every number here comes from satellites, computed live on Google Earth Engine." |
| 1:30 | Crop Vegetation | Switch the dataset selector: Sentinel-2, then MODIS, then Landsat | "Same field, three sensors, 10 m, 30 m and 250 m. The finer the pixel, the more cloud gaps." |
| 2:30 | Crop Phenology | Show the season curve for the year against the 2001-2018 average | "Start, peak and end of season. These dates are approximate because the satellite composites cover 16 days." |
| 3:30 | Rainfall Monitoring | Point to SPI-1, SPI-3, SPI-6 and the cumulative deficit line | "SPI compares rainfall with 30 years. Below -1 means a dry spell. It is a proper gamma fit, not just a standardised number." |
| 4:30 | Soil Moisture | Show the root-zone chart and the water-stress map | "SMAP is a 9 km product. It shows regional moisture, not individual fields, so we never zoom it past its resolution." |
| 5:30 | Evapotranspiration | Show ET/PET and the joint stress map | "Where plants are both browner and transpiring less than normal, stress is more likely. In irrigated fields ET can stay high anyway." |
| 6:30 | Agricultural Stress | Show component scores, then change a weight and press Apply, then tick PCA | "Five signals become one score. You can set the weights or let the data decide with PCA. It is a decision-support index and it is not yet validated against yields." |
| 8:00 | Agricultural Stress | Show the hotspot layer from the layer panel | "Gi* finds clusters of stress, not just individual bad pixels." |
| 8:45 | District Analytics | Sort by ASI, click a district, download the CSV | "Cropland-based statistics for every district: which ones to look at first." |
| 9:30 | Methodology | Open the dialog, expand one indicator | "Every indicator lists its formula, dataset, resolution, reference period and limitations." |

Optional extras: show the Sentinel-1 VV change layer ("radar sees through cloud; a big drop can mean standing water but needs checking"), the custom region box (press Map view), and the custom season window.

## Talking points by audience

- **Students and academics:** formulas are in the methodology; the Random Forest module needs your own samples and reports honest accuracy; Mann-Kendall and Sen's slope are provided.
- **Farmers and agriculture staff:** the district table and the risk gauge; "this tells you where to look, then check on the ground".
- **Disaster management and policy:** area under stress in hectares, hotspots, and the cumulative rainfall deficit.
- **Industry and insurers:** repeatable like-for-like seasonal comparison; mention that validation against loss data is the next step.

## Honest answers to likely questions

- *Is it accurate?* The inputs are standard satellite products. The composite ASI is not yet validated against crop-loss data, and we say so in the app.
- *Why is soil moisture so coarse?* SMAP Level 4 is about 9 km. We do not downscale it and imply finer measurements.
- *Can it predict yield?* No. It monitors condition. Yield needs ground yield records and a model.
- *Why did it take a minute?* Earth Engine is computing 25 years of satellite data for you. Repeats are cached.
- *Is the current season complete?* If not, a banner says the comparison uses the same shortened window in every year.
- *Why is a layer empty?* Cloud, a missing baseline (some layers need 5 earlier seasons) or no cropland. The legend panel lists the no-data rule for each layer.

## If something goes wrong

- Slow or blank map: switch to a single district, or play the backup video.
- Earth Engine error toast: read it aloud as a feature ("the system tells you what it could not compute"), switch view.
- No internet: use the screenshots and the video; the methodology dialog still works offline only if the server is running locally.

## Poster checklist

Title, the question, the data flow (Sentinel-1/2, Landsat, MODIS, CHIRPS, ERA5-Land, SMAP, WorldCover, Dynamic World to Earth Engine to ten modules), the ASI formula strip with five classes, two screenshots (Overview and Agricultural Stress), one line on limitations ("decision support, validation pending"), contact details and a QR code.
