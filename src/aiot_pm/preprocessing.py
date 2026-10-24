from __future__ import annotations

import numpy as np


def normalize_window(
    rows: list[dict[str, float]], feature_names: list[str], mean: list[float], scale: list[float]
) -> np.ndarray:
    # Giữ thứ tự feature_names từ metadata; đổi thứ tự sensor sẽ làm sai suy luận.
    # Không fit lại scaler trên engine đang demo: phải dùng mean/std của train.
    values = np.asarray(
        [[float(row[name]) for name in feature_names] for row in rows], dtype=np.float32
    )
    normalized = (values - np.asarray(mean, dtype=np.float32)) / np.asarray(scale, dtype=np.float32)
    # [30,14] → [1,14,30]; float32 là hợp đồng input kể cả graph lượng tử hóa INT8.
    return normalized.T[np.newaxis, ...].astype(np.float32)
