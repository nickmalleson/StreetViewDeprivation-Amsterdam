# Developer notes — StreetViewDeprivation-Amsterdam

A fully-open workflow predicting neighbourhood socio-economic status from open street-level panoramas
of Amsterdam. See `README.md` for the overview and `DATA_PROVENANCE.md` for data licences.

## Start here
The pipeline is built and was verified on a small development sample; it has **not** yet been run at
full scale, and `data/` is empty (everything regenerates). To run the real study:
1. `conda env create -f environment.yml && conda activate streetview-deprivation-amsterdam`
2. From `notebooks/`: `python panorama_acquisition.py` — builds the city-wide work list and downloads
   the panoramas (resumable; safe to Ctrl-C and rerun until complete). Tunables are constants at the
   top of the script (e.g. `TARGET_N = 12000`, a region-wide total).
3. Run notebooks 2 → 8 in order. In notebook 5, pick `global_k` from the diagnostics and set it in
   `clustering_functions.py` (currently 5).
4. Notebooks 9a/9b (satellite branch) need Google Earth Engine auth + a manual GeoTIFF download.

## Design choices
- **Imagery**: City of Amsterdam Open Panorama (CC-BY 4.0). Acquired by the standalone
  `notebooks/panorama_acquisition.py`; notebook 1 is a thin driver + H5 sanity-check. Each
  equirectangular panorama → 4× 90° rectilinear crops (N/E/S/W) via `py360convert`. Raw
  equirectangulars are never embedded. No road-network sampling / no OSMnx. **The study region
  is the set of municipalities in `MUNICIPALITY_CODES` (a deliberate scoping choice, not a data
  limitation)**: the panorama source covers Amsterdam plus neighbouring regional municipalities
  (verified: Amstelveen `0362`, Diemen `0384`, Almere `0034` — but not separate cities like
  Utrecht or Haarlem), each of which also has a CBS SES-WOA score. The region currently includes
  all four (codes verified against CBS 86092NED + PDOK WijkBuurtkaart 2024); narrowing it back to
  Amsterdam alone is just `MUNICIPALITY_CODES = ["0363"]`. Each municipality is queried and clipped
  separately, but all are then **pooled and thinned together against one region-wide `TARGET_N`**, so
  sampling density is uniform across the region (see Downloader).
- **Target**: CBS **SES-WOA** continuous score (StatLine 86092NED, year 2023). Report raw-score R²
  (the score is continuous, so there is no rank analysis).
- **Spatial unit**: CBS **buurt** (join key `buurtcode`, `BU########`).
- **CRS**: **EPSG:28992** for projected ops; WGS84 (4326) for point joins and Earth Engine / GeoTIFF.
- **Notebook 8** predicts the **3 SES-WOA components** (welvaart, opleidingsniveau, arbeidsverleden) —
  SES-WOA is a single composite with no separate domains, so there are no IMD-style domains to predict.
- **Satellite (9a/9b)**: AlphaEarth, AOI = Amsterdam, zonal units = buurten. 9a builds three
  per-buurt feature variants from the *same* raster: **whole-buurt zonal** (every 10 m pixel in the
  buurt — the original approach) and two **road-following** variants that re-aggregate the raster over
  the street-view sampling support (the panorama point locations from the H5, same point→buurt join as
  notebook 3): `road_point` (single pixel under each point; a weak sanity baseline) and `road_buffer`
  (median of pixels within `ROAD_BUFFER_M`, default 20 m — the satellite analogue of the panorama's
  outward gaze). 9b fits/compares all three against the street-view baseline to test whether
  AlphaEarth's weaker fit is partly a *sampling* artefact (built on/around roads where people are)
  rather than a *sensor* one. The road variants are the only part of 9a that needs the panorama H5.

## Config (the single place the study is defined)
- `notebooks/directory_filepaths.py` — paths + `MUNICIPALITY_CODES` (the study-region gemeente
  set), `PROJECTED_CRS`, `JOIN_KEY`, `TARGET_COL`, `CBS_TABLE`, `CBS_YEAR`.
- `notebooks/clustering_functions.py` — `global_k`, `embedding_statistic='median'`, `RANDOM_STATE=42`,
  aggregation helpers.
- `notebooks/amsterdam_data.py` — `get_amsterdam_buurten()` (PDOK WijkBuurtkaart 2024 → GPKG cache)
  and `get_ses_woa()` (CBS OData → CSV cache). Both now span every gemeente in `MUNICIPALITY_CODES`.
  **These caches are NOT auto-invalidated when `MUNICIPALITY_CODES` changes** — after editing the
  set, refresh them once with `get_amsterdam_buurten(force=True)` / `get_ses_woa(force=True)` (or
  delete the cached `.gpkg`/`.csv`) so the new scope takes effect; the worklist then auto-rebuilds.

## H5 data store (street_data.h5)
- `point_id` (int32, **== row index, no gaps**), `latitude`, `longitude`, `date` (`S10`, "YYYY-MM"),
  `image_paths` (N×4 `S512`), `images_present` (N×4 bool), `images_jpeg` (N×4 vlen uint8),
  `embeddings_clip` (N×4×512, added by notebook 2).
