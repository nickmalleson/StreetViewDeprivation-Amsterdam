# Developer notes — StreetViewDeprivation-Amsterdam

A fully-open workflow predicting neighbourhood socio-economic status from open street-level panoramas
of Amsterdam. See `README.md` for the overview and `DATA_PROVENANCE.md` for data licences.

## Start here
The pipeline is built and was verified on a small development sample; it has **not** yet been run at
full scale, and `data/` is empty (everything regenerates). To run the real study:
1. `conda env create -f environment.yml && conda activate streetview-deprivation-amsterdam`
2. From `notebooks/`: `python panorama_acquisition.py` — builds the city-wide work list and downloads
   the panoramas (resumable; safe to Ctrl-C and rerun until complete). Tunables are constants at the
   top of the script (e.g. `TARGET_N = 5000`).
3. Run notebooks 2 → 8 in order. In notebook 5, pick `global_k` from the diagnostics and set it in
   `clustering_functions.py` (currently 5).
4. Notebooks 9a/9b (satellite branch) need Google Earth Engine auth + a manual GeoTIFF download.

## Design choices
- **Imagery**: City of Amsterdam Open Panorama (CC-BY 4.0). Acquired by the standalone
  `notebooks/panorama_acquisition.py`; notebook 1 is a thin driver + H5 sanity-check. Each
  equirectangular panorama → 4× 90° rectilinear crops (N/E/S/W) via `py360convert`. Raw
  equirectangulars are never embedded. No road-network sampling / no OSMnx.
- **Target**: CBS **SES-WOA** continuous score (StatLine 86092NED, year 2023). Report raw-score R²
  (the score is continuous, so there is no rank analysis).
- **Spatial unit**: CBS **buurt** (join key `buurtcode`, `BU########`).
- **CRS**: **EPSG:28992** for projected ops; WGS84 (4326) for point joins and Earth Engine / GeoTIFF.
- **Notebook 8** predicts the **3 SES-WOA components** (welvaart, opleidingsniveau, arbeidsverleden) —
  SES-WOA is a single composite with no separate domains, so there are no IMD-style domains to predict.
- **Satellite (9a/9b)**: AlphaEarth, AOI = Amsterdam, zonal units = buurten.

## Config (the single place the study is defined)
- `notebooks/directory_filepaths.py` — paths + `MUNICIPALITY_CODE`, `PROJECTED_CRS`, `JOIN_KEY`,
  `TARGET_COL`, `CBS_TABLE`, `CBS_YEAR`.
- `notebooks/clustering_functions.py` — `global_k`, `embedding_statistic='median'`, `RANDOM_STATE=42`,
  aggregation helpers.
- `notebooks/amsterdam_data.py` — `get_amsterdam_buurten()` (PDOK WijkBuurtkaart 2024 → GPKG cache)
  and `get_ses_woa()` (CBS OData → CSV cache).

## H5 data store (street_data.h5)
- `point_id` (int32, **== row index, no gaps**), `latitude`, `longitude`, `date` (`S10`, "YYYY-MM"),
  `image_paths` (N×4 `S512`), `images_present` (N×4 bool), `images_jpeg` (N×4 vlen uint8),
  `embeddings_clip` (N×4×512, added by notebook 2).
- Native keys: `pano_id` (`S64`), `mission_year` (int32).
- Root attrs: image source, licence (CC-BY 4.0), attribution, privacy, headings, fov_deg, img_size.

## Downloader (panorama_acquisition.py)
Resumable/idempotent: a transactional manifest (`panorama_manifest.csv`) of completed `pano_id`s +
atomic temp→rename writes. Gentle: serial, polite delay, exponential backoff on HTTP 429. All knobs are
constants at the top. `--bbox`/`--limit` for dev; no args = whole municipality. The very dense (~1M)
panorama set is thinned **density-proportionally** to `TARGET_N` (≈5000) points: a fixed share of the
panoramas is kept within each `STRATUM_M` (200 m) grid cell, so sample density tracks local road
density (denser areas get proportionally more points) rather than being a flat per-km² grid. A
`MIN_SPACING_M` (30 m) floor drops near-duplicates first to curb clumping, and `RANDOM_STATE` makes
the sampling reproducible. Set `TARGET_N = None` to keep the full ~1M-point set.

The work list (`panorama_worklist.parquet`) is stamped with the settings it was built from
(`panorama_worklist.parquet.meta.json`: target size, stratum, spacing floor, seed, tile size, AOI,
image variant, dev bbox) and **auto-rebuilds** whenever any of those change — so editing `TARGET_N`
(etc.) at the top of the script is sufficient; you don't need to remember `--rebuild-worklist` (which
remains as a manual override). Re-running with unchanged settings reuses the cached list. Re-thinning
never re-downloads existing points: completed `pano_id`s are skipped via the manifest, and any
panorama whose four crops are already on disk is skipped too — only the newly-introduced points are
fetched.

## Status
- Notebooks 1–8 verified end-to-end on a **development sample** (~323 panoramas, central Amsterdam).
  9a/9b ported and syntax-checked (cannot run without GEE auth + a manual GeoTIFF download).
- **Not yet run at full-municipality scale**; `global_k` is currently 5 (to be chosen from notebook 5's
  diagnostics on the full data).

## Things only the user can do
GEE auth + Cloud project and the manual AlphaEarth GeoTIFF download (9a); choose `global_k`.

## Conventions
- British English in prose/comments; Dutch retained in source column names / area codes.
- Don't delete commented-out reference cells unless asked.
- Verification runs use the env's jupyter and a registered `amsterdam-verify` kernel; the committed
  notebooks use a generic `python3` kernelspec.

## Method provenance
The approach was first developed for Greater Manchester in the INTEGRATE project; this repository keeps
the same notebook numbering and split parameters so the two cities are comparable.
