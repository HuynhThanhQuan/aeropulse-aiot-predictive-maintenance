"""Read-only fleet snapshot built from the latest InfluxDB prediction per engine."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any

from .config import ROOT, load_config, project_path
from .engine_origin import is_displayed_engine

LOGGER = logging.getLogger(__name__)
QUERY_DIR = ROOT / "dashboard" / "queries"
LOOKBACK_PATTERN = re.compile(r"^\d+(m|h|d)$")
WINDOWS = {"15m": "15m", "30m": "30m", "1h": "1h"}
HEALTH_KEYS = ("normal", "warning", "critical", "unknown")
BINS = (
    ("0–20%", 0.0, 0.2),
    ("20–40%", 0.2, 0.4),
    ("40–60%", 0.4, 0.6),
    ("60–80%", 0.6, 0.8),
    ("80–100%", 0.8, 1.01),
)
REGISTRY_NOTE = (
    "Total engines are those with a prediction in the lookback window. "
    "Retired engines configured in DASHBOARD_EXCLUDED_ENGINES are excluded."
)
REPLAY_NOTE = (
    "A sudden drop is a simulator replay when cycle restarts. "
    "It is not treated as maintenance or recovery."
)


class FleetDataError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class LatestPrediction:
    engine_id: str
    timestamp: datetime
    failure_probability: float
    cycle: int | None
    latency_ms: float | None
    predicted_class: int | None
    risk_level: str | None


@dataclass(frozen=True)
class SeriesPoint:
    engine_id: str
    timestamp: datetime
    failure_probability: float
    cycle: int | None


def warning_threshold(alert_threshold: float) -> float:
    # Same watch band the edge service already publishes as risk_level=watch.
    return max(0.25, alert_threshold * 0.70)


def load_alert_threshold(config: dict[str, Any]) -> float:
    metadata_path = project_path(config["edge"]["metadata_path"])
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FleetDataError("Model metadata is not available") from exc
    return float(metadata["decision_threshold"])


def ranking_limit(total_engines: int) -> int:
    return 5 if total_engines <= 6 else 10


def health_from_probability(probability: float, alert_threshold: float, watch_threshold: float) -> str:
    if probability >= alert_threshold:
        return "critical"
    if probability >= watch_threshold:
        return "warning"
    return "normal"


def classify_engine(
    record: LatestPrediction,
    *,
    now: datetime,
    stale_after_seconds: float,
    alert_threshold: float,
    watch_threshold: float,
) -> dict[str, Any]:
    timestamp = record.timestamp if record.timestamp.tzinfo else record.timestamp.replace(tzinfo=UTC)
    age = max(0.0, (now - timestamp).total_seconds())
    stale = age >= stale_after_seconds
    last_known = health_from_probability(
        record.failure_probability, alert_threshold, watch_threshold
    )
    return {
        "engine_id": record.engine_id,
        "timestamp": timestamp.isoformat(),
        "age_seconds": round(age, 1),
        "failure_probability": record.failure_probability,
        "failure_percent": round(record.failure_probability * 100, 1),
        "cycle": record.cycle,
        "latency_ms": record.latency_ms,
        "predicted_class": record.predicted_class,
        "risk_level": record.risk_level,
        "last_known_health": last_known,
        "connectivity": "stale" if stale else "reporting",
        "health": "unknown" if stale else last_known,
    }


def bin_index(probability: float) -> int:
    for index, (_, lower, upper) in enumerate(BINS):
        if lower <= probability < upper:
            return index
    return len(BINS) - 1


def split_segments(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segment = 0
    previous_cycle: int | None = None
    rendered: list[dict[str, Any]] = []
    for point in points:
        cycle = point.get("cycle")
        if previous_cycle is not None and cycle is not None and cycle < previous_cycle - 5:
            segment += 1
        rendered.append({**point, "segment": segment})
        if cycle is not None:
            previous_cycle = cycle
    return rendered


def build_insights(
    *,
    total: int,
    reporting: int,
    reporting_percent: float,
    above: int,
    stale: int,
    stale_after_seconds: float,
    top_engine: str | None,
    top_percent: float | None,
) -> list[str]:
    if total == 0:
        return ["Insufficient data"]
    lines = [f"Fleet reporting coverage is {reporting_percent:.1f}%."]
    if reporting == 0:
        lines.append("No engine has reported within the freshness window.")
    else:
        lines.append(
            f"{above} of {reporting} reporting engines are at or above the alert threshold."
        )
    if top_engine and top_percent is not None:
        lines.append(
            f"{top_engine} has the highest current failure probability ({top_percent:.1f}%)."
        )
    if stale == 1:
        lines.append(
            f"One engine has not reported new data for more than {stale_after_seconds:.0f} seconds."
        )
    elif stale > 1:
        lines.append(
            f"{stale} engines have not reported new data for more than {stale_after_seconds:.0f} seconds."
        )
    return lines


def build_snapshot(
    records: list[LatestPrediction],
    *,
    now: datetime,
    alert_threshold: float,
    watch_threshold: float,
    stale_after_seconds: float,
    lookback: str,
    refresh_seconds: int,
) -> dict[str, Any]:
    engines = [
        classify_engine(
            record,
            now=now,
            stale_after_seconds=stale_after_seconds,
            alert_threshold=alert_threshold,
            watch_threshold=watch_threshold,
        )
        for record in records
    ]
    engines.sort(key=lambda engine: engine["engine_id"])
    total = len(engines)
    reporting = [engine for engine in engines if engine["connectivity"] == "reporting"]
    above = [
        engine for engine in reporting if engine["failure_probability"] >= alert_threshold
    ]
    distribution = {key: 0 for key in HEALTH_KEYS}
    for engine in engines:
        distribution[engine["health"]] += 1
    limit = ranking_limit(total)
    ranking = sorted(reporting, key=lambda engine: engine["failure_probability"], reverse=True)[
        :limit
    ]
    bins = [
        {"label": label, "min": lower, "max": upper if upper <= 1 else 1.0, "count": 0}
        for label, lower, upper in BINS
    ]
    for engine in reporting:
        bins[bin_index(engine["failure_probability"])]["count"] += 1
    reporting_count = len(reporting)
    reporting_percent = (100.0 * reporting_count / total) if total else 0.0
    exposure = (100.0 * len(above) / reporting_count) if reporting_count else 0.0
    top = ranking[0] if ranking else None
    limited = total < 20
    summary = {
        "generated_at": now.isoformat(),
        "lookback": lookback,
        "stale_after_seconds": stale_after_seconds,
        "alert_threshold": alert_threshold,
        "warning_threshold": watch_threshold,
        "refresh_seconds": refresh_seconds,
        "total_engines": total,
        "reporting_engines": reporting_count,
        "reporting_percent": round(reporting_percent, 1),
        "critical_engines": distribution["critical"],
        "stale_engines": sum(1 for engine in engines if engine["connectivity"] == "stale"),
        "above_threshold": len(above),
        "risk_exposure_percent": round(exposure, 1),
        "insufficient_data": total == 0,
        "registry_note": REGISTRY_NOTE,
        "insights": build_insights(
            total=total,
            reporting=reporting_count,
            reporting_percent=reporting_percent,
            above=len(above),
            stale=sum(1 for engine in engines if engine["connectivity"] == "stale"),
            stale_after_seconds=stale_after_seconds,
            top_engine=top["engine_id"] if top else None,
            top_percent=top["failure_percent"] if top else None,
        ),
    }
    return {
        "summary": summary,
        "engines": engines,
        "health_distribution": {**distribution, "total": total},
        "ranking": ranking,
        "ranking_limit": limit,
        "contributing_reporting_engines": reporting_count,
        "risk_distribution": {
            "bins": bins,
            "reporting_engines": reporting_count,
            "limited_sample": limited,
            "note": (
                "Each reporting engine is counted once. This fleet is a limited sample."
                if limited
                else "Each reporting engine is counted once."
            ),
        },
    }


def build_trend(
    points: list[SeriesPoint],
    ranking_ids: list[str],
    *,
    window: str,
    top: int,
    alert_threshold: float,
) -> dict[str, Any]:
    selected = ranking_ids[:top]
    grouped: dict[str, list[SeriesPoint]] = {engine_id: [] for engine_id in selected}
    for point in points:
        if point.engine_id in grouped:
            grouped[point.engine_id].append(point)
    series = []
    for engine_id in selected:
        ordered = sorted(grouped[engine_id], key=lambda point: point.timestamp)
        rendered = split_segments(
            [
                {
                    "timestamp": point.timestamp.isoformat(),
                    "failure_probability": point.failure_probability,
                    "failure_percent": round(point.failure_probability * 100, 1),
                    "cycle": point.cycle,
                }
                for point in ordered
            ]
        )
        series.append({"engine_id": engine_id, "points": rendered})
    return {
        "window": window,
        "top": top,
        "alert_threshold": alert_threshold,
        "engines": selected,
        "series": series,
        "replay_note": REPLAY_NOTE,
    }


def render_query(name: str, values: dict[str, str]) -> str:
    path = QUERY_DIR / name
    template = Template(path.read_text(encoding="utf-8"))
    return template.substitute(values)


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def parse_latest(tables: list[Any]) -> list[LatestPrediction]:
    merged: dict[str, dict[str, Any]] = {}
    for table in tables:
        for record in table.records:
            engine_id = record.values.get("engine_id")
            if not engine_id:
                continue
            row = merged.setdefault(engine_id, {"engine_id": str(engine_id)})
            field = record.get_field()
            row[field] = record.get_value()
            row["risk_level"] = record.values.get("risk_level") or row.get("risk_level")
            if field == "failure_probability":
                row["timestamp"] = record.get_time()
    parsed: list[LatestPrediction] = []
    for row in merged.values():
        probability = _as_float(row.get("failure_probability"))
        timestamp = row.get("timestamp")
        if probability is None or timestamp is None:
            continue
        parsed.append(
            LatestPrediction(
                engine_id=row["engine_id"],
                timestamp=timestamp,
                failure_probability=probability,
                cycle=_as_int(row.get("cycle")),
                latency_ms=_as_float(row.get("latency_ms")),
                predicted_class=_as_int(row.get("predicted_class")),
                risk_level=row.get("risk_level"),
            )
        )
    return parsed


def parse_series(tables: list[Any]) -> list[SeriesPoint]:
    merged: dict[tuple[str, datetime], dict[str, Any]] = {}
    for table in tables:
        for record in table.records:
            engine_id = record.values.get("engine_id")
            timestamp = record.get_time()
            if not engine_id or timestamp is None:
                continue
            key = (str(engine_id), timestamp)
            row = merged.setdefault(key, {"engine_id": str(engine_id), "timestamp": timestamp})
            row[record.get_field()] = record.get_value()
    points: list[SeriesPoint] = []
    for row in merged.values():
        probability = _as_float(row.get("failure_probability"))
        if probability is None:
            continue
        points.append(
            SeriesPoint(
                engine_id=row["engine_id"],
                timestamp=row["timestamp"],
                failure_probability=probability,
                cycle=_as_int(row.get("cycle")),
            )
        )
    return points


class InfluxFleetReader:
    def __init__(self, url: str, token: str, org: str, bucket: str, lookback: str) -> None:
        self.url = url
        self.token = token
        self.org = org
        self.bucket = bucket
        self.lookback = lookback

    def latest(self) -> list[LatestPrediction]:
        flux = render_query(
            "latest_predictions.flux", {"bucket": self.bucket, "lookback": self.lookback}
        )
        return parse_latest(self._query(flux))

    def series(self, window: str) -> list[SeriesPoint]:
        flux = render_query(
            "prediction_series.flux", {"bucket": self.bucket, "window": window}
        )
        return parse_series(self._query(flux))

    def _query(self, flux: str) -> list[Any]:
        if not self.token:
            raise FleetDataError("InfluxDB token is not configured")
        try:
            from influxdb_client import InfluxDBClient

            with InfluxDBClient(
                url=self.url, token=self.token, org=self.org, timeout=20_000
            ) as client:
                return client.query_api().query(flux, org=self.org)
        except FleetDataError:
            raise
        except Exception as exc:
            LOGGER.exception("Influx fleet query failed")
            raise FleetDataError("InfluxDB query failed") from exc


class FleetService:
    def __init__(
        self,
        reader: InfluxFleetReader,
        *,
        alert_threshold: float,
        watch_threshold: float,
        stale_after_seconds: float,
        lookback: str,
        refresh_seconds: int,
    ) -> None:
        self.reader = reader
        self.alert_threshold = alert_threshold
        self.watch_threshold = watch_threshold
        self.stale_after_seconds = stale_after_seconds
        self.lookback = lookback
        self.refresh_seconds = refresh_seconds
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] | None = None
        self._snapshot_at = 0.0

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> FleetService:
        loaded = config or load_config()
        influx = loaded["influxdb"]
        lookback = os.getenv("FLEET_LOOKBACK", "24h")
        if not LOOKBACK_PATTERN.fullmatch(lookback):
            raise FleetDataError("FLEET_LOOKBACK must look like 24h, 30m, or 7d")
        alert = load_alert_threshold(loaded)
        return cls(
            InfluxFleetReader(
                url=influx["url"],
                token=influx["token"],
                org=influx["org"],
                bucket=influx["bucket"],
                lookback=lookback,
            ),
            alert_threshold=alert,
            watch_threshold=warning_threshold(alert),
            stale_after_seconds=float(os.getenv("FLEET_STALE_AFTER_S", "60")),
            lookback=lookback,
            refresh_seconds=int(os.getenv("FLEET_REFRESH_S", "10")),
        )

    def snapshot(self, *, now: datetime | None = None) -> dict[str, Any]:
        with self._lock:
            age = time.monotonic() - self._snapshot_at
            if now is None and self._snapshot is not None and age < 2:
                return self._snapshot
            built = build_snapshot(
                [record for record in self.reader.latest() if is_displayed_engine(record.engine_id)],
                now=now or datetime.now(UTC),
                alert_threshold=self.alert_threshold,
                watch_threshold=self.watch_threshold,
                stale_after_seconds=self.stale_after_seconds,
                lookback=self.lookback,
                refresh_seconds=self.refresh_seconds,
            )
            if now is None:
                self._snapshot = built
                self._snapshot_at = time.monotonic()
            return built

    def summary(self) -> dict[str, Any]:
        return self.snapshot()["summary"]

    def engines(self) -> dict[str, Any]:
        snap = self.snapshot()
        return {
            "engines": snap["engines"],
            "total_engines": snap["summary"]["total_engines"],
            "health_distribution": snap["health_distribution"],
            "ranking": snap["ranking"],
            "ranking_limit": snap["ranking_limit"],
            "contributing_reporting_engines": snap["contributing_reporting_engines"],
        }

    def distribution(self) -> dict[str, Any]:
        return self.snapshot()["risk_distribution"]

    def engine(self, engine_id: str) -> dict[str, Any] | None:
        for engine in self.engines()["engines"]:
            if engine["engine_id"] == engine_id:
                return engine
        return None

    def trend(self, window: str, top: int) -> dict[str, Any]:
        if window not in WINDOWS:
            raise FleetDataError("window must be 15m, 30m, or 1h")
        if top not in {3, 5}:
            raise FleetDataError("top must be 3 or 5")
        ranking_ids = [engine["engine_id"] for engine in self.engines()["ranking"]]
        points = self.reader.series(WINDOWS[window])
        return build_trend(
            points,
            ranking_ids,
            window=window,
            top=top,
            alert_threshold=self.alert_threshold,
        )


def query_path(name: str) -> Path:
    return QUERY_DIR / name
