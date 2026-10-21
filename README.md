# Oil Monitor

A daily, free, automated read on physical oil flows, built from Copernicus
Sentinel-1 radar, IMF PortWatch, free AIS and EIA data, written to a Google Sheet.

Runs on GitHub Actions at **10:30 UTC** (6:30 AM New York in summer, 5:30 AM in winter).

## What lands in the sheet

| Tab | What it is | Cadence |
|---|---|---|
| `Summary` | One row per location, latest numbers, plain-English **flags** | daily |
| `Chokepoints_Daily` | PortWatch transits per chokepoint (total, tankers, 7d/30d averages, 7d-vs-30d %) | IMF updates weekly (Tue) |
| `SAR_Detections` | One row per radar scene: vessels, large vessels (>150 m), persistent objects, AIS matches, dark candidates, z-score vs last 10 same-direction scenes | when a satellite passes (every 1-6 days per box) |
| `Dark_Candidates` | Radar targets with no AIS match nearby: position, est. length, confidence, crop image name | per scene |
| `Storage_Index` | Tank-farm radar index (bright_frac, p95_db), z-score vs same viewing geometry | per scene |
| `Storage_Calibration` | Does the Cushing index track EIA's weekly stocks? Verdict per geometry | weekly |
| `EIA_Cushing` | EIA weekly Cushing crude stocks | weekly |
| `Upcoming_Passes` | Predicted Sentinel-1 passes for the next 36 h | daily |
| `Run_Log` | What ran, what failed | daily |

Quicklook images (whole box, ships circled: blue = AIS match, red = dark
candidate, grey = persistent) and crops of each dark candidate are saved as a
downloadable artifact on every run (Actions tab > a run > Artifacts).

## How to read it honestly

- **Radar counts are snapshots**, not 24 h transit totals. Compare a box only
  with its own history (the z-score does that).
- **Dark candidates are candidates.** Free AIS is shore-based and patchy.
  `high` confidence means nearby ships matched AIS fine but this one didn't.
  `no_ais_coverage` means no recording existed for that pass.
- **Persistent objects** are mostly platforms and buoys, but at Qingdao,
  Ningbo or Fujairah a long-anchored tanker is persistent too, which can be
  floating storage.
- **Storage is uncalibrated until `Storage_Calibration` says `usable`.** At
  10 m resolution this is an experiment. If Cushing never tracks EIA, ignore
  the storage tabs.
- **AIS-based numbers undercount in conflict zones.** Since March 2026
  Hormuz has been largely shut to non-Iran-linked shipping, with reports of
  transponders switched off and positions spoofed. PortWatch (built on AIS)
  can't see dark traffic; the radar counts can. Spoofing also means AIS
  matches near Hormuz are less reliable, so expect more `low` confidence
  dark candidates there.
- **The 1-year baseline erodes during a long disruption.** As months of
  closure enter the 365-day window, "vs 1-year" shrinks even if nothing
  improves. Compare against the same months of 2025 in `Chokepoints_Daily`
  for the pre-conflict level.
- **Boxes are hand-drawn.** Check the quicklooks after the first runs and
  adjust `config.py` if a box sits wrong.

## Setup (one time)

1. **Copernicus** (satellite imagery, free): register at
   <https://dataspace.copernicus.eu>, then open the Sentinel Hub dashboard
   <https://shapps.dataspace.copernicus.eu/dashboard/> > User settings >
   OAuth clients > **Create**. Copy the client ID and secret (the secret is
   shown once).
2. **AISstream** (ship transponders, free): <https://aisstream.io> > sign in
   with GitHub > API Keys > create one.
3. **Google service account** (lets the robot write to your sheet):
   1. <https://console.cloud.google.com> > create project `oil-monitor`.
   2. APIs & Services > Library > enable **Google Sheets API**.
   3. IAM & Admin > Service Accounts > Create > any name > Done.
   4. Click it > Keys > Add key > JSON. A file downloads.
   5. Open the Google Sheet, Share it with the service account's email
      (`...@...iam.gserviceaccount.com`) as **Editor**.
4. **GitHub secrets**: repo > Settings > Secrets and variables > Actions >
   New repository secret, one each:
   `CDSE_CLIENT_ID`, `CDSE_CLIENT_SECRET`, `AISSTREAM_API_KEY`,
   `SHEET_ID` (the long id in the sheet's URL between `/d/` and `/edit`),
   `GOOGLE_SERVICE_ACCOUNT_JSON` (paste the whole JSON file).
5. **Turn on AIS recording**: same page > Variables tab > `AIS_ENABLED` = `true`.
   Only on a public repo (see the cost note in `.github/workflows/ais_window.yml`).
6. Actions tab > `daily` > **Run workflow** to test immediately.

Anything not configured is skipped and noted in `Run_Log`, so you can add
the accounts one at a time.

## Run on your own computer

```
pip install -r requirements.txt
python -m pytest -q                         # 34 tests, no internet needed
python run_daily.py --only portwatch,eia    # needs internet; writes output/sheet/*.csv
```

## Code map and lessons

| File | Job | Lesson inside |
|---|---|---|
| `config.py` | every box + tuning knobs | dataclasses, coordinate order |
| `monitor/portwatch.py` | Phase 1: IMF transits | APIs, paging, groupby + rolling |
| `monitor/copernicus.py` | find scenes, download pixels | OAuth tokens, evalscripts, 16-bit packing |
| `monitor/landmask.py` | sea vs land | broadcasting, distance transforms |
| `monitor/detect.py` | Phase 2: CFAR ship detector | vectorised box filters, blob labelling |
| `monitor/passes.py` | predict satellite passes | 12-day repeat orbits |
| `monitor/ais.py` | Phase 3: AIS + dark candidates | async websockets, dead reckoning, greedy matching |
| `monitor/storage.py` | Phase 4: tank farms vs EIA | interpolation, correlation as a test |
| `monitor/report.py` | Summary flags | z-scores |
| `monitor/sheets.py` | Google Sheet / CSV writer | classes, idempotent upserts |
| `run_daily.py` | the morning job | try/except per step, exit codes |
| `run_ais_window.py` | hourly AIS recorder | sleeping until an event |
| `tests/` | 34 tests incl. a full offline run | fixtures, monkeypatching, synthetic data |

Data credits: IMF PortWatch; Copernicus Sentinel data (ESA/EU); AISstream.io; U.S. EIA.
