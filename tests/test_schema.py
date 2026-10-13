from aiot_pm.schema import Telemetry, engine_topic, topic_engine_id


def test_telemetry_adds_timestamp() -> None:
    payload = Telemetry(
        engine_id="FD001-001",
        cycle=12,
        sensors={"sensor_2": 641.2},
        operational_settings={"op_setting_1": 0.0},
    ).to_dict()
    assert payload["timestamp"]
    assert payload["cycle"] == 12


def test_engine_topic_round_trip() -> None:
    topic = engine_topic("FD001-100", "prediction")
    assert topic == "aiot/engine/FD001-100/prediction"
    assert topic_engine_id(topic) == "FD001-100"
