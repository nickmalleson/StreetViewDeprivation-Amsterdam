"""
Shared configuration for the StreetViewDeprivation-Amsterdam pipeline.

This is the single place where the project's settings live (paths, CRS, area of
interest, join key, target column), so that adapting the pipeline is a matter of
*configuration* rather than scattered logic. Most notebooks do
`from directory_filepaths import *`.

All paths are relative to the notebooks/ directory (where the notebooks run).
"""

import os

# ---------------------------------------------------------------------------
# Directories
# ---------------------------------------------------------------------------
data_dir    = os.path.join("..", "data", "processed")
raw_dir     = os.path.join("..", "data", "raw")
outputs_dir = os.path.join("..", "outputs")

# ---------------------------------------------------------------------------
# Data files
# ---------------------------------------------------------------------------
# Buurt boundaries (CBS WijkBuurtkaart 2024, restricted to Amsterdam municipality).
# Saved as GeoPackage so the Dutch CBS field names are not truncated (as they
# would be in a shapefile). geopandas reads .gpkg and .shp identically, so the
# notebooks are unaffected by the format choice.
boundaries_file = os.path.join(raw_dir, "spatial", "buurten_2024", "amsterdam_buurten_2024.gpkg")

# CBS SES-WOA scores per buurt (StatLine table 86092NED), downloaded via the CBS
# open-data (OData) API by notebook 3.
ses_file = os.path.join(data_dir, "ses_woa_86092NED.csv")

# HDF5 data store (images, embeddings, metadata) built by panorama_acquisition.py.
h5_filename = os.path.join(data_dir, "street_data.h5")

# Cached PCA->RGB colour model for embeddings (see embedding_rgb.py). Fit and
# re-cached by notebook 2 each run; loadable later so embedding colours stay
# consistent across the pipeline.
embedding_rgb_pca_file = os.path.join(data_dir, "embedding_rgb_pca.joblib")

# ---------------------------------------------------------------------------
# Amsterdam-specific settings (centralised so the city difference lives here)
# ---------------------------------------------------------------------------
# CBS municipality codes (gemeente) that define the study region. This is a deliberate
# *scoping choice*, not a data limitation: it restricts the boundaries, the SES-WOA
# target and the imagery work list to these municipalities. The open panorama source is
# NOT confined to Amsterdam — it also covers neighbouring regional municipalities (each
# of which has a national CBS SES-WOA score), though not separate cities such as Utrecht
# or Haarlem. The study region is therefore the set below; broadening or narrowing it is
# a matter of editing this list (it drives the SES filter, the boundary fetch and the
# work-list clip — see amsterdam_data.py and panorama_acquisition.py). Codes verified
# against CBS table 86092NED and the PDOK WijkBuurtkaart 2024:
#   0363 Amsterdam  ·  0362 Amstelveen  ·  0384 Diemen  ·  0034 Almere
# Amsterdam is listed first by convention; the order does not affect results (each
# municipality is sampled independently — see TARGET_N in panorama_acquisition.py).
MUNICIPALITY_CODES = ["0363", "0362", "0384", "0034"]

# Projected CRS for all spatial operations in metres: Amersfoort / RD New,
# the standard projected CRS for the Netherlands.
PROJECTED_CRS = "EPSG:28992"

# Geographic CRS used for lat/lon points and for Earth Engine / GeoTIFF I/O.
GEOGRAPHIC_CRS = "EPSG:4326"

# Join key for the spatial unit (CBS buurt). Buurt codes look like "BU0363....".
JOIN_KEY = "buurtcode"

# Primary deprivation target column (continuous SES-WOA total score).
TARGET_COL = "ses_woa_score"

# CBS StatLine table and reporting period for the SES-WOA target.
# Table 86092NED = "Sociaal-economische status; scores per wijk en buurt,
# regio-indeling 2024". CBS_YEAR is the OData period key (2023 is the latest;
# CBS flags the most recent year as provisional - see DATA_PROVENANCE.md).
CBS_TABLE = "86092NED"
CBS_YEAR  = "2023JJ00"

# ---------------------------------------------------------------------------
# Plot defaults (keep notebook-embedded figures small)
# ---------------------------------------------------------------------------
# Inline figures are rasterised to PNG at the figure DPI. matplotlib's default
# (100) makes the basemap maps ~1 MB each and bloats the committed notebooks.
# Dropping to 72 keeps the same layout at lower resolution (roughly half the file
# size) and is ample for on-screen / GitHub viewing. Every notebook imports this
# module before it plots, so the default applies project-wide. Override per figure
# with plt.savefig(dpi=...) where a crisper export is needed; for the photographic
# basemap maps a far bigger saving comes from saving them as JPEG instead of PNG.
import matplotlib as mpl
mpl.rcParams["figure.dpi"] = 72


def show_jpeg(fig=None, quality=85, dpi=None):
    """Display a figure inline as a JPEG (instead of the default PNG) and close it.

    Photographic figures — contextily basemaps and raster ``imshow`` maps — are far
    smaller as JPEG than as the notebook's default inline PNG (roughly 5-6x; e.g. a
    basemap map of ~1 MB PNG becomes ~0.2 MB JPEG) with no visible loss, which stops
    these heavy cells bloating the committed notebooks. Use it *in place of*
    ``plt.show()`` for such cells only; keep ``plt.show()`` (PNG) for line / scatter
    plots, where JPEG would smear text and gridlines. ``fig`` defaults to the current
    figure; ``dpi`` defaults to the figure DPI set above.
    """
    import io
    import matplotlib.pyplot as plt
    from IPython.display import Image, display

    if fig is None:
        fig = plt.gcf()
    buf = io.BytesIO()
    fig.savefig(buf, format="jpeg", dpi=dpi, bbox_inches="tight",
                pil_kwargs={"quality": quality})
    plt.close(fig)
    display(Image(data=buf.getvalue(), format="jpeg"))
