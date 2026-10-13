from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Telemetry:
    engine_id: str
    cycle: int
    sensors: dict[str, float]
    operational_settings: dict[str, float]
    source: str = "NASA C-MAPSS FD001 replay"
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = self.timestamp or utc_now()
        return payload


@dataclass(frozen=True)
class Prediction:
    engine_id: str
    cycle: int
    failure_probability: float
    predicted_class: int
    risk_level: str
    model: str
    latency_ms: float
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = self.timestamp or utc_now()
        return payload


def topic_engine_id(topic: str) -> str:
    parts = topic.split("/")
    if len(parts) != 4 or parts[:2] != ["aiot", "engine"]:
        raise ValueError(f"Unexpected topic: {topic}")
    return parts[2]


def engine_topic(engine_id: str, kind: str) -> str:
    if "/" in engine_id or not engine_id:
        raise ValueError("engine_id must be a non-empty MQTT topic segment")
    return f"aiot/engine/{engine_id}/{kind}"
