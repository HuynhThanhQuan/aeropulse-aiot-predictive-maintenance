from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from aiot_pm.dashboard import app
from aiot_pm.fleet import (
    LatestPrediction,
    SeriesPoint,
    build_insights,
    build_snapshot,
    build_trend,
    ranking_limit,
    warning_threshold,
)


def record(engine_id: str, probability: float, *, age_seconds: float = 1, cycle: int = 40) -> LatestPrediction:
    return LatestPrediction(
        engine_id=engine_id,
        timestamp=datetime(2026, 10, 21, tzinfo=UTC) - timedelta(seconds=age_seconds),
        failure_probability=probability,
        cycle=cycle,
        latency_ms=0.2,
        predicted_class=int(probability >= 0.62),
        risk_level="high" if probability >= 0.62 else "watch" if probability >= 0.434 else "normal",
    )


def snapshot_for(records: list[LatestPrediction]):
    return build_snapshot(
        records,
        now=datetime(2026, 10, 21, tzinfo=UTC),
        alert_threshold=0.62,
        watch_threshold=warning_threshold(0.62),
        stale_after_seconds=60,
        lookback="24h",
        refresh_seconds=10,
    )


def test_warning_band_matches_edge_rule() -> None:
    assert warning_threshold(0.62) == max(0.25, 0.62 * 0.70)


def test_distribution_counts_each_engine_once_and_sums_to_total() -> None:
    snap = snapshot_for(
        [
            record("FD001-095", 0.10),
            record("FD001-096", 0.50),
            record("FD001-097", 0.70),
            record("FD001-098", 0.91),
            record("FD001-099", 0.20, age_seconds=90),
            record("FD001-100", 0.95, age_seconds=120),
        ]
    )
    distribution = snap["health_distribution"]
    assert distribution["normal"] == 1
    assert distribution["warning"] == 1
    assert distribution["critical"] == 2
    assert distribution["unknown"] == 2
    assert sum(distribution[key] for key in ("normal", "warning", "critical", "unknown")) == distribution["total"]
    assert distribution["total"] == 6
    assert snap["summary"]["reporting_engines"] == 4
    assert snap["summary"]["reporting_percent"] == 66.7
    assert snap["summary"]["stale_engines"] == 2
    assert snap["summary"]["critical_engines"] == 2
    assert snap["summary"]["risk_exposure_percent"] == 50.0
    assert [engine["engine_id"] for engine in snap["ranking"]] == ["FD001-098", "FD001-097", "FD001-096", "FD001-095"]
    assert "FD001-100" not in {engine["engine_id"] for engine in snap["ranking"]}
    assert sum(item["count"] for item in snap["risk_distribution"]["bins"]) == 4


def test_ranking_limit_grows_after_the_six_engine_demo() -> None:
    assert ranking_limit(6) == 5
    assert ranking_limit(7) == 10


def test_stale_engine_is_unknown_and_keeps_last_known_health() -> None:
    snap = snapshot_for([record("FD001-098", 0.9, age_seconds=61)])
    engine = snap["engines"][0]
    assert engine["health"] == "unknown"
    assert engine["connectivity"] == "stale"
    assert engine["last_known_health"] == "critical"
    assert snap["summary"]["insights"] == [
        "Fleet reporting coverage is 0.0%.",
        "No engine has reported within the freshness window.",
        "One engine has not reported new data for more than 60 seconds.",
    ]


def test_empty_fleet_says_insufficient_data() -> None:
    assert build_insights(
        total=0,
        reporting=0,
        reporting_percent=0,
        above=0,
        stale=0,
        stale_after_seconds=60,
        top_engine=None,
        top_percent=None,
    ) == ["Insufficient data"]


def test_cycle_reset_starts_a_new_segment() -> None:
    start = datetime(2026, 10, 21, tzinfo=UTC)
    points = [
        SeriesPoint("FD001-098", start, 0.4, 10),
        SeriesPoint("FD001-098", start + timedelta(seconds=2), 0.5, 11),
        SeriesPoint("FD001-098", start + timedelta(seconds=4), 0.1, 1),
    ]
    trend = build_trend(points, ["FD001-098"], window="15m", top=3, alert_threshold=0.62)
    segments = [point["segment"] for point in trend["series"][0]["points"]]
    assert segments == [0, 0, 1]


def test_fleet_page_and_api_do_not_require_a_live_database(monkeypatch) -> None:
    snap = snapshot_for([record("FD001-098", 0.8), record("FD001-099", 0.2, age_seconds=90)])

    class FakeFleet:
        def summary(self):
            return snap["summary"]

        def engines(self):
            return {
                "engines": snap["engines"],
                "total_engines": 2,
                "health_distribution": snap["health_distribution"],
                "ranking": snap["ranking"],
                "ranking_limit": snap["ranking_limit"],
                "contributing_reporting_engines": snap["contributing_reporting_engines"],
            }

        def distribution(self):
            return snap["risk_distribution"]

        def trend(self, window, top):
            return build_trend([], ["FD001-098"], window=window, top=top, alert_threshold=0.62)

        def engine(self, engine_id):
            return next((engine for engine in snap["engines"] if engine["engine_id"] == engine_id), None)

    monkeypatch.setattr("aiot_pm.dashboard.fleet_service", lambda: FakeFleet())
    with TestClient(app) as client:
        page = client.get("/fleet")
        assert page.status_code == 200
        assert "General Dashboard" in page.text
        assert "INFLUXDB_TOKEN" not in page.text
        summary = client.get("/api/fleet/summary")
        assert summary.status_code == 200
        assert summary.json()["critical_engines"] == 1
        missing = client.get("/api/fleet/engines/FD001-001")
        assert missing.status_code == 404
        trend = client.get("/api/fleet/risk-trend?window=1h&top=5")
        assert trend.status_code == 200
        assert trend.json()["window"] == "1h"
        home = client.get("/")
        assert 'href="/fleet"' in home.text
