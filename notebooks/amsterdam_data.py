"""
Amsterdam reference-data helpers: buurt boundaries and the CBS SES-WOA target.

These two functions download (and locally cache) the non-imagery Amsterdam data
that the notebooks need, keeping the notebooks themselves simple (they just read a
cached file from disk):

  * get_amsterdam_buurten()  -> GeoDataFrame of Amsterdam municipality buurten
                                (CBS WijkBuurtkaart 2024 via PDOK WFS), cached as
                                a GeoPackage at `boundaries_file`.
  * get_ses_woa()            -> DataFrame of CBS SES-WOA scores per buurt
                                (StatLine table 86092NED via OData), cached as a
                                CSV at `ses_file`.
"""

import os
import io
import time

import requests
import pandas as pd
import geopandas as gpd

from directory_filepaths import (
    boundaries_file, ses_file,
    MUNICIPALITY_CODE, CBS_TABLE, CBS_YEAR, JOIN_KEY,
    PROJECTED_CRS, GEOGRAPHIC_CRS,
)

# A clear User-Agent identifying this as academic research (polite API use).
USER_AGENT = "StreetViewDeprivation-Amsterdam/1.0 (academic research; n.s.malleson@leeds.ac.uk)"

# Endpoints
PDOK_WFS = "https://service.pdok.nl/cbs/wijkenbuurten/2024/wfs/v1_0"
CBS_ODATA = "https://opendata.cbs.nl/ODataApi/odata"

# Generous bounding box covering the whole Amsterdam municipality (lat/lon, WGS84).
# Used only to limit the PDOK WFS request; the result is then filtered to the
# municipality by gemeentecode, so the exact bbox is not critical.
_AMS_BBOX_LATLON = (52.27, 4.72, 52.44, 5.08)  # (min_lat, min_lon, max_lat, max_lon)

# CBS SES-WOA score columns in table 86092NED (confirmed via the OData metadata).
#   GemiddeldeScore_29 = SES-WOA total score (primary target)
#   GemiddeldeScore_31 = deelscore financiele welvaart (welvaart)
#   GemiddeldeScore_33 = deelscore opleidingsniveau
#   GemiddeldeScore_35 = deelscore arbeidsverleden
_SES_COLS = {
    "GemiddeldeScore_29": "ses_woa_score",
    "GemiddeldeScore_31": "ses_welvaart",
    "GemiddeldeScore_33": "ses_opleiding",
    "GemiddeldeScore_35": "ses_arbeidsverleden",
}


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


# ---------------------------------------------------------------------------
# Buurt boundaries (the spatial analysis unit)
# ---------------------------------------------------------------------------
def get_amsterdam_buurten(force=False):
    """
    Return a GeoDataFrame of Amsterdam municipality buurten (land only), in the
    projected CRS (EPSG:28992). Cached as a GeoPackage at `boundaries_file`.

    Columns include `buurtcode` (the JOIN_KEY, e.g. 'BU0363AA01'), `buurtnaam`,
    `wijkcode` and `gemeentecode`.
    """
    if os.path.exists(boundaries_file) and not force:
        return gpd.read_file(boundaries_file)

    os.makedirs(os.path.dirname(boundaries_file), exist_ok=True)
    min_lat, min_lon, max_lat, max_lon = _AMS_BBOX_LATLON

    # NOTE: this PDOK WFS silently ignores CQL_FILTER, so we fetch by bounding box
    # (which it honours) and filter to the municipality client-side.
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeName": "wijkenbuurten:buurten", "outputFormat": "application/json",
        "srsName": GEOGRAPHIC_CRS, "count": "10000",
        "bbox": f"{min_lat},{min_lon},{max_lat},{max_lon},urn:ogc:def:crs:EPSG::4326",
    }
    r = _session().get(PDOK_WFS, params=params, timeout=120)
    r.raise_for_status()
    gdf = gpd.read_file(io.BytesIO(r.content))

    gm_code = f"GM{MUNICIPALITY_CODE}"
    gdf = gdf[gdf["gemeentecode"] == gm_code].copy()
    # Drop artificial "water" buurten (large inland water bodies), keeping land units.
    if "water" in gdf.columns:
        gdf = gdf[gdf["water"] != "JA"].copy()

    gdf = gdf.rename(columns={"buurtcode": JOIN_KEY})
    gdf = gdf.to_crs(PROJECTED_CRS)
    gdf = gdf.reset_index(drop=True)

    gdf.to_file(boundaries_file, driver="GPKG")
    print(f"Saved {len(gdf)} Amsterdam buurten to {boundaries_file}")
    return gdf


# ---------------------------------------------------------------------------
# CBS SES-WOA target (the deprivation measure)
# ---------------------------------------------------------------------------
def get_ses_woa(force=False):
    """
    Return a DataFrame of CBS SES-WOA scores per Amsterdam buurt for CBS_YEAR,
    keyed on `buurtcode` (the JOIN_KEY). Cached as a CSV at `ses_file`.

    Columns: buurtcode, ses_woa_score (total, the primary target),
             ses_welvaart, ses_opleiding, ses_arbeidsverleden (the three
             SES-WOA components used by notebook 8).
    """
    if os.path.exists(ses_file) and not force:
        return pd.read_csv(ses_file, dtype={JOIN_KEY: str})

    os.makedirs(os.path.dirname(ses_file), exist_ok=True)
    select = ["WijkenEnBuurten", "Perioden"] + list(_SES_COLS.keys())
    # OData filter: Amsterdam buurten (codes start 'BU0363') for the chosen period.
    flt = (f"startswith(WijkenEnBuurten,'BU{MUNICIPALITY_CODE}') "
           f"and Perioden eq '{CBS_YEAR}'")
    params = {"$format": "json", "$select": ",".join(select), "$filter": flt}

    rows, url = [], f"{CBS_ODATA}/{CBS_TABLE}/TypedDataSet"
    s = _session()
    while url:
        resp = s.get(url, params=params, timeout=120)
        resp.raise_for_status()
        payload = resp.json()
        rows.extend(payload["value"])
        url = payload.get("odata.nextLink") or payload.get("@odata.nextLink")
        params = None  # nextLink already carries the query
        time.sleep(0.2)

    df = pd.DataFrame(rows)
    df = df.rename(columns=_SES_COLS)
    df[JOIN_KEY] = df["WijkenEnBuurten"].str.strip()
    df = df[[JOIN_KEY] + list(_SES_COLS.values())].dropna(subset=["ses_woa_score"])
    df = df.reset_index(drop=True)

    df.to_csv(ses_file, index=False)
    print(f"Saved SES-WOA ({CBS_YEAR}) for {len(df)} buurten to {ses_file}")
    return df
