from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from aiot_pm.config import load_config
from aiot_pm.dashboard import STATE, DashboardState, app, connection_mode
from aiot_pm.dashboard_cache import DashboardCache


def test_cache_save_and_load_round_trip(tmp_path) -> None:
    cache = DashboardCache(tmp_path / "cache.sqlite")
    cache.start()
    payload = {"cycle": 12, "sensors": {"sensor_2": 642.5}, "timestamp": "2026-10-17T10:00:00+00:00"}
    cache.enqueue("FD001-100", "telemetry", 12, payload["timestamp"], payload)
    cache.enqueue(
        "FD001-100",
        "status",
        None,
        "2026-10-17T10:00:01+00:00",
        {"state": "running", "maintenance_scheduled": False},
    )
    cache.flush()
    loaded = cache.load()
    cache.stop()

    assert loaded["telemetry"]["FD001-100"] == [payload]
    assert loaded["status"]["FD001-100"]["state"] == "running"
    assert loaded["events"]["FD001-100"][0]["kind"] == "status"
    assert loaded["latest_ts"] == "2026-10-17T10:00:01+00:00"


def test_connection_mode_live_stale_offline() -> None:
    assert connection_mode(False, 0.1, 5) == "offline"
    assert connection_mode(True, 4.9, 5) == "live"
    assert connection_mode(True, 5, 5) == "stale"
    assert connection_mode(True, None, 5) == "stale"


def test_api_starts_when_mqtt_is_unreachable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DASHBOARD_CACHE_PATH", str(tmp_path / "cache.sqlite"))
    monkeypatch.setattr(STATE.client, "connect_async", lambda *args, **kwargs: None)
    monkeypatch.setattr(STATE.client, "loop_start", lambda: None)
    monkeypatch.setattr(STATE.client, "loop_stop", lambda: None)
    monkeypatch.setattr(STATE.client, "disconnect", lambda: None)
    monkeypatch.setattr(STATE.client, "is_connected", lambda: False)

    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json() == {
            "status": "degraded",
            "mqtt_connected": False,
            "mode": "offline",
        }
        snapshot = client.get("/api/snapshot")
        assert snapshot.status_code == 200
        body = snapshot.json()
        assert body["mode"] == "offline"
        assert body["from_cache"] is True
        command = client.post(
            "/api/command",
            json={"engine_id": "FD001-100", "action": "reset_maintenance"},
        )
        assert command.status_code == 503
        assert command.json()["detail"] == "MQTT not connected"


def test_duplicate_cycle_keeps_buffers_and_large_drop_clears_them(tmp_path) -> None:
    state = DashboardState(load_config(), cache=DashboardCache(tmp_path / "cache.sqlite"))
    state.cache.start()
    state.ingest("telemetry", "FD001-100", {"cycle": 10, "sensors": {}})
    state.ingest("telemetry", "FD001-100", {"cycle": 11, "sensors": {}})
    state.ingest("prediction", "FD001-100", {"cycle": 11, "failure_probability": 0.2})
    state.ingest("telemetry", "FD001-100", {"cycle": 11, "sensors": {"sensor_2": 1}})
    assert [row["cycle"] for row in state.telemetry["FD001-100"]] == [10, 11, 11]
    assert len(state.predictions["FD001-100"]) == 1

    state.ingest("telemetry", "FD001-100", {"cycle": 6, "sensors": {}})
    assert [row["cycle"] for row in state.telemetry["FD001-100"]] == [10, 11, 11, 6]

    state.ingest("telemetry", "FD001-100", {"cycle": 0, "sensors": {}})
    assert [row["cycle"] for row in state.telemetry["FD001-100"]] == [0]
    assert len(state.predictions["FD001-100"]) == 0
    state.cache.stop()


def test_startup_shows_saved_rows_before_mqtt(tmp_path) -> None:
    cache = DashboardCache(tmp_path / "cache.sqlite")
    cache.start()
    cache.enqueue("FD001-100", "telemetry", 8, "2020-01-01T00:00:00+00:00", {"cycle": 8, "sensors": {}})
    cache.flush()
    state = DashboardState(load_config(), cache=cache)
    state._load_cache()
    snapshot = state.snapshot()
    assert snapshot["from_cache"] is True
    assert snapshot["telemetry"][0]["cycle"] == 8
    assert snapshot["last_message_at"] == "2020-01-01T00:00:00+00:00"
    age = snapshot["seconds_since_last_message"]
    assert age is not None and age > 5
    cache.stop()


def test_unknown_engine_snapshot_does_not_reuse_another_engine(tmp_path) -> None:
    state = DashboardState(load_config(), cache=DashboardCache(tmp_path / "cache.sqlite"))
    state.ingest("telemetry", "FD001-100", {"cycle": 40, "sensors": {"sensor_2": 1}})
    snapshot = state.snapshot("FD001-095")
    assert snapshot["engine_id"] == "FD001-095"
    assert snapshot["telemetry"] == []
    assert snapshot["predictions"] == []
    assert snapshot["events"] == []
    assert snapshot["status"] is None
    assert "FD001-100" in snapshot["engines"]


def test_recent_message_is_live_only_while_connected(tmp_path) -> None:
    state = DashboardState(load_config(), cache=DashboardCache(tmp_path / "cache.sqlite"))
    state.client.is_connected = lambda: True
    state.last_message_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    state.from_cache = False
    assert state.describe()["mode"] == "live"
    state.client.is_connected = lambda: False
    assert state.describe()["mode"] == "offline"


def test_selected_engine_stays_stale_when_other_engine_is_live() -> None:
    state = DashboardState(load_config())
    state.client.is_connected = lambda: True
    state.ingest("telemetry", "FD001-099", {"cycle": 40, "sensors": {}})
    state.last_message_by_engine["FD001-099"] = (
        datetime.now(UTC) - timedelta(seconds=10)
    ).isoformat()
    state.ingest("telemetry", "FD001-100", {"cycle": 50, "sensors": {}})
    assert state.snapshot("FD001-099")["mode"] == "stale"
    assert state.snapshot("FD001-100")["mode"] == "live"
    assert state.snapshot("FD001-001")["mode"] == "stale"
