"""Create or update the InfluxDB dashboard GENERAL_DASHBOARD.

The script reads general_dashboard.json beside this file, which is the InfluxDB
2.7 dashboard export, and applies it with the templates API. A second run
updates the same dashboard instead of creating another copy.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_PATH = Path(__file__).resolve().parent / "general_dashboard.json"
DASHBOARD_NAME = "GENERAL_DASHBOARD"
TEMPLATE_NAME = "general-dashboard"
STACK_NAME = "GENERAL_DASHBOARD"

KIND_TO_VIEW = {
    "Single_Stat": "single-stat",
    "Table": "table",
    "Mosaic": "mosaic",
    "Xy": "xy",
}
PREDICTION_FIELDS = {"failure_probability", "predicted_class", "cycle", "latency_ms"}
BUILTIN_VARIABLES = {"timeRangeStart", "timeRangeStop", "windowPeriod"}
BUCKET_PATTERN = re.compile(r'from\(bucket:\s*"[^"]+"\)')
STALE_DURATION = re.compile(r"experimental\.subDuration\(d:\s*(\d+)s")
STALE_ASSIGNMENT = re.compile(r"(staleAfter|staleSeconds)\s*=\s*(\d+)")
THRESHOLD_LITERAL = re.compile(r"0\.620000")
FIELD_LITERAL = re.compile(r'_field\s*==\s*"([^"]+)"')
MEASUREMENT_LITERAL = re.compile(r'_measurement\s*==\s*"([^"]+)"')
VARIABLE_LITERAL = re.compile(r"\bv\.([A-Za-z_][A-Za-z0-9_]*)")
DISPLAY_EXTRA = {"—", "–"}


class DashboardInstallError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def load_env_file() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require_settings() -> dict[str, str]:
    load_env_file()
    required = ("INFLUXDB_URL", "INFLUXDB_ORG", "INFLUXDB_BUCKET", "INFLUXDB_TOKEN")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        joined = ", ".join(missing)
        raise DashboardInstallError(
            f"Missing {joined}. Set them in the environment or in .env at the repository root. "
            "See .env.example."
        )
    bucket = os.environ["INFLUXDB_BUCKET"].strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", bucket):
        raise DashboardInstallError(
            "INFLUXDB_BUCKET may contain only letters, numbers, underscores, and hyphens."
        )
    url = os.environ["INFLUXDB_URL"].strip().rstrip("/")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DashboardInstallError("INFLUXDB_URL must be an http or https URL.")
    stale = os.environ.get("FLEET_STALE_AFTER_S", "60").strip()
    if not re.fullmatch(r"\d+", stale) or int(stale) <= 0:
        raise DashboardInstallError("FLEET_STALE_AFTER_S must be a positive whole number of seconds.")
    return {
        "url": url,
        "org": os.environ["INFLUXDB_ORG"].strip(),
        "bucket": bucket,
        "token": os.environ["INFLUXDB_TOKEN"].strip(),
        "stale_seconds": stale,
    }


def load_decision_threshold() -> float | None:
    configured = os.environ.get("METADATA_PATH", "models/metadata.json").strip()
    path = Path(configured)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        return None
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
        return float(metadata["decision_threshold"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise DashboardInstallError(f"Could not read decision_threshold from {path}: {exc}") from exc


def load_template() -> list[dict[str, Any]]:
    if not DASHBOARD_PATH.is_file():
        raise DashboardInstallError(f"Dashboard file not found: {DASHBOARD_PATH}")
    try:
        document = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DashboardInstallError(f"Dashboard JSON is not valid: {exc}") from exc
    if isinstance(document, dict):
        document = [document]
    if not isinstance(document, list) or not document:
        raise DashboardInstallError("Dashboard JSON must contain one Dashboard resource.")
    return document


def assert_display_text(value: str, label: str) -> None:
    unexpected = sorted({char for char in value if ord(char) > 127 and char not in DISPLAY_EXTRA})
    if unexpected:
        raise DashboardInstallError(f"{label} is not English display text.")


def prepare_dashboard(
    resources: list[dict[str, Any]],
    *,
    bucket: str,
    stale_seconds: str,
    threshold: float | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    dashboards = [item for item in resources if item.get("kind") == "Dashboard"]
    if len(dashboards) != 1:
        raise DashboardInstallError("The export must contain exactly one Dashboard resource.")
    dashboard = dashboards[0]
    metadata = dashboard.setdefault("metadata", {})
    metadata["name"] = TEMPLATE_NAME
    spec = dashboard.setdefault("spec", {})
    spec["name"] = DASHBOARD_NAME
    description = str(spec.get("description") or "")
    assert_display_text(description, "Dashboard description")
    charts = spec.get("charts")
    if not isinstance(charts, list) or not charts:
        raise DashboardInstallError("The dashboard export does not contain any cells.")

    threshold_text = f"{threshold:.6f}" if threshold is not None else None
    variables: set[str] = set()
    for chart in charts:
        name = str(chart.get("name") or "")
        if not name:
            raise DashboardInstallError("A dashboard cell is missing a name.")
        assert_display_text(name, f"Cell {name}")
        kind = chart.get("kind")
        if kind not in KIND_TO_VIEW:
            raise DashboardInstallError(f"Cell {name} has unsupported chart kind {kind!r}.")
        if "width" not in chart or "height" not in chart:
            raise DashboardInstallError(f"Cell {name} is missing a width or height.")
        for option in chart.get("fieldOptions") or []:
            assert_display_text(str(option.get("displayName") or ""), f"Column on {name}")
        queries = chart.get("queries") or []
        if not queries:
            raise DashboardInstallError(f"Cell {name} has no Flux query.")
        for entry in queries:
            query = str(entry.get("query") or "")
            if not query.strip():
                raise DashboardInstallError(f"Cell {name} has an empty Flux query.")
            query = BUCKET_PATTERN.sub(f'from(bucket: "{bucket}")', query)
            query = STALE_DURATION.sub(
                f"experimental.subDuration(d: {stale_seconds}s",
                query,
            )
            query = STALE_ASSIGNMENT.sub(
                lambda match, seconds=stale_seconds: f"{match.group(1)} = {seconds}",
                query,
            )
            if threshold_text is not None:
                query = THRESHOLD_LITERAL.sub(threshold_text, query)
            entry["query"] = query
            measurements = set(MEASUREMENT_LITERAL.findall(query))
            if measurements != {"prediction"}:
                raise DashboardInstallError(
                    f"Cell {name} must query measurement prediction. Found {sorted(measurements)}."
                )
            fields = set(FIELD_LITERAL.findall(query))
            unknown = fields - PREDICTION_FIELDS
            if unknown:
                raise DashboardInstallError(
                    f"Cell {name} queries unknown prediction fields: {sorted(unknown)}."
                )
            variables.update(VARIABLE_LITERAL.findall(query))

    unknown_variables = variables - BUILTIN_VARIABLES
    if unknown_variables:
        raise DashboardInstallError(
            "The dashboard uses custom variables that are not in the export: "
            + ", ".join(sorted(unknown_variables))
        )
    info = {
        "charts": len(charts),
        "variables": sorted(variables),
        "threshold": threshold_text or "0.620000",
        "threshold_source": "metadata" if threshold_text else "dashboard JSON",
    }
    return resources, info


def query_text(properties: dict[str, Any]) -> str:
    parts = []
    for query in properties.get("queries") or []:
        parts.append(str(query.get("text") or query.get("query") or ""))
    return "\n".join(parts)


def decimal_places(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and "digits" in value:
        return {
            "isEnforced": bool(value.get("isEnforced", True)),
            "digits": int(value["digits"]),
        }
    if isinstance(value, int):
        return {"isEnforced": True, "digits": value}
    return {"isEnforced": False, "digits": 2}


def flux_query(text: str) -> dict[str, Any]:
    return {
        "name": "",
        "text": text,
        "editMode": "advanced",
        "builderConfig": {
            "buckets": [],
            "tags": [],
            "functions": [{"name": "mean"}],
            "aggregateWindow": {"period": "auto", "fillValues": False},
        },
    }


def colors_for(chart: dict[str, Any]) -> list[dict[str, Any]]:
    rendered = []
    for index, color in enumerate(chart.get("colors") or []):
        item = {
            "id": color.get("id") or f"color-{index}",
            "type": color.get("type") or "scale",
            "hex": color.get("hex") or "#31C0F6",
            "name": color.get("name") or "Nineteen Eighty Four",
            "value": color.get("value", 0),
        }
        rendered.append(item)
    return rendered


def field_options(chart: dict[str, Any]) -> list[dict[str, Any]]:
    rendered = []
    for option in chart.get("fieldOptions") or []:
        internal = option.get("internalName") or option.get("fieldName") or option.get("displayName")
        if not internal:
            continue
        rendered.append(
            {
                "internalName": internal,
                "displayName": option.get("displayName") or internal,
                "visible": bool(option.get("visible", True)),
            }
        )
    return rendered


def table_options(chart: dict[str, Any]) -> dict[str, Any]:
    raw = chart.get("tableOptions") or {}
    options: dict[str, Any] = {
        "verticalTimeAxis": bool(raw.get("verticalTimeAxis", True)),
        "wrapping": raw.get("wrapping") or "truncate",
        "fixFirstColumn": bool(raw.get("fixFirstColumn", False)),
    }
    sort_by = raw.get("sortBy")
    if isinstance(sort_by, str) and sort_by:
        options["sortBy"] = {
            "internalName": sort_by,
            "displayName": sort_by,
            "visible": True,
        }
    elif isinstance(sort_by, dict):
        options["sortBy"] = sort_by
    return options


def axes_for(chart: dict[str, Any]) -> dict[str, Any]:
    axes: dict[str, Any] = {}
    for axis in chart.get("axes") or []:
        name = axis.get("name")
        if name not in {"x", "y"}:
            continue
        axes[name] = {
            "bounds": ["", ""],
            "label": axis.get("label") or "",
            "prefix": axis.get("prefix") or "",
            "suffix": axis.get("suffix") or "",
            "base": str(axis.get("base") or "10"),
            "scale": axis.get("scale") or "linear",
        }
    for name in ("x", "y"):
        axes.setdefault(
            name,
            {
                "bounds": ["", ""],
                "label": "",
                "prefix": "",
                "suffix": "",
                "base": "10",
                "scale": "linear",
            },
        )
    return axes


def static_legend(chart: dict[str, Any]) -> dict[str, Any] | None:
    raw = chart.get("staticLegend") or {}
    if not raw:
        return None
    legend = {
        "colorizeRows": bool(raw.get("colorizeRows", chart.get("legendColorizeRows", False))),
        "opacity": raw.get("opacity", chart.get("legendOpacity", 1)),
        "orientationThreshold": raw.get(
            "orientationThreshold",
            chart.get("legendOrientationThreshold", 0),
        ),
    }
    if "widthRatio" in raw:
        legend["widthRatio"] = raw["widthRatio"]
    if "heightRatio" in raw:
        legend["heightRatio"] = raw["heightRatio"]
    return legend


def view_properties(chart: dict[str, Any]) -> dict[str, Any]:
    kind = chart["kind"]
    view_type = KIND_TO_VIEW[kind]
    queries = [flux_query(entry["query"]) for entry in chart["queries"]]
    properties: dict[str, Any] = {
        "type": view_type,
        "shape": "chronograf-v2",
        "queries": queries,
        "colors": colors_for(chart),
        "note": chart.get("note") or "",
        "showNoteWhenEmpty": bool(chart.get("noteOnEmpty", False)),
    }
    if "decimalPlaces" in chart:
        properties["decimalPlaces"] = decimal_places(chart["decimalPlaces"])
    if kind == "Single_Stat":
        properties["prefix"] = chart.get("prefix") or ""
        properties["suffix"] = chart.get("suffix") or ""
        properties["tickPrefix"] = chart.get("tickPrefix") or ""
        properties["tickSuffix"] = chart.get("tickSuffix") or ""
    elif kind == "Table":
        properties["fieldOptions"] = field_options(chart)
        properties["tableOptions"] = table_options(chart)
        properties["timeFormat"] = chart.get("timeFormat") or "YYYY-MM-DD HH:mm:ss"
        properties.setdefault("decimalPlaces", decimal_places(None))
    elif kind == "Xy":
        properties.update(
            {
                "axes": axes_for(chart),
                "geom": chart.get("geom") or "line",
                "xColumn": chart.get("xCol") or "_time",
                "yColumn": chart.get("yCol") or "_value",
                "position": chart.get("position") or "overlaid",
                "hoverDimension": chart.get("hoverDimension") or "auto",
                "legendColorizeRows": bool(chart.get("legendColorizeRows", False)),
                "legendOpacity": chart.get("legendOpacity", 1),
                "legendOrientationThreshold": chart.get("legendOrientationThreshold", 0),
                "shadeBelow": bool(chart.get("shade", False)),
            }
        )
        legend = static_legend(chart)
        if legend:
            properties["staticLegend"] = legend
    elif kind == "Mosaic":
        properties["colors"] = [
            str(color.get("hex") or "#31C0F6") for color in (chart.get("colors") or [])
        ] or ["#31C0F6", "#A500A5", "#FF7E27"]
        properties.update(
            {
                "xColumn": chart.get("xCol") or "_time",
                "ySeriesColumns": chart.get("ySeriesColumns") or ["engine_id"],
                "fillColumns": chart.get("fillColumns") or ["_value"],
                "legendColorizeRows": bool(chart.get("legendColorizeRows", True)),
                "legendOpacity": chart.get("legendOpacity", 1),
                "legendOrientationThreshold": chart.get("legendOrientationThreshold", 0),
            }
        )
    return properties


def geometry(chart: dict[str, Any]) -> dict[str, int]:
    return {
        "x": int(chart.get("xPos", 0)),
        "y": int(chart.get("yPos", 0)),
        "w": int(chart["width"]),
        "h": int(chart["height"]),
    }


class InfluxSession:
    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | list[Any] | None = None,
        *,
        content_type: str = "application/json",
        accept: str = "application/json",
    ) -> tuple[int, Any]:
        target = path if path.startswith("http") else self.url + path
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(target, data=payload, method=method)
        request.add_header("Authorization", f"Token {self.token}")
        request.add_header("Accept", accept)
        if payload is not None:
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            raise DashboardInstallError(
                f"InfluxDB {method} {urllib.parse.urlparse(target).path} returned {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise DashboardInstallError(
                f"Could not connect to InfluxDB at {self.url}: {exc.reason}"
            ) from exc
        if not raw:
            return status, None
        text = raw.decode("utf-8", errors="replace")
        if "json" in accept:
            try:
                return status, json.loads(text)
            except json.JSONDecodeError:
                return status, text
        return status, text

    def get(self, path: str) -> Any:
        _status, payload = self.request("GET", path)
        return payload

    def send(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        _status, payload = self.request(method, path, body)
        return payload


def check_version(session: InfluxSession) -> str:
    try:
        health = session.get("/health")
    except DashboardInstallError as exc:
        raise DashboardInstallError(
            f"{exc.message} Start InfluxDB before running this script."
        ) from exc
    version = str((health or {}).get("version") or "")
    if not version.startswith(("2", "v2")):
        raise DashboardInstallError(
            "This dashboard export uses the InfluxDB 2 template API. "
            f"The server at {session.url} reported version {version or 'unknown'}."
        )
    return version


def find_org_id(session: InfluxSession, org: str) -> str:
    payload = session.get("/api/v2/orgs?org=" + urllib.parse.quote(org))
    matches = [
        item for item in (payload or {}).get("orgs") or [] if item.get("name") == org
    ]
    if not matches:
        raise DashboardInstallError(
            f"InfluxDB organization {org!r} was not found. "
            "Check INFLUXDB_ORG. This script does not create an organization."
        )
    return str(matches[0]["id"])


def list_dashboards(session: InfluxSession, org_id: str) -> list[dict[str, Any]]:
    dashboards: list[dict[str, Any]] = []
    path: str | None = f"/api/v2/dashboards?orgID={urllib.parse.quote(org_id)}&limit=100"
    while path:
        payload = session.get(path)
        dashboards.extend((payload or {}).get("dashboards") or [])
        next_link = ((payload or {}).get("links") or {}).get("next")
        if not next_link:
            break
        path = next_link.removeprefix(session.url)
    return dashboards


def list_stacks(session: InfluxSession, org_id: str) -> list[dict[str, Any]]:
    payload = session.get(f"/api/v2/stacks?orgID={urllib.parse.quote(org_id)}")
    return list((payload or {}).get("stacks") or [])


def stack_dashboard_id(stack: dict[str, Any]) -> str | None:
    for resource in stack.get("resources") or []:
        if resource.get("kind") == "Dashboard" and resource.get("templateMetaName") == TEMPLATE_NAME:
            return str(resource.get("resourceID") or "")
    return None


def find_stack(stacks: list[dict[str, Any]]) -> dict[str, Any] | None:
    named = [stack for stack in stacks if stack.get("name") == STACK_NAME]
    if named:
        return named[0]
    for stack in stacks:
        if stack_dashboard_id(stack):
            return stack
    return None


def template_resources(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned = json.loads(json.dumps(resources))
    for resource in cleaned:
        for chart in (resource.get("spec") or {}).get("charts") or []:
            chart.pop("_bucket", None)
    return cleaned


def apply_template(
    session: InfluxSession,
    org_id: str,
    resources: list[dict[str, Any]],
    stack_id: str | None,
) -> str:
    body: dict[str, Any] = {
        "orgID": org_id,
        "dryRun": False,
        "templates": [{"contentType": "json", "contents": template_resources(resources)}],
    }
    if stack_id:
        body["stackID"] = stack_id
    payload = session.send("POST", "/api/v2/templates/apply", body)
    applied = str((payload or {}).get("stackID") or stack_id or "")
    if not applied:
        raise DashboardInstallError("InfluxDB applied the template but did not return a stack ID.")
    return applied


def name_stack(session: InfluxSession, stack_id: str) -> None:
    session.send(
        "PATCH",
        f"/api/v2/stacks/{urllib.parse.quote(stack_id)}",
        {
            "name": STACK_NAME,
            "description": "AeroPulse GENERAL_DASHBOARD import",
        },
    )


def dashboard_charts(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for resource in resources:
        if resource.get("kind") == "Dashboard":
            return list(resource["spec"]["charts"])
    return []


def cell_view(session: InfluxSession, dashboard_id: str, cell_id: str) -> dict[str, Any]:
    payload = session.get(
        f"/api/v2/dashboards/{urllib.parse.quote(dashboard_id)}/cells/{urllib.parse.quote(cell_id)}/view"
    )
    if not isinstance(payload, dict):
        raise DashboardInstallError(f"Cell {cell_id} did not return a view.")
    return payload


def create_cell(
    session: InfluxSession,
    dashboard_id: str,
    chart: dict[str, Any],
) -> dict[str, Any]:
    created = session.send(
        "POST",
        f"/api/v2/dashboards/{urllib.parse.quote(dashboard_id)}/cells",
        geometry(chart),
    )
    if not isinstance(created, dict) or not created.get("id"):
        raise DashboardInstallError(f"InfluxDB did not create the cell {chart['name']}.")
    return created


def update_cell_view(
    session: InfluxSession,
    dashboard_id: str,
    cell_id: str,
    chart: dict[str, Any],
    view_id: str | None,
) -> None:
    body: dict[str, Any] = {
        "name": chart["name"],
        "properties": view_properties(chart),
    }
    if view_id:
        body["id"] = view_id
    session.send(
        "PATCH",
        f"/api/v2/dashboards/{urllib.parse.quote(dashboard_id)}/cells/{urllib.parse.quote(cell_id)}/view",
        body,
    )
    session.send(
        "PATCH",
        f"/api/v2/dashboards/{urllib.parse.quote(dashboard_id)}/cells/{urllib.parse.quote(cell_id)}",
        geometry(chart),
    )


def reconcile_cells(
    session: InfluxSession,
    dashboard_id: str,
    charts: list[dict[str, Any]],
) -> int:
    dashboard = session.get(f"/api/v2/dashboards/{urllib.parse.quote(dashboard_id)}")
    cells = list((dashboard or {}).get("cells") or [])
    by_name: dict[str, dict[str, Any]] = {}
    unnamed: list[dict[str, Any]] = []
    for cell in cells:
        view = cell_view(session, dashboard_id, cell["id"])
        cell["_view"] = view
        name = str(view.get("name") or "")
        if name:
            by_name.setdefault(name, cell)
        else:
            unnamed.append(cell)

    used: set[str] = set()
    for chart in charts:
        cell = by_name.get(chart["name"])
        if cell is None and unnamed:
            cell = unnamed.pop(0)
        if cell is None:
            cell = create_cell(session, dashboard_id, chart)
            view_id = None
        else:
            view_id = str((cell.get("_view") or {}).get("id") or "") or None
        try:
            update_cell_view(session, dashboard_id, cell["id"], chart, view_id)
        except DashboardInstallError as exc:
            raise DashboardInstallError(f"Cell {chart['name']}: {exc.message}") from exc
        used.add(cell["id"])

    for cell in cells:
        if cell["id"] in used:
            continue
        session.request(
            "DELETE",
            f"/api/v2/dashboards/{urllib.parse.quote(dashboard_id)}/cells/"
            f"{urllib.parse.quote(cell['id'])}",
        )
    return len(charts)


def verify_dashboard(
    session: InfluxSession,
    dashboard_id: str,
    charts: list[dict[str, Any]],
) -> None:
    dashboard = session.get(f"/api/v2/dashboards/{urllib.parse.quote(dashboard_id)}")
    if (dashboard or {}).get("name") != DASHBOARD_NAME:
        raise DashboardInstallError("The installed dashboard name is not GENERAL_DASHBOARD.")
    cells = list((dashboard or {}).get("cells") or [])
    if len(cells) != len(charts):
        raise DashboardInstallError(
            f"Expected {len(charts)} cells on GENERAL_DASHBOARD, found {len(cells)}."
        )
    expected = {chart["name"]: chart for chart in charts}
    seen: set[str] = set()
    for cell in cells:
        view = cell_view(session, dashboard_id, cell["id"])
        name = str(view.get("name") or "")
        chart = expected.get(name)
        if chart is None:
            raise DashboardInstallError(f"Unexpected cell {name!r} on GENERAL_DASHBOARD.")
        seen.add(name)
        properties = view.get("properties") or {}
        if properties.get("type") != KIND_TO_VIEW[chart["kind"]]:
            raise DashboardInstallError(f"Cell {name} has the wrong visualization type.")
        stored = query_text(properties)
        if not stored.strip():
            raise DashboardInstallError(f"Cell {name} was created without a Flux query.")
        if f'from(bucket: "{chart["_bucket"]}")' not in stored:
            raise DashboardInstallError(f"Cell {name} does not query bucket {chart['_bucket']}.")
        position = {key: int(cell.get(key, -1)) for key in ("x", "y", "w", "h")}
        if position != geometry(chart):
            raise DashboardInstallError(f"Cell {name} does not match the exported layout.")
    missing = set(expected) - seen
    if missing:
        raise DashboardInstallError(f"Missing cells: {', '.join(sorted(missing))}.")


def prediction_status(session: InfluxSession, org: str, bucket: str) -> str:
    flux = (
        f'from(bucket: "{bucket}")\n'
        "  |> range(start: -24h)\n"
        '  |> filter(fn: (r) => r._measurement == "prediction" and '
        'r._field == "failure_probability")\n'
        "  |> group()\n"
        "  |> count()\n"
    )
    path = "/api/v2/query?org=" + urllib.parse.quote(org)
    try:
        _status, text = session.request(
            "POST",
            path,
            {"query": flux, "type": "flux"},
            accept="application/csv",
        )
    except DashboardInstallError as exc:
        return (
            "Prediction data: GENERAL_DASHBOARD is installed, but the data check failed: "
            f"{exc.message}"
        )
    if not isinstance(text, str):
        return "Prediction data: the dashboard was created, but the data check returned no text."
    if "error" in text.lower() and "_value" not in text:
        return (
            "Prediction data: no readable prediction count. "
            "The dashboard is installed and no sample points were inserted. "
            f"Query detail: {text.strip()[:500]}"
        )
    total = 0
    header: list[str] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        columns = line.split(",")
        if not header:
            header = columns
            continue
        if "_value" not in header or len(columns) != len(header):
            continue
        raw = columns[header.index("_value")].strip()
        if raw:
            total += int(float(raw))
    if total == 0:
        return (
            f"Prediction data: bucket {bucket!r} has no prediction failure_probability points "
            "in the last 24 hours. GENERAL_DASHBOARD is installed and will fill when the "
            "simulator, edge, and storage services write predictions. No sample points were inserted."
        )
    return (
        f"Prediction data: {total} failure_probability points in bucket {bucket!r} "
        "during the last 24 hours."
    )


def install() -> int:
    settings = require_settings()
    threshold = load_decision_threshold()
    resources, info = prepare_dashboard(
        load_template(),
        bucket=settings["bucket"],
        stale_seconds=settings["stale_seconds"],
        threshold=threshold,
    )
    for chart in dashboard_charts(resources):
        chart["_bucket"] = settings["bucket"]

    session = InfluxSession(settings["url"], settings["token"])
    version = check_version(session)
    org_id = find_org_id(session, settings["org"])
    before = list_dashboards(session, org_id)
    before_ids = {item["id"]: item.get("name") for item in before}
    existing = [item for item in before if item.get("name") == DASHBOARD_NAME]
    stack = find_stack(list_stacks(session, org_id))
    stack_id = apply_template(
        session,
        org_id,
        resources,
        str(stack["id"]) if stack else None,
    )
    name_stack(session, stack_id)
    stacks = list_stacks(session, org_id)
    managed = next((item for item in stacks if item.get("id") == stack_id), None)
    dashboard_id = stack_dashboard_id(managed or {})
    if not dashboard_id:
        named = [
            item
            for item in list_dashboards(session, org_id)
            if item.get("name") == DASHBOARD_NAME
        ]
        if len(named) == 1:
            dashboard_id = named[0]["id"]
        else:
            raise DashboardInstallError(
                "The template was applied, but GENERAL_DASHBOARD could not be identified."
            )

    session.send(
        "PATCH",
        f"/api/v2/dashboards/{urllib.parse.quote(dashboard_id)}",
        {
            "name": DASHBOARD_NAME,
            "description": dashboard_charts(resources) and resources[0]["spec"].get("description"),
        },
    )
    charts = dashboard_charts(resources)
    cell_count = reconcile_cells(session, dashboard_id, charts)
    verify_dashboard(session, dashboard_id, charts)

    after = list_dashboards(session, org_id)
    for item in after:
        if item.get("name") == DASHBOARD_NAME and item["id"] != dashboard_id:
            session.request(
                "DELETE",
                f"/api/v2/dashboards/{urllib.parse.quote(item['id'])}",
            )
    after = list_dashboards(session, org_id)
    kept = [item for item in after if item.get("name") == DASHBOARD_NAME]
    if len(kept) != 1 or kept[0]["id"] != dashboard_id:
        raise DashboardInstallError("GENERAL_DASHBOARD was not unique after installation.")
    lost = [
        name
        for ident, name in before_ids.items()
        if name != DASHBOARD_NAME and ident not in {item["id"] for item in after}
    ]
    if lost:
        raise DashboardInstallError(
            "Installation removed dashboards other than GENERAL_DASHBOARD: " + ", ".join(lost)
        )

    action = "updated" if existing else "created"
    url = f"{settings['url']}/orgs/{org_id}/dashboards/{dashboard_id}"
    print(f"Dashboard: {DASHBOARD_NAME}")
    print(f"ID: {dashboard_id}")
    print(f"URL: {url}")
    print(f"Action: {action}")
    print(f"Cells: {cell_count}")
    print(f"InfluxDB: {version}")
    print(
        "Alert rule: latest prediction per engine over 24h; "
        f"stale after {settings['stale_seconds']}s; "
        f"critical when failure_probability >= {info['threshold']} "
        f"({info['threshold_source']}); table status uses predicted_class from that model."
    )
    if info["variables"]:
        print("Dashboard variables: " + ", ".join(f"v.{name}" for name in info["variables"]))
    else:
        print("Dashboard variables: none. History cells use the built-in dashboard time range.")
    print(prediction_status(session, settings["org"], settings["bucket"]))
    return 0


def main() -> int:
    try:
        return install()
    except DashboardInstallError as exc:
        print(f"ERROR: {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
