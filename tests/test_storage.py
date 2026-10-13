from aiot_pm.storage_service import InfluxStorageService


def test_prediction_converts_to_influx_line_protocol() -> None:
    point = InfluxStorageService.to_point(
        "prediction",
        "FD001-100",
        {
            "cycle": 100,
            "failure_probability": 0.91,
            "latency_ms": 1.2,
            "predicted_class": 1,
            "risk_level": "high",
        },
    )
    line = point.to_line_protocol()
    assert line.startswith("prediction,engine_id=FD001-100,risk_level=high")
    assert "failure_probability=0.91" in line
