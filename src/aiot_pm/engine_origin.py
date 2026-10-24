"""Resolve replay provenance against the deployed model's split metadata."""

import json
import os
import re

from .config import load_config, project_path


def dataset_split(engine_id: str | None) -> str:
    if not engine_id:
        return "Unknown"
    if re.fullmatch(r"FD001-TEST-(00[1-9]|0[1-9][0-9]|100)", engine_id):
        return "Test"
    match = re.fullmatch(r"FD001-(\d{3})", engine_id)
    if not match:
        return "Unknown"
    try:
        path = project_path(load_config()["edge"]["metadata_path"])
        metadata = json.loads(path.read_text())
    except (OSError, ValueError):
        return "Unknown"
    number = int(match[1])
    if number in metadata.get("train_engine_ids", []):
        return "Train"
    if number in metadata.get("validation_engine_ids", []):
        return "Validation"
    return "Unknown"


def is_displayed_engine(engine_id: str) -> bool:
    """Hide retired replay engines without deleting their stored observations."""
    excluded = os.getenv("DASHBOARD_EXCLUDED_ENGINES", "").split(",")
    return engine_id not in {item.strip() for item in excluded if item.strip()}
