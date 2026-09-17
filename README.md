# Street-Level Image Embeddings & Deprivation Analysis — Amsterdam

Can the socio-economic status of a neighbourhood be predicted from what its streets *look like*?
This repository provides an end-to-end, fully open workflow that tests exactly that for the
**municipality of Amsterdam**, using openly-licensed 360° street-level panoramas and openly-published
socio-economic data. 

The code has predominantly been vibe-created by Claude, especially the data acquisition stuff.
Although a lot of the method and the later stages in the analysis  (modelling deprivation etc.) 
came from a repository I worked on previously called
[INTEGRATE-Embeddings-Deprivation](https://github.com/Urban-Analytics/INTEGRATE-Embeddings-Deprivation). 
That code was supported by AI but was mostly written by me and colleagues

The workflow:

1. **acquires** open street-level panoramas for Amsterdam and reprojects each into four cardinal-facing
   rectilinear images;
2. encodes every image with **CLIP** (512-dimensional embeddings);
3. aggregates the embeddings to the neighbourhood (*buurt*) level by median pooling;
4. trains **XGBoost** models to predict the CBS **SES-WOA** socio-economic status score; and
5. compares the street-level signal against a **satellite** baseline (Google DeepMind AlphaEarth).

Everything is reproducible from open sources: the imagery, the deprivation target and the boundaries
are all downloaded by the code (`RANDOM_STATE = 42`, 80/20 train/test split).

## Key design choices

- **Imagery — City of Amsterdam Open Panorama** ("Kernregistratie panoramabeelden"), CC-BY 4.0, with
  faces and number plates blurred at source. The most recent panorama per location is used, thinned
  density-proportionally to ~5000 points (a fixed share kept per 200 m grid cell) so the sample density
  tracks local road density — denser areas get proportionally more points — at a tractable size. Each equirectangular
  panorama is reprojected into **four 90°-FOV rectilinear crops** facing N/E/S/W, so CLIP receives
  in-distribution rectangular inputs (raw equirectangular panoramas are never embedded).
- **Deprivation target — CBS SES-WOA score** (StatLine table 86092NED): a *continuous* socio-economic
  status score combining financial wealth (*welvaart*), education level (*opleidingsniveau*) and labour
  history (*arbeidsverleden*).
- **Spatial unit — CBS *buurt*** (neighbourhood; join key `buurtcode`), from the CBS WijkBuurtkaart
  2024 via PDOK, restricted to the Amsterdam municipality (gemeente 0363). All projected spatial
  operations use **EPSG:28992** (Amersfoort / RD New).
- **Configuration-first.** Everything that defines the study area (paths, CRS, AOI, join key, target
  column, CBS table/year) lives in `notebooks/directory_filepaths.py`; the non-imagery data fetching
  lives in `notebooks/amsterdam_data.py`. Adapting the pipeline is a matter of configuration.

## Data sources, licences & attribution

See **[`DATA_PROVENANCE.md`](DATA_PROVENANCE.md)** for the full, paper-ready statement. In brief:

- **Imagery:** City of Amsterdam Open Panorama, **CC-BY 4.0**. Attribution: *"Contains data from
  Gemeente Amsterdam, licensed under CC-BY 4.0."*
- **Deprivation:** CBS **SES-WOA** score per buurt, StatLine table **86092NED** (regio-indeling 2024,
  reporting year 2023), CBS open data.
- **Boundaries:** CBS WijkBuurtkaart 2024 *buurten* via PDOK.
- **Satellite:** Google DeepMind **AlphaEarth** Satellite Embedding annual composite (via Google Earth
  Engine).

## Reporting the SES-WOA target

SES-WOA is a **continuous score**. Results are reported as the **raw-score R²** on the SES-WOA total —
the measure of explained variance.

### "Domains" (notebook 8)

SES-WOA is a single composite index built from three constituent components, so — unlike multi-domain
indices such as the UK IMD — it has no set of separate deprivation domains. Notebook 8 therefore
predicts the **three SES-WOA components** (welvaart, opleidingsniveau, arbeidsverleden). Because these
are constituents of one composite, they are highly inter-correlated by construction; the analysis is a
coarse component-level decomposition rather than a set of independent dimensions, and the notebook
documents this limitation inline.

## Notebook dependency graph

```
                     External data
              (open panoramas, CBS SES-WOA, buurten)
                          │
                   ┌──────┴──────┐
                   ▼             ▼
              1-Acquire      9a-AlphaEarth
              Panoramas      (GEE download)
                   │             │
                   ▼             │
              2-Calculate        │
              Embeddings         │
                   │             │
                   ▼             │
              3-Process          │
              Embeddings         │
                   │             │
              ┌────┴────┐        │
              ▼         ▼        │
         4-Global    5-Cluster   │
         Model       Numbers     │
              │         │        │
              │    ┌────┼────┐   │
              │    ▼    ▼    ▼   │
              │    6    7    8   │
              │                  │
              └─────────┬────────┘
                        ▼
                   9b-AlphaEarth
                   Model
```

| Notebook | Depends on | Key inputs |
|----------|-----------|------------|
| **1** | — | Open Panorama API (via `panorama_acquisition.py`) |
| **2** | 1 | H5 store (images) |
| **3** | 2 | H5 store (embeddings), buurt boundaries |
| **4** | 3 | Per-buurt median embedding, SES-WOA target |
| **5** | 3 | Expanded image-level pickle, H5 (for sample images) |
| **6** | 4, 5 | Cluster-assigned pickle, best model, SES-WOA |
| **7** | 4, 5 | Cluster-assigned pickle, best model, SES-WOA |
| **8** | 4, 5 | Cluster-assigned pickle, best model, SES-WOA components |
| **9a** | — | Google Earth Engine (requires authentication) |
| **9b** | 4, 9a | AlphaEarth embedding pickle, best model, SES-WOA |

Shared modules:
- **`directory_filepaths.py`** — paths and study settings (`MUNICIPALITY_CODE`, `PROJECTED_CRS`,
  `JOIN_KEY`, `TARGET_COL`, `CBS_TABLE`, `CBS_YEAR`).
- **`clustering_functions.py`** — `global_k`, `embedding_statistic`, `RANDOM_STATE`, aggregation helpers.
- **`amsterdam_data.py`** — downloads/caches the buurt boundaries and CBS SES-WOA target.

## The notebooks

### `panorama_acquisition.py` (the acquisition script)
Standalone, **resumable** command-line script that builds the canonical work list from the Open
Panorama API, downloads each panorama, reprojects it into four N/E/S/W crops, and writes the H5 store.
Gentle on the server (serial, polite delay, exponential backoff on HTTP 429), fully idempotent (atomic
writes + a transactional manifest), and safe to Ctrl-C and rerun until complete. All knobs are constants
at the top of the file. Run from `notebooks/`:

```bash
python panorama_acquisition.py --bbox 4.890 52.368 4.900 52.374   # small dev box
python panorama_acquisition.py                                     # whole municipality
```

The work list is stamped with the settings it was built from (target size, stratum, spacing floor,
seed, tile size, AOI, image variant, bbox) and **rebuilds automatically** if you change any of them —
so to re-sample at, say, 8000 points you just edit `TARGET_N` and rerun; no need to remember
`--rebuild-worklist`. Re-thinning never re-downloads existing panoramas (completed points are skipped
via the manifest); only the newly-introduced points are fetched.

### 1-SampleStreetNetwork.ipynb
Drives the acquisition script and **sanity-checks** the resulting H5 store. **Output:** H5 database of
images + metadata + provenance.

### 2-CalculateEmbeddings.ipynb
Computes a 512-dim CLIP embedding for each image and writes them back to the H5 store.

### 3-ProcessEmbeddings+FindMedianEmbeddingPerLSOA.ipynb
Spatially joins images to buurten, expands to one row per image, and computes per-buurt mean/median
embeddings. (The "PerLSOA" suffix in the filename is retained only so the notebook numbering lines up
with the companion study referenced below; the unit here is the buurt.) **Output:** expanded pickle +
per-buurt embedding summaries.

### 4-RunModelsWithMedianEmbedding.ipynb
Model selection, hyper-parameter tuning and out-of-sample testing (XGBoost, 80/20) predicting the
SES-WOA score (raw-score R²). **Output:** saved best model.

### 5-IdentifyOptimalClusterNumber.ipynb
Clustering diagnostics (silhouette, elbow, ARI) to choose the number of clusters (`global_k` in
`clustering_functions.py`), plus per-cluster example-image figures.

### 6-TestModelOverClusters_ControlledForSampleSize.ipynb
How predictive performance varies with sample size and cluster count, controlling for image counts.

### 7-RunModels_ForEachOfNClusters.ipynb
Per-buurt per-cluster embedding summaries, then a separate XGBoost model per cluster.

### 8-Prediction_Deprivation_domains.ipynb
Predicts the three SES-WOA components per cluster + global. **Output:** R²/NRMSE heatmaps.

### 9a-DownloadAlphaEarthEmbeddings.ipynb
Downloads AlphaEarth satellite embeddings for Amsterdam and computes per-buurt zonal stats. **Requires
GEE authentication + a Cloud project (set the `GEE_PROJECT` env var) and a manual GeoTIFF download** (flagged inline).

### 9b-RunModelWithAlphaEarthEmbeddings.ipynb
XGBoost on the 64-dim satellite embeddings (same methodology as notebook 4), enabling a street-view vs
satellite comparison. **Output:** `best_model_alphaearth.joblib`; residuals map.

## Environment

```bash
conda env create -f environment.yml
conda activate streetview-deprivation-amsterdam
```

## Things you must do yourself

- **Google Earth Engine** authentication + a Cloud project, and the **manual AlphaEarth GeoTIFF
  download** (notebook 9a).
- Choose the cluster count **`global_k`** for Amsterdam from notebook 5's diagnostics.

## Related work

The method here — CLIP embeddings of cardinal street-level views, pooled to small areas and regressed
onto a deprivation measure, with a parallel satellite-embedding baseline — was first developed for
Greater Manchester in the [INTEGRATE](https://urban-analytics.github.io/INTEGRATE/) project
([INTEGRATE-Embeddings-Deprivation](https://github.com/Urban-Analytics/INTEGRATE-Embeddings-Deprivation),
using Google Street View and the UK Index of Multiple Deprivation). This repository deliberately keeps
the same notebook numbering, `RANDOM_STATE` and 80/20 split so the two cities can be compared directly,
while standing on its own as a fully-open Amsterdam study.

## Licence

Code released under the MIT licence (see `LICENSE`). Data are subject to their own licences — see
[`DATA_PROVENANCE.md`](DATA_PROVENANCE.md).
