from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

COLUMNS = (
    ["unit_id", "cycle"]
    + [f"op_setting_{index}" for index in range(1, 4)]
    + [f"sensor_{index}" for index in range(1, 22)]
)


@dataclass(frozen=True)
class PreparedData:
    x_train: np.ndarray
    y_train: np.ndarray
    x_validation: np.ndarray
    y_validation: np.ndarray
    scaler: StandardScaler
    feature_names: list[str]
    train_engine_ids: list[int]
    validation_engine_ids: list[int]


def load_cmapss(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path, sep=r"\s+", header=None, names=COLUMNS)
    if frame.shape[1] != len(COLUMNS):
        raise ValueError(f"Expected {len(COLUMNS)} C-MAPSS columns, got {frame.shape[1]}")
    if frame.empty:
        raise ValueError("C-MAPSS data file is empty")
    frame["rul"] = frame.groupby("unit_id")["cycle"].transform("max") - frame["cycle"]
    return frame


def split_engine_ids(
    frame: pd.DataFrame, validation_fraction: float, seed: int
) -> tuple[list[int], list[int]]:
    ids = np.array(sorted(frame["unit_id"].unique()), dtype=np.int64)
    rng = np.random.default_rng(seed)
    rng.shuffle(ids)
    validation_count = max(1, round(len(ids) * validation_fraction))
    validation_ids = sorted(ids[:validation_count].tolist())
    train_ids = sorted(ids[validation_count:].tolist())
    if not train_ids:
        raise ValueError("At least one engine must remain in the training split")
    return train_ids, validation_ids


def build_windows(
    frame: pd.DataFrame,
    feature_names: list[str],
    window_size: int,
    warning_rul: int,
) -> tuple[np.ndarray, np.ndarray]:
    windows: list[np.ndarray] = []
    labels: list[int] = []
    for _, engine in frame.groupby("unit_id", sort=True):
        values = engine[feature_names].to_numpy(dtype=np.float32)
        rul = engine["rul"].to_numpy(dtype=np.int64)
        for end in range(window_size - 1, len(engine)):
            windows.append(values[end - window_size + 1 : end + 1].T)
            labels.append(int(rul[end] <= warning_rul))
    if not windows:
        raise ValueError("No windows were generated; reduce window_size or check the dataset")
    return np.stack(windows).astype(np.float32), np.asarray(labels, dtype=np.float32)


def prepare_data(
    path: str | Path,
    sensors: list[int],
    window_size: int,
    warning_rul: int,
    validation_fraction: float,
    seed: int,
) -> PreparedData:
    frame = load_cmapss(path)
    feature_names = [f"sensor_{sensor}" for sensor in sensors]
    missing = set(feature_names) - set(frame.columns)
    if missing:
        raise ValueError(f"Unknown sensor columns: {sorted(missing)}")

    train_ids, validation_ids = split_engine_ids(frame, validation_fraction, seed)
    train_frame = frame[frame["unit_id"].isin(train_ids)].copy()
    validation_frame = frame[frame["unit_id"].isin(validation_ids)].copy()

    scaler = StandardScaler()
    train_frame[feature_names] = scaler.fit_transform(train_frame[feature_names]).astype(np.float32)
    validation_frame[feature_names] = scaler.transform(validation_frame[feature_names]).astype(
        np.float32
    )

    x_train, y_train = build_windows(train_frame, feature_names, window_size, warning_rul)
    x_validation, y_validation = build_windows(
        validation_frame, feature_names, window_size, warning_rul
    )
    return PreparedData(
        x_train=x_train,
        y_train=y_train,
        x_validation=x_validation,
        y_validation=y_validation,
        scaler=scaler,
        feature_names=feature_names,
        train_engine_ids=train_ids,
        validation_engine_ids=validation_ids,
    )
