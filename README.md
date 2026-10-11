# Team Zenith image and SAR change prototype

This project combines the original Team Zenith image scripts with a small,
beginner-friendly command-line application for the NASA Space Apps Challenge
2026 **Dancing with the SARs** project.

## Requirements and Windows setup

Use Python 3.10 or newer. In PowerShell:

```powershell
cd C:\Users\user\Downloads\change
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process Bypass`
for the current terminal, or invoke `.venv\Scripts\python.exe` directly.

## Commands

All outputs go to `outputs\` by default. Inputs may be anywhere; `data\input\`
is provided as a convenient place to store them.

```powershell
py main.py view --image satellite.png
py main.py view --image satellite.png --grayscale --enhance-contrast
py main.py inspect --image satellite.png
py main.py compare --before before.png --after after.png
py main.py compare --before before.png --after after.png --resize-for-demo
py main.py sar-change --before before.tif --after after.tif --units linear --threshold-db 3
py main.py sar-change --before before_db.tif --after after_db.tif --units db
```

`compare` refuses different image dimensions unless
`--resize-for-demo` is explicitly selected. It produces a visual pixel
difference, but a PNG/JPEG screenshot is not a calibrated SAR measurement and
the result must not be described as confirmed flooding, erosion, or another
Earth-surface change.

## SAR input contract

`sar-change` accepts two single-band, calibrated and already co-registered
GeoTIFF layers. The inputs must have matching dimensions, CRS, resolution,
affine transform, and spatial bounds. The program also checks raster masks,
nodata, finite values, positive values for linear power, and selected
polarization/acquisition metadata when present.

Use `--units linear` only for suitable positive linear backscatter power
(for example, a prepared GCOV gamma-0 layer). The program converts it to dB as
`10*log10(after) - 10*log10(before)`. Use `--units db` when both inputs are
already in dB; they are subtracted directly. The default ±3 dB threshold is an
experimental heuristic, not a universal scientific threshold. The mask marks
candidate changes requiring interpretation and validation, not confirmed
events.

Each SAR run writes:

- `sar_difference_db.tif` — dB difference with georeferencing preserved.
- `potential_change_mask.tif` — `1` candidate, `0` valid non-candidate, `255` invalid.
- `sar_comparison_preview.png` — a visual review figure.
- `sar_processing_summary.json` — inputs, settings, counts, and warnings.

The application deliberately does **not** guess HDF5 dataset paths, units, or
polarizations and does not directly consume arbitrary NISAR HDF5 files. For
real NISAR work, inspect the product structure and metadata first, identify
the appropriate GCOV dataset and polarization, and export matching
analysis-ready GeoTIFF layers using documented tools. Useful references are:

- [NASA Space Apps: Dancing with the SARs](https://www.spaceappschallenge.org/2026/challenges/dancing-with-the-sars/)
- [ASF Vertex](https://vertex.daac.asf.alaska.edu/)
- [ASF NISAR Cookbook](https://github.com/ASFOpenSARlab/NISAR_Cookbook)
- [GCOV change-detection notebook](https://github.com/ASFOpenSARlab/NISAR_Cookbook/blob/main/notebooks/NISAR_GCOV_SARChangeDetectionMethods.ipynb)

Do not claim a real scientific result until calibration, acquisition
compatibility, co-registration, masking, threshold selection, and domain
interpretation have been independently reviewed.

## EarthChange AI website prototype

This repository also contains a static HTML/CSS/JavaScript prototype for the
EarthChange AI landing page and image-difference demo.

Run it locally with:

```powershell
cd C:\Users\user\Downloads\change
py -m http.server 8000
```

Then open `http://localhost:8000` in a browser. The site is designed for static
hosting and uses client-side image comparison logic only; there is no Python
backend required on GitHub Pages.

The frontend now includes a desktop-style GIS workspace in
[static/index.html](./static/index.html). It uses OpenLayers with an attributed
OpenStreetMap basemap, a layer manager, study-area controls, map navigation,
local GeoJSON loading, honest empty states for live NISAR observations, and a
client-side image comparison drawer. GeoTIFF/HDF5 scientific layers are not
silently treated as ordinary images: they require georeferenced processing or
an API/backend.

The [backend.py](./backend.py) service now provides real Earthdata metadata
search endpoints and a bounded streaming-preprocessing boundary. Complete
granule downloads are disabled.
When the backend is running, it serves only the files in `static/` as the
frontend. Project source files, `.env`, `.git`, and local data are not exposed
as web assets. Derived previews are served separately from `data/streamed/`.
The frontend refresh action calls the search endpoint and populates the actual
returned acquisition dates and product identifiers. A static-only server still
shows the honest disconnected state.