- Native keys: `pano_id` (`S64`), `mission_year` (int32).
- Root attrs: image source, licence (CC-BY 4.0), attribution, privacy, headings, fov_deg, img_size.

## Downloader (panorama_acquisition.py)
Resumable/idempotent: a transactional manifest (`panorama_manifest.csv`) of completed `pano_id`s +
atomic temp→rename writes. Gentle: serial, polite delay, exponential backoff on HTTP 429. All knobs are
constants at the top.

**Withdrawn imagery (2023):** the City withdrew the entire 2023 campaign's image blobs from storage
(404 at every resolution) while leaving the API records live. Since `newest_in_range=true` returns the
newest mission per location, and 2023 is the newest across **Almere** (only — Amsterdam/Amstelveen/Diemen
have 2024/25), this silently lost ~72% of Almere. `_query_tile` now detects a withdrawn newest year
(`UNAVAILABLE_MISSION_YEARS = {2023}`) and re-queries that tile for all missions, keeping the newest one
that still has imagery (Almere → 2017-2020). Tiles with intact newest imagery never pay the extra query;
recovered Almere imagery is older than Amsterdam's 2025 (a documented temporal mismatch). `--bbox`/`--limit` for dev; no args = whole study region (every gemeente in
`MUNICIPALITY_CODES`, subject to the scored-buurten restriction below). By default
(`RESTRICT_TO_SCORED_BUURTEN = True`) the work list is **clipped to the buurten that have a CBS
SES-WOA score** (≈646 of 839 across the four municipalities; Amsterdam alone ≈420 of 517): the
unscored non-residential buurten (ports, industrial estates, parks, rail yards) can never
contribute a (target, imagery) training pair, so panoramas there are not downloaded. The scored set
comes from `get_ses_woa()` (a standalone CBS-OData fetch with no dependency on the imagery, already
cached), so there is no ordering problem. `--all-buurten` fetches the whole region regardless.

**Each municipality is queried and clipped separately** (a per-gemeente loop in `build_worklist`)
but the results are **pooled and thinned together once**, so `TARGET_N` is a **single region-wide**
budget and sampling density is uniform across the region (no gemeente is sampled more heavily per km²
than another). Trade-off: because the global per-cell share depends on the region-wide total, adding
or removing a municipality re-thins the whole region, so an existing gemeente's selected points can
change — re-running the downloader then fetches the newly-selected points and leaves the now-
deselected crops unused on disk. The very dense panorama set is thinned **density-proportionally** to
~`TARGET_N` points region-wide: a fixed share of the panoramas is kept within each `STRATUM_M` (200 m)
grid cell, so sample density tracks local road density (denser areas get proportionally more points)
rather than being a flat per-km² grid. A `MIN_SPACING_M` (30 m) floor drops near-duplicates first to curb clumping. After the
density sample, **`MIN_PER_BUURT` (10) guarantees a floor of that many panoramas per scored buurt**
where available — an *additive* top-up (points are spatially assigned to buurten and under-filled
buurten draw extra from their own leftovers) that keeps the selection a superset, so small / low-
road-density buurten get a stable per-buurt median embedding. `RANDOM_STATE` makes both steps
reproducible. Set `TARGET_N = None` to keep the full point set; `MIN_PER_BUURT = None` to disable the
floor.

The work list (`panorama_worklist.parquet`) is stamped with the settings it was built from
(`panorama_worklist.parquet.meta.json`: target size, stratum, spacing floor, per-buurt floor, seed,
tile size, the `MUNICIPALITY_CODES` set, image variant, dev bbox, the scored-buurten restriction
(and, when it is on, the CBS table/year that defines the scored set), **and `WORKLIST_LOGIC_VERSION`**
— a hand-bumped version of the build code
itself) and **auto-rebuilds** whenever any of those change — so editing `TARGET_N` (etc.) at the top
of the script, or fixing the sampling logic and bumping `WORKLIST_LOGIC_VERSION`, is sufficient; you
don't need to remember `--rebuild-worklist`. The manual flag is now only for picking up changed
*upstream data* under an unchanged config (new panoramas published, or a refreshed boundary/SES
cache under the same CBS table/year), which the stamp cannot detect; the "loading cached work list"
message says so each run. Re-running
with unchanged settings reuses the cached list. Re-thinning
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
- **If you change the sampling/acquisition approach** (`panorama_acquisition.py` — querying, the
  withdrawn-imagery fallback, the thinning/floors, `TARGET_N`/`MIN_PER_BUURT`/`STRATUM_M` semantics,
  etc.), also update the prose description in **notebook 1** (`1-SampleStreetNetwork.ipynb`, the
  "What the acquisition script does" / "If a panorama isn't available" cell) so the two stay in sync.
  Describe the behaviour and refer to the constants **by name** — do not bake in their numeric values,
  which change.
- Don't delete commented-out reference cells unless asked.
- Verification runs use the env's jupyter and a registered `amsterdam-verify` kernel; the committed
  notebooks use a generic `python3` kernelspec.

## Method provenance
The approach was first developed for Greater Manchester in the INTEGRATE project; this repository keeps
the same notebook numbering and split parameters so the two cities are comparable.
