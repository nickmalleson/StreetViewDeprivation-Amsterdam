#!/usr/bin/env python
"""
Resumable acquisition of City-of-Amsterdam open panoramas, reprojected into four
perspective (N/E/S/W) crops, written into the HDF5 structure that the rest of the
pipeline (notebooks 2+) expects.

The imagery is the "Kernregistratie panoramabeelden" published by Gemeente
Amsterdam (CC-BY 4.0; faces and number plates blurred at source), served from
api.data.amsterdam.nl.

Pipeline
--------
1. build_worklist : query the panorama REST API over a grid of small bbox tiles
                    with `newest_in_range=true` (= most recent mission per
                    location), clip to the Amsterdam municipality, thin
                    density-proportionally to ~TARGET_N points, and persist the
                    canonical work list.
2. download_all   : for each work-list point NOT already completed, download the
                    equirectangular image and reproject it into four 90-deg FOV
                    rectilinear crops (headings 0/90/180/270 = N/E/S/W). Crops are
                    cached on disk; completion is recorded in a manifest.
3. build_h5       : assemble the on-disk crops into the HDF5 store (the pipeline's
                    image schema + provenance attributes), with point_id == row index.

Resumability / politeness
-------------------------
* Fully resumable & idempotent: a manifest of completed pano_ids is appended
  transactionally only after all four crops are written with atomic temp->rename,
  so an interrupted run never leaves a point marked complete with a partial image.
  Re-running downloads only the remainder; nothing is ever fetched twice.
* Gentle on the server: serial requests by default, a polite delay between
  requests, exponential backoff with jitter on transient errors / HTTP 429
  (honouring Retry-After), and an academic-research User-Agent.

All of the knobs are the constants at the top of this file.

Typical use
-----------
  # small bounding box for development (lon/lat order):
  python panorama_acquisition.py --bbox 4.890 52.368 4.900 52.374
  # whole municipality (safe to Ctrl-C and rerun until complete):
  python panorama_acquisition.py
"""

import os
import io
import csv
import sys
import json
import time
import math
import random
import argparse

import numpy as np
import pandas as pd
import requests
import geopandas as gpd
from shapely.geometry import Point, box
from shapely.prepared import prep
from PIL import Image
import py360convert
from tqdm import tqdm

from directory_filepaths import (
    raw_dir, h5_filename, GEOGRAPHIC_CRS, PROJECTED_CRS, MUNICIPALITY_CODE,
)
from amsterdam_data import get_amsterdam_buurten, USER_AGENT

# =============================================================================
# CONSTANTS  (the single, easily-found place to tune behaviour)
# =============================================================================
# --- politeness / robustness ---
REQUEST_DELAY_S = 0.5     # polite pause between successive HTTP requests
MAX_CONCURRENCY = 1       # serial by default; keep low to be gentle on the server
MAX_RETRIES     = 6       # attempts per request before giving up
BACKOFF_BASE_S  = 2.0     # exponential backoff base (BACKOFF_BASE_S ** attempt)
BACKOFF_CAP_S   = 120.0   # maximum single backoff wait
HTTP_TIMEOUT_S  = 60      # per-request timeout

# --- imagery geometry: four 90-deg FOV rectilinear crops per point (N/E/S/W) ---
IMG_VARIANT = "equirectangular_medium"   # 4000x2000 source (~1 MB); also _full (8000), _small (2000)
IMG_SIZE    = 640                        # output crop size in px (640x640; a standard street-view tile size)
FOV_DEG     = 90                         # field of view per crop
HEADINGS    = [0, 90, 180, 270]          # compass bearings -> N, E, S, W (slot order)
JPEG_QUALITY = 90

# --- work-list construction ---
# Tile area must stay under the API's hard 0.25 km^2 (250,000 m^2) cap on
# newest_in_range queries. 480 m -> 230,400 m^2 keeps ~8% headroom for the slack
# introduced when each projected square is converted to its WGS84 lat/lon bbox
# (don't push to 500 m: 250,000 m^2 == the cap, with no margin, so it will error).
TILE_M = 480.0           # bbox tile size in metres; <0.25 km^2 cap for newest_in_range
RANDOM_STATE = 42        # seed for the (reproducible) density-proportional sampling

