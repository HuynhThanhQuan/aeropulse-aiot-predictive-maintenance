from __future__ import annotations

import numpy as np


def normalize_window(
    rows: list[dict[str, float]], feature_names: list[str], mean: list[float], scale: list[float]
) -> np.ndarray:
    values = np.asarray(
        [[float(row[name]) for name in feature_names] for row in rows], dtype=np.float32
    )
    normalized = (values - np.asarray(mean, dtype=np.float32)) / np.asarray(scale, dtype=np.float32)
    return normalized.T[np.newaxis, ...].astype(np.float32)
