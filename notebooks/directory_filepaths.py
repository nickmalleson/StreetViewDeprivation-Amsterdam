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
# CBS municipality code for Amsterdam (gemeente). This is a deliberate *scoping
# choice*, not a data limitation: it restricts the boundaries, the SES-WOA target
# and the imagery work list to the Amsterdam municipality. The open panorama source
# itself is NOT confined to Amsterdam — it also covers neighbouring regional
# municipalities (verified: Amstelveen, Almere and Diemen all return panoramas),
# though not separate cities such as Utrecht or Haarlem. The study could therefore
# be broadened to those municipalities (which also have CBS SES-WOA scores) by
# widening this to a set of municipality codes here and in the SES filter, the
# boundary fetch and the work-list clip.
MUNICIPALITY_CODE = "0363"

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