# Density-proportional thinning of the (very dense) panorama points.
# Amsterdam's open panoramas sit every few metres (~1M points for the municipality),
# which is impractical to download in full and far finer than the neighbourhood scale of
# the analysis. Rather than an even spatial grid (constant points per km^2, which under-
# samples densely-roaded areas), we keep a fixed *share* of the panoramas within each
# STRATUM_M grid cell. Because panoramas trace the road network, this makes the sample
# density track local road density -- denser, more built-up areas get proportionally more
# points -- while every populated cell keeps at least one. TARGET_N sets the overall
# budget and the per-cell share is derived from it (TARGET_N / available points); the
# min-one-per-cell rule means the realised count can run slightly above TARGET_N. Set
# TARGET_N to None to keep every newest-per-location panorama (the full ~1M-point set).
TARGET_N      = 5000     # approximate total number of points to sample city-wide
STRATUM_M     = 200.0    # grid cell size the proportional share is applied over
MIN_SPACING_M = 30.0     # anti-clump floor: drop near-duplicates before sampling; None to disable

# --- endpoints ---
PANO_API = "https://api.data.amsterdam.nl/panorama/panoramas/"

# --- provenance (written into the HDF5 root attributes) ---
IMAGE_SOURCE = "City of Amsterdam Open Panorama (Kernregistratie panoramabeelden)"
IMAGE_LICENCE = "CC-BY 4.0"
IMAGE_ATTRIBUTION = "Contains data from Gemeente Amsterdam, licensed under CC-BY 4.0"
IMAGE_PRIVACY = "faces and number plates blurred at source"

# --- local paths ---
WORKLIST_PATH = os.path.join(raw_dir, "panorama_worklist.parquet")
WORKLIST_META = WORKLIST_PATH + ".meta.json"   # records the settings the work list was built with
MANIFEST_PATH = os.path.join(raw_dir, "panorama_manifest.csv")
IMAGES_DIR    = os.path.join(raw_dir, "panorama_images")  # crops cache: <pano_id>/h{heading}.jpg


# =============================================================================
# Logging  (narration on by default; --quiet restores the original terse output)
# =============================================================================
# The script narrates *what* it is doing and *why* as it goes, so the process is
# self-documenting when you (or a future reader) come back to the code. All of that
# explanatory output goes through vprint() and is suppressed by --quiet, which leaves
# only the essential progress prints — i.e. the behaviour this script had before the
# narration was added. The plain print() calls below are deliberately kept outside
# vprint() so they show in both modes.
VERBOSE = True   # flipped to False by --quiet in main(); governs the narration only


def vprint(*args, **kwargs):
    """Print explanatory narration — silenced by --quiet (see VERBOSE)."""
    if VERBOSE:
        print(*args, **kwargs)


# =============================================================================
# HTTP helpers
# =============================================================================
def make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


class PanoramaUnavailable(Exception):
    """A panorama is permanently unavailable (an HTTP 4xx such as 404 'blob does not
    exist'). Retrying cannot help — the image will not appear later — so we raise this
    immediately instead of backing off, and the caller skips the point."""


