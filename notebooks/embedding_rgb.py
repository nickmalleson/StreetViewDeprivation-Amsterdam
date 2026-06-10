"""
Deterministic PCA->RGB colour model for CLIP street-level embeddings.

A single fitted model maps a 512-d CLIP embedding to an (R, G, B) colour, so that
embeddings can be *visualised as colours* consistently wherever this is useful
(notebook 2's sanity-check map, and any later step). Because the mapping is the
same everywhere, two points that look alike on one map look alike on another.

The model is fit by notebook 2 and **re-fit / re-cached every time that notebook
runs**, since new imagery (and hence new embeddings) may have been added since
the last run. Later steps that just want colours should *load* the cached model
via `load_embedding_rgb` rather than re-fitting, so they reuse the same axes.

A model is a small dict persisted with joblib:
    pca : fitted sklearn PCA(n_components=3) -- the three colour axes
    lo, hi : per-component robust percentile bounds mapping each principal
             component to [0, 1] for use as R, G and B.
"""

import numpy as np
import joblib
from sklearn.decomposition import PCA

RANDOM_STATE = 42          # matches clustering_functions.RANDOM_STATE for reproducibility
N_COMPONENTS = 3           # three components -> R, G, B
PERCENTILE = (2.0, 98.0)   # robust per-component bounds for the [0, 1] colour stretch


def _valid_rows(embeddings):
    """Return the finite (M, D) rows of an array of embeddings, dropping any NaN rows.

    Accepts any (..., D) shape (e.g. (N, 512) per-point or (N, 4, 512) per-crop)
    and flattens the leading axes.
    """
    emb = np.asarray(embeddings, dtype=np.float64)
    flat = emb.reshape(-1, emb.shape[-1])
    return flat[np.isfinite(flat).all(axis=1)]


def fit_embedding_rgb(embeddings, cache_path=None, percentile=PERCENTILE):
    """Fit the PCA->RGB model on (..., D) embeddings and (optionally) cache it.

    The PCA is fit on every finite embedding row; the [0, 1] colour stretch is
    derived from robust percentiles of the fitted components, so a few outliers
    do not wash out the colours. Returns the model dict.
    """
    X = _valid_rows(embeddings)
    if X.shape[0] < N_COMPONENTS:
        raise ValueError(f"Need at least {N_COMPONENTS} valid embeddings to fit, got {X.shape[0]}")

    pca = PCA(n_components=N_COMPONENTS, random_state=RANDOM_STATE).fit(X)
    comps = pca.transform(X)
    lo = np.percentile(comps, percentile[0], axis=0)
    hi = np.percentile(comps, percentile[1], axis=0)

    model = {
        "pca": pca,
        "lo": lo,
        "hi": hi,
        "percentile": percentile,
        "n_fit": int(X.shape[0]),
        "explained_variance_ratio": pca.explained_variance_ratio_,
    }
    if cache_path is not None:
        joblib.dump(model, cache_path)
    return model


def load_embedding_rgb(cache_path):
    """Load a previously cached PCA->RGB model (as written by `fit_embedding_rgb`)."""
    return joblib.load(cache_path)


def embeddings_to_rgb(model, embeddings):
    """Map (..., D) embeddings to (..., 3) RGB floats in [0, 1].

    Rows that contain any non-finite value (e.g. points/crops with no image) are
    returned as NaN so callers can mask them out.
    """
    emb = np.asarray(embeddings, dtype=np.float64)
    flat = emb.reshape(-1, emb.shape[-1])
    ok = np.isfinite(flat).all(axis=1)

    rgb = np.full((flat.shape[0], N_COMPONENTS), np.nan)
    if ok.any():
        comps = model["pca"].transform(flat[ok])
        span = np.where(model["hi"] > model["lo"], model["hi"] - model["lo"], 1.0)
        scaled = (comps - model["lo"]) / span
        rgb[ok] = np.clip(scaled, 0.0, 1.0)
    return rgb.reshape(*emb.shape[:-1], N_COMPONENTS)
