"""Shared model-evaluation helpers.

`compute_metrics` is the single source of truth for the regression metrics
reported across the modelling notebooks (4, 7, 9b). Each notebook keeps its own
`evaluate_model` wrapper for plotting, but delegates the metric calculation
here so the numbers stay consistent.
"""

import numpy as np
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from scipy.stats import spearmanr


def compute_metrics(y_true, y_pred):
    """Regression metrics for predicted vs true SES-WOA values.

    Returns a dict with RMSE, NRMSE (RMSE normalised by the std dev of
    ``y_true``, so it is comparable across models/clusters), MAE, raw-score R²
    (the primary metric) and the Spearman rank correlation.
    """
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    return {
        'RMSE': rmse,
        'NRMSE': rmse / np.std(y_true),
        'MAE': mean_absolute_error(y_true, y_pred),
        'R2': r2_score(y_true, y_pred),
        'Spearman_rank_corr': spearmanr(y_true, y_pred)[0],
    }