def request_with_backoff(session, url, params=None, stream=False):
    """GET with exponential backoff + jitter on *transient* failures (HTTP 429 and 5xx,
    connection errors, timeouts). A permanent 4xx client error (e.g. 404) is not retried:
    it raises PanoramaUnavailable straight away."""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, params=params, stream=stream, timeout=HTTP_TIMEOUT_S)
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if (retry_after and retry_after.isdigit()) \
                    else min(BACKOFF_CAP_S, BACKOFF_BASE_S ** (attempt + 1))
                vprint(f"    [HTTP 429] server is rate-limiting us; honouring backoff and "
                       f"sleeping {wait:.1f}s before retry {attempt + 2}/{MAX_RETRIES}.")
                time.sleep(wait + random.uniform(0, 1))
                continue
            if resp.status_code >= 500:
                resp.close()
                raise requests.HTTPError(f"server {resp.status_code}")
            if 400 <= resp.status_code < 500:
                # Client error (404 missing blob, 403, 410, ...) — permanent. Don't waste
                # retries/backoff on something that will never succeed; fail fast and skip.
                reason = f"{resp.status_code} {resp.reason}"
                resp.close()
                raise PanoramaUnavailable(f"{reason} for {url}")
            resp.raise_for_status()
            return resp
        except (requests.RequestException, requests.HTTPError) as exc:
            last_exc = exc
            wait = min(BACKOFF_CAP_S, BACKOFF_BASE_S ** (attempt + 1)) + random.uniform(0, 1)
            vprint(f"    [transient error] {exc}; backing off {wait:.1f}s then retrying "
                   f"(attempt {attempt + 2}/{MAX_RETRIES}).")
            time.sleep(wait)
    raise RuntimeError(f"GET failed after {MAX_RETRIES} attempts: {url} ({last_exc})")


# =============================================================================
# Step 1 — build the canonical work list
# =============================================================================
def _tiles_over_bounds(bounds_proj, tile_m):
    """Yield (minx, miny, maxx, maxy) tiles in the projected CRS covering bounds."""
    minx, miny, maxx, maxy = bounds_proj
    nx = max(1, math.ceil((maxx - minx) / tile_m))
    ny = max(1, math.ceil((maxy - miny) / tile_m))
    for ix in range(nx):
        for iy in range(ny):
            yield (minx + ix * tile_m, miny + iy * tile_m,
                   min(maxx, minx + (ix + 1) * tile_m), min(maxy, miny + (iy + 1) * tile_m))


def _query_tile(session, bbox_latlon):
    """Return list of newest-in-range panoramas for one (minlon,minlat,maxlon,maxlat) bbox."""
    min_lon, min_lat, max_lon, max_lat = bbox_latlon
    params = {
        "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "srid": "4326", "newest_in_range": "true", "page_size": "2000",
    }
    out, url = [], PANO_API
    while url:
        resp = request_with_backoff(session, url, params=params)
        data = resp.json()
        if isinstance(data, list):          # API returns a list only on validation error
            raise RuntimeError(f"panorama API error for bbox {bbox_latlon}: {data}")
        for p in data["_embedded"]["panoramas"]:
            lon, lat = p["geometry"]["coordinates"][0], p["geometry"]["coordinates"][1]
            out.append({
                "pano_id": p["pano_id"], "lon": lon, "lat": lat,
                "mission_year": int(p["mission_year"]), "timestamp": p["timestamp"],
                "heading": float(p.get("heading", 0.0) or 0.0),
                "url": p["_links"][IMG_VARIANT]["href"],
            })
        url = (data["_links"].get("next") or {}).get("href")
        params = None
        time.sleep(REQUEST_DELAY_S)
    return out


def _thin_to_spacing(gdf_proj, spacing_m):
    """Keep at most one point per spacing_m grid cell (the one nearest the cell centre)."""
    gx = np.floor(gdf_proj.geometry.x / spacing_m).astype(int)
    gy = np.floor(gdf_proj.geometry.y / spacing_m).astype(int)
    cx = (gx + 0.5) * spacing_m
    cy = (gy + 0.5) * spacing_m
    d2 = (gdf_proj.geometry.x - cx) ** 2 + (gdf_proj.geometry.y - cy) ** 2
    tmp = gdf_proj.assign(_cell=list(zip(gx, gy)), _d2=d2)
    keep = tmp.sort_values("_d2").drop_duplicates("_cell").index
    return gdf_proj.loc[keep].reset_index(drop=True)


