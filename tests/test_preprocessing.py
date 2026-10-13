import numpy as np

from aiot_pm.preprocessing import normalize_window


def test_normalize_window_transposes_for_conv1d() -> None:
    rows = [
        {"sensor_2": 10.0, "sensor_3": 20.0},
        {"sensor_2": 12.0, "sensor_3": 24.0},
    ]
    values = normalize_window(rows, ["sensor_2", "sensor_3"], [10.0, 20.0], [2.0, 4.0])
    assert values.shape == (1, 2, 2)
    np.testing.assert_allclose(values[0], [[0.0, 1.0], [0.0, 1.0]])
