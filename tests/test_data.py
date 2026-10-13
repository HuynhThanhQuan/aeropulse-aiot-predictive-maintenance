import pandas as pd

from aiot_pm.data import build_windows, split_engine_ids


def test_build_windows_uses_last_cycle_label() -> None:
    frame = pd.DataFrame(
        {
            "unit_id": [1, 1, 1, 1],
            "cycle": [1, 2, 3, 4],
            "sensor_2": [0.0, 1.0, 2.0, 3.0],
            "rul": [3, 2, 1, 0],
        }
    )
    values, labels = build_windows(frame, ["sensor_2"], window_size=2, warning_rul=1)
    assert values.shape == (3, 1, 2)
    assert labels.tolist() == [0.0, 1.0, 1.0]


def test_split_engine_ids_has_no_overlap() -> None:
    frame = pd.DataFrame({"unit_id": list(range(1, 11))})
    train, validation = split_engine_ids(frame, validation_fraction=0.2, seed=42)
    assert len(validation) == 2
    assert set(train).isdisjoint(validation)