def _thin_to_target(gdf_proj, target_n, stratum_m, min_spacing_m=None,
                    random_state=RANDOM_STATE):
    """Density-proportional thinning to ~target_n points.

    Keep a fixed share of the panoramas within every stratum_m grid cell, so denser
    areas (more road, hence more panoramas) keep proportionally more points while every
    populated cell keeps at least one. The share is target_n / (points available after
    the optional min_spacing_m floor); the min-one-per-cell rule means the realised
    count can run a little above target_n where many cells are sparse.

    target_n=None keeps everything (subject only to the floor).
    """
    # The min-spacing floor resets the index, so all label-based selection below must be
    # against `g` (the floored frame), NOT the original gdf_proj: their labels no longer
    # correspond. (Selecting from gdf_proj here silently returned the lowest-indexed
    # original rows — i.e. the points collected first, which, since tiles are queried
    # west->east, biased the whole sample to the west of the city.)
    g = (_thin_to_spacing(gdf_proj, min_spacing_m) if min_spacing_m else gdf_proj
         ).reset_index(drop=True)
    if not target_n or target_n >= len(g):
        return g

    fraction = target_n / len(g)
    gx = np.floor(g.geometry.x / stratum_m).astype(int)
    gy = np.floor(g.geometry.y / stratum_m).astype(int)
    g = g.assign(_cell=list(zip(gx, gy)))
    rng = np.random.default_rng(random_state)
    keep = []
    for _, grp in g.groupby("_cell", sort=False):
        n_keep = max(1, round(len(grp) * fraction))
        if n_keep >= len(grp):
            keep.extend(grp.index)
        else:
            keep.extend(rng.choice(grp.index.to_numpy(), size=n_keep, replace=False))
    return g.loc[keep].drop(columns="_cell").reset_index(drop=True)


def _worklist_settings(dev_bbox):
    """The settings that determine the work list. If any of these change, the cached
    work list is stale and must be rebuilt (handled automatically in build_worklist)."""
    return {
        "TARGET_N": TARGET_N,
        "STRATUM_M": STRATUM_M,
        "MIN_SPACING_M": MIN_SPACING_M,
        "RANDOM_STATE": RANDOM_STATE,
        "TILE_M": TILE_M,
        "MUNICIPALITY_CODE": MUNICIPALITY_CODE,
        "IMG_VARIANT": IMG_VARIANT,            # the stored image URL depends on this
        "dev_bbox": list(dev_bbox) if dev_bbox else None,
    }


