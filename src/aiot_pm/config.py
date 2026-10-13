from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "config.yaml"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else DEFAULT_CONFIG
    with config_path.open("r", encoding="utf-8") as stream:
        config = yaml.safe_load(stream)

    config["mqtt"]["host"] = os.getenv("MQTT_HOST", config["mqtt"]["host"])
    config["mqtt"]["port"] = int(os.getenv("MQTT_PORT", config["mqtt"]["port"]))
    config["influxdb"]["url"] = os.getenv("INFLUXDB_URL", config["influxdb"]["url"])
    config["influxdb"]["org"] = os.getenv("INFLUXDB_ORG", config["influxdb"]["org"])
    config["influxdb"]["bucket"] = os.getenv(
        "INFLUXDB_BUCKET", config["influxdb"]["bucket"]
    )
    config["influxdb"]["token"] = os.getenv(
        "INFLUXDB_TOKEN", config["influxdb"]["token"]
    )
    config["edge"]["model_path"] = os.getenv("MODEL_PATH", config["edge"]["model_path"])
    config["edge"]["metadata_path"] = os.getenv(
        "METADATA_PATH", config["edge"]["metadata_path"]
    )
    return config


def project_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path