The [nisar_pipeline.py](./nisar_pipeline.py) module provides Earthdata metadata
search and path-discovery-first HDF5 inspection. HDF5 inspection is optional
because the installed Windows h5py wheel may be blocked by local native DLL
policy. Earthdata credentials must remain server-side or in a local `.env`/`.netrc`;
the browser never requests NASA passwords.

The current service endpoints are:

- `GET /api/health`
- `GET /api/earthdata/status`
- `GET /api/earthdata/collections`
- `POST /api/earthdata/observations/search`
- `POST /api/earthdata/archive/search?page=1&page_size=20`
- `GET /api/earthdata/timeline?collection=NISAR_L2_GCOV_PROVISIONAL_V1`
- `POST /api/earthdata/stream`
- `POST /api/earthdata/change`
- `POST /api/products/{product_id}/inspect`

Search performs metadata discovery only; it does not download multi-gigabyte
products. Streaming authenticates with `earthaccess.login(strategy="environment")` on
the first real streaming request, then uses `earthaccess.open()` and h5py
file-like access to read
only bounded `frequencyA` GCOV previews. It writes small PNG/JSON artifacts
under `data/streamed/`; no `.h5` file is saved. A run is limited to five
granules and reports catalog sizes before opening them. Credentials remain
server-side.

When exactly two observations from the same collection maturity are selected,
`/api/earthdata/change` reads bounded sampled power arrays from both remote
products, checks their sampled shapes, calculates a dB difference, applies the
configured candidate threshold, and writes a small derived PNG/JSON result.
The result deliberately reports candidate backscatter change rather than a
confirmed environmental cause. Until real coordinate and projection metadata
has been validated for the opened product, the result is a preview and is not
presented as a georeferenced map raster.

The GIS catalog supports collection, date-range, and bounding-box filters. The
archive endpoint exposes bounded pagination and labels responses as `partial`
when the provider result window is saturated; it never presents a bounded
search as a complete archive inventory. The timeline endpoint derives its
earliest/latest dates from catalog metadata and likewise reports incomplete
coverage when the configured query window is exhausted. This is important
because CMR/earthaccess result ordering and provider limits can change over
time. Acquisition dates are not publication or ingestion dates.

For full NISAR processing, the streaming service inspects actual HDF5 dataset
attributes and selects a bounded `frequencyA` HHHH/HVHV preview. Georeferenced
window export and aligned change rasters remain a later step. See the official
[ASF NISAR Cookbook](https://asfopensarlab.github.io/NISAR_Cookbook/),
[GCOV documentation](https://nisar-docs.asf.alaska.edu/gcov/), and
[earthaccess guidance](https://nisar-docs.asf.alaska.edu/earthaccess/).

### Run the GIS frontend and API

```powershell
cd C:\Users\user\Downloads\change
.\.venv\Scripts\python.exe -m uvicorn backend:app --reload --port 8010
```

Open `http://127.0.0.1:8010`. Use the refresh button in the Browser panel to
perform a real unauthenticated metadata search. Searching succeeded in the Windows development environment during validation
and returned a genuine GCOV granule. A live remote HDF5 stream still requires
Earthdata credentials and ASF access approval; no credentials are stored in
the repository.

For static-only hosting:

```powershell
py -m http.server 8000
```

The static frontend remains GitHub Pages-compatible, but live Earthdata search,
downloads, HDF5 inspection, and scientific raster processing require the
separate Python service.

## Tests

The tests use small synthetic PNG/JPEG and GeoTIFF fixtures plus mocked
Earthdata responses, not downloaded NISAR observations:

```powershell
python -m pytest -q
```

They cover image statistics, ordinary image comparison, synthetic linear-power
SAR changes, nodata/no-valid-pixel handling, incompatible geospatial grids,
Earthdata response normalization, input validation, service health, and safe
product inspection boundaries. HDF5-specific tests are skipped when the native
h5py DLL cannot load on Windows.

The streaming implementation is intentionally separate from browser code. The
browser requests derived artifacts from the backend; it never opens HDF5
directly. No full NISAR product is downloaded or retained by the application.

## Dashboard integration

The deployed dashboard at `https://teamzenith26.vercel.app/dashboard` is a
web page and cannot execute local Python scripts. The independent CLI is the
source of truth. The JSON summary and PNG/GeoTIFF outputs provide a safe export
contract for a future backend/API integration; no credentials or API keys are
required by this project.