def build_worklist(session, dev_bbox=None, force=False):
    """Build (and cache) the canonical work list of panoramas to download.

    The cache is reused only if it was built with the same settings (target size,
    stratum, spacing floor, seed, tile size, AOI, image variant, dev bbox). If any of
    those changed since the cache was written, it is rebuilt automatically — so editing
    e.g. TARGET_N at the top of this file is enough; you do not need to remember
    --rebuild-worklist.
    """
    vprint("\n" + "-" * 70)
    vprint("STEP 1/3 — build the work list (which panoramas to download)")
    vprint("-" * 70)
    vprint("The work list is the canonical set of points the rest of the run operates on.")
    vprint("It is cached to disk and only rebuilt when the settings that define it change,")
    vprint("so this step is usually instant on reruns.")

    current = _worklist_settings(dev_bbox)
    if os.path.exists(WORKLIST_PATH) and not force:
        cached = None
        if os.path.exists(WORKLIST_META):
            with open(WORKLIST_META) as f:
                cached = json.load(f)
        if cached == current:
            print(f"Work list exists and matches current settings — loading {WORKLIST_PATH}")
            return pd.read_parquet(WORKLIST_PATH)
        # Settings changed (or no stamp) -> rebuild and say why.
        if cached is None:
            print("Work list has no settings stamp — rebuilding to be safe.")
        else:
            changed = {k: (cached.get(k), current[k]) for k in current if cached.get(k) != current[k]}
            print("Work list settings changed since it was built — rebuilding automatically:")
            for k, (old, new) in changed.items():
                print(f"    {k}: {old} -> {new}")

    vprint("Loading the Amsterdam municipality boundary (CBS buurten, EPSG:28992) and")
    vprint("dissolving it into a single polygon to clip panoramas against.")
    buurten = get_amsterdam_buurten()                      # EPSG:28992
    boundary = buurten.geometry.union_all()                # municipality polygon
    boundary_ll = gpd.GeoSeries([boundary], crs=PROJECTED_CRS).to_crs(GEOGRAPHIC_CRS).iloc[0]

    if dev_bbox is not None:
        vprint(f"Dev mode: restricting to bbox {dev_bbox} (lon/lat), intersected with the")
        vprint("municipality — a small area for quick end-to-end testing.")
    else:
        vprint("Whole-municipality run: no --bbox given, so the full city is in scope.")

    if dev_bbox is not None:
        # dev_bbox is (minlon, minlat, maxlon, maxlat); intersect with municipality
        region_proj = (gpd.GeoSeries([box(*dev_bbox)], crs=GEOGRAPHIC_CRS)
                       .to_crs(PROJECTED_CRS).iloc[0]).intersection(boundary)
        clip_geom = boundary_ll.intersection(box(*dev_bbox))
    else:
        region_proj = boundary
        clip_geom = boundary_ll

    # Tiles cover the *rectangle* of the region's bounds, but Amsterdam's outline is
    # irregular and water-laced, so much of that rectangle is open water or neighbouring
    # municipalities. Skip any tile that doesn't actually intersect the (land-only)
    # boundary before querying it — the per-point clip below still runs, so this only
    # avoids wasted API calls, it never changes the result.
    vprint(f"The panorama API caps each 'newest_in_range' query at 0.25 km^2, so the area")
    vprint(f"is covered by a grid of {TILE_M:.0f} m tiles and queried tile by tile. Each query")
    vprint("asks for the newest mission per location, so we get one current panorama per spot.")
    region_prep = prep(region_proj)
    all_tiles = list(_tiles_over_bounds(region_proj.bounds, TILE_M))
    tiles = [t for t in all_tiles if region_prep.intersects(box(*t))]
    vprint(f"The bounding rectangle gives {len(all_tiles)} tiles, but Amsterdam's outline is")
    vprint("irregular and water-laced; tiles not touching land are dropped to avoid wasted")
    vprint("API calls (this never changes the result — points are clipped precisely below).")
    print(f"Querying {len(tiles)} tiles of {TILE_M:.0f} m "
          f"(skipped {len(all_tiles) - len(tiles)} empty tiles outside the municipality).")

    records, seen = [], set()
    for t in tqdm(tiles, desc="worklist tiles", unit="tile"):
        bbox_ll = (gpd.GeoSeries([box(*t)], crs=PROJECTED_CRS)
                   .to_crs(GEOGRAPHIC_CRS).iloc[0].bounds)  # (minlon,minlat,maxlon,maxlat)
        for rec in _query_tile(session, bbox_ll):
            if rec["pano_id"] not in seen:
                seen.add(rec["pano_id"])
                records.append(rec)

    vprint(f"Collected {len(records)} unique panoramas across all tiles (deduplicated by")
    vprint("pano_id, since tiles can return the same point near their shared edges).")
    df = pd.DataFrame.from_records(records)
    gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat),
                           crs=GEOGRAPHIC_CRS)

    # Clip precisely to the municipality (tiles overlap the bbox, not the polygon).
    vprint("Clipping points to the exact municipality polygon (tiles are rectangular, the")
    vprint("city is not), so panoramas in water or neighbouring municipalities are dropped.")
    prepared = prep(clip_geom)
    gdf = gdf[gdf.geometry.apply(prepared.covers)].reset_index(drop=True)
    print(f"{len(gdf)} newest-per-location panoramas inside the area.")

    if TARGET_N or MIN_SPACING_M:
        vprint("")
        vprint("Thinning the points. Amsterdam's panoramas sit every few metres (~1M city-")
        vprint("wide) — far finer than the neighbourhood scale of the analysis and too many")
        vprint("to download. Two passes reduce them sensibly:")
        if MIN_SPACING_M:
            vprint(f"  (a) anti-clump floor: drop near-duplicates closer than {MIN_SPACING_M:.0f} m,")
            vprint("      keeping the one nearest each grid-cell centre.")
        if TARGET_N:
            vprint(f"  (b) density-proportional sample to ~{TARGET_N} points: keep a fixed *share*")
            vprint(f"      of the points in every {STRATUM_M:.0f} m cell, so denser (more-roaded,")
            vprint("      more built-up) areas keep proportionally more points, while every")
            vprint(f"      populated cell keeps at least one (seed RANDOM_STATE={RANDOM_STATE}).")
        gdf_proj = gdf.to_crs(PROJECTED_CRS)
        gdf_proj = _thin_to_target(gdf_proj, TARGET_N, STRATUM_M,
                                   min_spacing_m=MIN_SPACING_M)
        gdf = gdf.loc[gdf["pano_id"].isin(gdf_proj["pano_id"])].reset_index(drop=True)
        print(f"Density-proportional thinning to ~{TARGET_N} points "
              f"(STRATUM_M={STRATUM_M:.0f} m, floor={MIN_SPACING_M} m): {len(gdf)} points.")

    worklist = gdf.drop(columns="geometry").reset_index(drop=True)
    os.makedirs(os.path.dirname(WORKLIST_PATH), exist_ok=True)
    worklist.to_parquet(WORKLIST_PATH, index=False)
    with open(WORKLIST_META, "w") as f:
        json.dump(current, f, indent=2)
    vprint("Stamping the work list with the settings it was built from (next to the parquet)")
    vprint("so a future run can tell whether it is still current or needs rebuilding.")
    print(f"Saved work list ({len(worklist)} points) to {WORKLIST_PATH}")
    return worklist


