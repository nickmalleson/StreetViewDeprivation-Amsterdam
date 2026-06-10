# Data provenance & licensing

---

## 1. Street-level imagery — City of Amsterdam Open Panorama

- **Dataset:** "Kernregistratie panoramabeelden" (panorama imagery base registration), 360°
  equirectangular street-level panoramas.
- **Publisher:** Gemeente Amsterdam (City of Amsterdam).
- **Access:** REST API at `https://api.data.amsterdam.nl/panorama/panoramas/` (locations/metadata and
  equirectangular images). Retrieved per ~450 m bbox tile, restricted to the Amsterdam municipality
  (gemeente 0363).
- **Licence:** **Creative Commons Attribution 4.0 International (CC-BY 4.0).**
- **Required attribution:** *"Contains data from Gemeente Amsterdam, licensed under CC-BY 4.0."*
- **Privacy:** Faces and vehicle number plates are **blurred at source** by the publisher.
- **Processing in this repository:** each equirectangular panorama was reprojected into **four
  90°-field-of-view rectilinear images** facing north, east, south and west (via `py360convert`), at
  640×640 px. Raw equirectangular panoramas are not used as model inputs. The image source, licence,
  attribution and privacy status are also recorded in the HDF5 data store's root attributes.
- **Access date:** _<to be completed at download>_

> **Suggested data-statement sentence.** "Street-level imagery was obtained from the City of Amsterdam
> Open Panorama service ('Kernregistratie panoramabeelden'), published by Gemeente Amsterdam and
> licensed under CC-BY 4.0 (faces and number plates blurred at source). Contains data from Gemeente
> Amsterdam, licensed under CC-BY 4.0."

---

## 2. Deprivation target — CBS SES-WOA score

- **Dataset:** Socio-economic status score (**SES-WOA**: sociaaleconomische status — *welvaart*,
  *opleidingsniveau*, *arbeidsverleden*; i.e. financial wealth, education level and labour history) per
  *buurt*.
- **Source table:** Statistics Netherlands (CBS) StatLine table **86092NED**, *"Sociaal-economische
  status; scores per wijk en buurt, regio-indeling 2024"*.
- **Publisher:** Centraal Bureau voor de Statistiek (CBS).
- **Access:** CBS open-data (OData) API at `https://opendata.cbs.nl/ODataApi/odata/86092NED/`,
  filtered to Amsterdam buurten (codes beginning `BU0363`).
- **Reporting year:** **2023** (the latest available in this table edition). CBS flags its most recent
  reporting year as **provisional** (*voorlopig*); figures may be revised in a later release. For exact
  reproducibility, pin the table edition/access date below.
- **Columns used:** `GemiddeldeScore_29` = SES-WOA total score (the primary target, mapped to
  `ses_woa_score`); the three component scores welvaart / opleidingsniveau / arbeidsverleden are mapped
  to `ses_welvaart`, `ses_opleiding`, `ses_arbeidsverleden`.
- **Nature of the measure:** SES-WOA is a **continuous score** (≈ mean 0). This repository reports
  raw-score R² as the measure of explained variance.
- **Licence:** CBS open data; freely reusable with source attribution to CBS (CC-BY 4.0 equivalent
  under the CBS terms of use).
- **Required attribution / citation:** *"Source: CBS, StatLine table 86092NED (Sociaal-economische
  status; scores per wijk en buurt, regio-indeling 2024), reporting year 2023."*
- **Access date:** _<to be completed at download>_

> **Suggested data-statement sentence.** "Area-level deprivation was measured using the socio-economic
> status score (SES-WOA) per buurt from Statistics Netherlands (CBS) StatLine table 86092NED
> (regio-indeling 2024; reporting year 2023), retrieved via the CBS open-data API and reproduced under
> the CBS open-data terms (source: CBS)."

---

## 3. Spatial units & boundaries — CBS WijkBuurtkaart 2024 (via PDOK)

- **Dataset:** CBS Wijk- en Buurtkaart 2024 — *buurten* (neighbourhood) polygons.
- **Publisher:** CBS, served via PDOK.
- **Access:** PDOK WFS `https://service.pdok.nl/cbs/wijkenbuurten/2024/wfs/v1_0` (layer
  `wijkenbuurten:buurten`), restricted to Amsterdam (gemeentecode `GM0363`), land buurten only.
- **Join key:** `buurtcode` (format `BU########`, e.g. `BU0363AA01`).
- **Projected CRS:** all projected (metre-based) spatial operations use **EPSG:28992** (Amersfoort /
  RD New); WGS84 (EPSG:4326) is used for point joins and Earth Engine / GeoTIFF I/O.
- **Licence:** CBS / PDOK open data (CC-BY 4.0 equivalent; attribute CBS).
- **Access date:** _<to be completed at download>_

---

## 4. Satellite embeddings — Google DeepMind AlphaEarth (via Google Earth Engine)

- **Dataset:** AlphaEarth Foundations Satellite Embedding annual composite
  (`GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL`), 64-dimensional, 10 m resolution, year **2023**.
- **Publisher:** Google / Google DeepMind, via Google Earth Engine.
- **Access:** exported for the Amsterdam AOI via the Earth Engine Python API, then summarised zonally
  per buurt. **Requires** Google Earth Engine authentication and a Google Cloud project, and a manual
  GeoTIFF download from Google Drive after the export task completes.
- **Licence / terms:** subject to the Google Earth Engine terms of service and the dataset's own terms;
  cite Google DeepMind AlphaEarth Foundations. Not redistributed in this repository.
- **Access date:** _<to be completed at download>_

---

## Summary table

| Component | Source | Unit | Licence | Attribution |
|-----------|--------|------|---------|-------------|
| Imagery | Amsterdam Open Panorama (Kernregistratie panoramabeelden) | panorama point | CC-BY 4.0 | "Contains data from Gemeente Amsterdam, licensed under CC-BY 4.0" |
| Deprivation | CBS SES-WOA (StatLine 86092NED, 2023) | buurt | CBS open data | "Source: CBS, table 86092NED" |
| Boundaries | CBS WijkBuurtkaart 2024 (PDOK) | buurt | CBS/PDOK open data | "Source: CBS" |
| Satellite | AlphaEarth Satellite Embedding (GEE) | 10 m pixel → buurt | Google EE terms | "Google DeepMind AlphaEarth" |