# =============================================================================
# Step 2 — download + reproject (resumable, atomic)
# =============================================================================
def _atomic_write_bytes(path, data):
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _crop_path(pano_id, heading):
    return os.path.join(IMAGES_DIR, pano_id, f"h{heading:03d}.jpg")


def _load_manifest():
    done = set()
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, newline="") as f:
            for row in csv.reader(f):
                if row and row[0] != "pano_id":   # skip header
                    done.add(row[0])
    return done


def _append_manifest(pano_id, mission_year, timestamp):
    """Transactionally record a completed point (append + flush + fsync)."""
    new = not os.path.exists(MANIFEST_PATH)
    with open(MANIFEST_PATH, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["pano_id", "mission_year", "timestamp"])
        w.writerow([pano_id, mission_year, timestamp])
        f.flush()
        os.fsync(f.fileno())


def download_and_crop(session, row):
    """Download one panorama and write its 4 perspective crops atomically.

    Returns True on success. Crops already on disk are not re-fetched.
    """
    pano_id = row["pano_id"]
    paths = {h: _crop_path(pano_id, h) for h in HEADINGS}
    if all(os.path.exists(p) for p in paths.values()):
        return True  # already cropped on a previous run

    os.makedirs(os.path.join(IMAGES_DIR, pano_id), exist_ok=True)
    resp = request_with_backoff(session, row["url"])
    equi = np.array(Image.open(io.BytesIO(resp.content)).convert("RGB"))
    time.sleep(REQUEST_DELAY_S)

    # Modern Amsterdam equirectangulars are north-up (heading=0); subtracting the
    # panorama's own heading makes the crops genuinely compass-aligned either way.
    pano_heading = float(row.get("heading", 0.0) or 0.0)
    for h, path in paths.items():
        if os.path.exists(path):
            continue
        u_deg = ((h - pano_heading + 180) % 360) - 180
        persp = py360convert.e2p(equi, fov_deg=FOV_DEG, u_deg=u_deg, v_deg=0,
                                 out_hw=(IMG_SIZE, IMG_SIZE))
        buf = io.BytesIO()
        Image.fromarray(persp.astype(np.uint8)).save(buf, format="JPEG", quality=JPEG_QUALITY)
        _atomic_write_bytes(path, buf.getvalue())
    return True


def download_all(session, worklist, limit=None):
    """Download every work-list point not already completed (resumable)."""
    vprint("\n" + "-" * 70)
    vprint("STEP 2/3 — download each panorama and reproject into 4 crops")
    vprint("-" * 70)
    vprint("For every point we fetch one equirectangular panorama and reproject it into four")
    vprint(f"{FOV_DEG}-deg rectilinear crops at headings {HEADINGS} (N/E/S/W), each {IMG_SIZE}x{IMG_SIZE} px.")
    vprint("Per point: one HTTP download, then four py360convert.e2p reprojections written")
    vprint("with atomic temp->rename so an interrupted write never leaves a half file.")
    vprint("Completed points are recorded in a manifest, so this whole step is resumable —")
    vprint("safe to Ctrl-C and rerun; nothing already done is fetched again.")

    os.makedirs(IMAGES_DIR, exist_ok=True)
    done = _load_manifest()
    vprint(f"Read the manifest: {len(done)} pano_id(s) already marked complete from prior runs.")
    todo = worklist[~worklist["pano_id"].isin(done)].reset_index(drop=True)
    if limit:
        vprint(f"--limit {limit}: capping this pass at the first {limit} outstanding point(s).")
        todo = todo.head(limit)
    print(f"{len(done)} already complete; {len(todo)} to download "
          f"(of {len(worklist)} total).")
    if len(todo) == 0:
        vprint("Nothing left to download — every work-list point is already complete.")

    for _, row in tqdm(todo.iterrows(), total=len(todo), desc="download", unit="pano"):
        try:
            if download_and_crop(session, row):
                _append_manifest(row["pano_id"], row["mission_year"], row["timestamp"])
        except KeyboardInterrupt:
            print("\nInterrupted — progress saved; rerun to continue.")
            raise
        except PanoramaUnavailable as exc:
            # Permanent: the image is simply not on the server. Skip it (no retry); it
            # stays out of the manifest, which is fine — there is nothing to download.
            tqdm.write(f"[SKIP] {row['pano_id']}: not available ({exc})")
        except Exception as exc:  # noqa: BLE001 - log and continue; rerun retries it
            tqdm.write(f"[WARN] {row['pano_id']}: {exc}")
    print("Download pass complete.")


# =============================================================================
# Step 3 — assemble the HDF5 store (image schema + provenance)
# =============================================================================
def build_h5(worklist, out_path=h5_filename):
    """Assemble completed on-disk crops into the HDF5 store used by notebooks 2+.

    Datasets: point_id, latitude, longitude, date, image_paths, images_present,
    images_jpeg (+ embeddings_clip added by notebook 2), plus pano_id and
    mission_year, with provenance in the root attributes. point_id == H5 row
    index (0..N-1, no gaps).
    """
    import h5py

    vprint("\n" + "-" * 70)
    vprint("STEP 3/3 — assemble the HDF5 store consumed by notebooks 2+")
    vprint("-" * 70)
    vprint("Packs the on-disk crops plus their metadata into one HDF5 file. The JPEG bytes")
    vprint("are embedded directly (images_jpeg), so the store is self-contained, and")
    vprint("point_id is set equal to the row index (0..N-1, no gaps) — an invariant the")
    vprint("downstream notebooks rely on. Licence/attribution/privacy go in the root attrs.")

    done = _load_manifest()
    wl = worklist[worklist["pano_id"].isin(done)].reset_index(drop=True)
    # Keep only points whose 4 crops are all present on disk.
    vprint("Including only points that are both in the manifest and have all four crops on")
    vprint("disk, so a partially-downloaded point can never make it into the store.")
    has_all = wl["pano_id"].apply(lambda pid: all(os.path.exists(_crop_path(pid, h))
                                                   for h in HEADINGS))
    wl = wl[has_all].reset_index(drop=True)
    N = len(wl)
    if N == 0:
        raise RuntimeError("No completed points found — run the download step first.")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    print(f"Building H5 for {N} points -> {out_path}")
    with h5py.File(out_path, "w") as f:
        # Provenance (drop-in for a paper's data statement; see DATA_PROVENANCE.md)
        f.attrs["image_source"] = IMAGE_SOURCE
        f.attrs["licence"] = IMAGE_LICENCE
        f.attrs["attribution"] = IMAGE_ATTRIBUTION
        f.attrs["privacy"] = IMAGE_PRIVACY
        f.attrs["headings"] = np.array(HEADINGS, dtype="int32")  # slot order N,E,S,W
        f.attrs["fov_deg"] = FOV_DEG
        f.attrs["img_size"] = IMG_SIZE

        f.create_dataset("point_id", (N,), dtype="int32")
        f.create_dataset("latitude", (N,), dtype="float64")
        f.create_dataset("longitude", (N,), dtype="float64")
        f.create_dataset("date", (N,), dtype="S10")            # "YYYY-MM"
        f.create_dataset("pano_id", (N,), dtype="S64")
        f.create_dataset("mission_year", (N,), dtype="int32")
        f.create_dataset("image_paths", (N, 4), dtype="S512")
        f.create_dataset("images_present", (N, 4), dtype="bool")
        img_ds = f.create_dataset("images_jpeg", (N, 4),
                                  dtype=h5py.vlen_dtype(np.dtype("uint8")))

        for i, row in wl.iterrows():
            pid = row["pano_id"]
            f["point_id"][i] = i                               # row index, no gaps
            f["latitude"][i] = float(row["lat"])
            f["longitude"][i] = float(row["lon"])
            f["date"][i] = np.bytes_(str(row["timestamp"])[:7])  # "YYYY-MM"
            f["pano_id"][i] = np.bytes_(pid[:64])
            f["mission_year"][i] = int(row["mission_year"])
            for j, h in enumerate(HEADINGS):
                path = _crop_path(pid, h)
                rel = os.path.relpath(path, start=os.path.dirname(out_path))
                f["image_paths"][i, j] = np.bytes_(rel)
                with open(path, "rb") as fh:
                    img_ds[i, j] = np.frombuffer(fh.read(), dtype=np.uint8)
                f["images_present"][i, j] = True
            if (i + 1) % 1000 == 0 or (i + 1) == N:
                print(f"  wrote {i + 1}/{N}")
    print(f"HDF5 written: {out_path} ({N} points)")


# =============================================================================
# CLI
# =============================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("MINLON", "MINLAT", "MAXLON", "MAXLAT"),
                    help="restrict to a small dev bounding box (lon/lat order)")
    ap.add_argument("--limit", type=int, default=None,
                    help="download at most this many new points (for testing)")
    ap.add_argument("--rebuild-worklist", action="store_true",
                    help="rebuild the work list even if cached")
    ap.add_argument("--worklist-only", action="store_true",
                    help="build the work list and stop")
    ap.add_argument("--build-h5", action="store_true",
                    help="(re)assemble the H5 from completed crops and stop")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress the explanatory narration, leaving only essential "
                         "progress output (the original, terser behaviour)")
    args = ap.parse_args(argv)

    global VERBOSE
    VERBOSE = not args.quiet

    vprint("=" * 70)
    vprint("Amsterdam open-panorama acquisition")
    vprint("=" * 70)
    vprint("Three resumable steps, each cached/idempotent so reruns only do what's left:")
    vprint("  1. build_worklist — choose which panoramas to fetch (query the API over a")
    vprint("     grid of tiles, clip to the municipality, thin to ~TARGET_N points).")
    vprint("  2. download_all   — fetch each panorama and reproject it into 4 N/E/S/W crops.")
    vprint("  3. build_h5       — pack the crops + metadata into the HDF5 store.")
    vprint("All tunables live in the CONSTANTS block at the top of this file.")
    vprint("(Run with --quiet to silence this narration.)")

    session = make_session()

    if args.build_h5:
        vprint("\n--build-h5: skipping steps 1 and 2; rebuilding the store from existing crops.")
        worklist = pd.read_parquet(WORKLIST_PATH)
        build_h5(worklist)
        return

    worklist = build_worklist(session, dev_bbox=tuple(args.bbox) if args.bbox else None,
                              force=args.rebuild_worklist)
    if args.worklist_only:
        vprint("\n--worklist-only: stopping after the work list (no downloads, no H5).")
        return

    download_all(session, worklist, limit=args.limit)
    build_h5(worklist)
    vprint("\nAll steps complete.")


if __name__ == "__main__":
    main()
