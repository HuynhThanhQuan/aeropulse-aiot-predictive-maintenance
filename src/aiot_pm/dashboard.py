from __future__ import annotations

import json
import logging
import os
import threading
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import paho.mqtt.client as mqtt
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT, load_config
from .dashboard_cache import DashboardCache, resolve_cache_path
from .fleet import FleetDataError, FleetService
from .mqtt_utils import create_client
from .schema import engine_topic, topic_engine_id, utc_now

LOGGER = logging.getLogger(__name__)
WEB_DIR = ROOT / "web"
SUBSCRIPTIONS = (
    "aiot/engine/+/telemetry",
    "aiot/engine/+/prediction",
    "aiot/engine/+/alert",
    "aiot/engine/+/status",
    "aiot/engine/+/command",
)


def stale_after_seconds() -> float:
    return float(os.getenv("DASHBOARD_STALE_AFTER_S", "5"))


def connection_mode(
    mqtt_connected: bool, seconds_since_last_message: float | None, stale_after_s: float
) -> str:
    # offline: mất broker. stale: còn broker nhưng lâu không có tin mới. live: tin mới dưới ngưỡng.
    if not mqtt_connected:
        return "offline"
    if seconds_since_last_message is None or seconds_since_last_message >= stale_after_s:
        return "stale"
    return "live"


class CommandRequest(BaseModel):
    engine_id: str
    action: str


class DashboardState:
    def __init__(self, config: dict[str, Any], cache: DashboardCache | None = None) -> None:
        self.config = config
        self.cache = cache
        self.lock = threading.Lock()
        self.telemetry: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=180))
        self.predictions: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=180))
        self.status: dict[str, dict] = {}
        self.events: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=40))
        self.last_message_at: str | None = None
        self.last_message_by_engine: dict[str, str] = {}
        self.from_cache = True
        self.stale_after_s = stale_after_seconds()
        self.client = create_client("aiot-web-dashboard")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def on_connect(self, client: mqtt.Client, userdata, flags, reason_code, properties) -> None:
        LOGGER.info("Dashboard connected to MQTT: %s", reason_code)
        for topic in SUBSCRIPTIONS:
            client.subscribe(topic, qos=1)

    def on_message(self, client: mqtt.Client, userdata, message: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            self.ingest(message.topic.rsplit("/", 1)[-1], topic_engine_id(message.topic), payload)
        except (ValueError, json.JSONDecodeError):
            LOGGER.warning("Ignored invalid dashboard message on %s", message.topic)

    def ingest(self, kind: str, engine_id: str, payload: dict[str, Any]) -> None:
        # Cùng một lối vào cho tin MQTT và cho chế độ phát lại sau này.
        timestamp = utc_now()
        with self.lock:
            if kind == "telemetry":
                self._append_telemetry(engine_id, payload)
            elif kind == "prediction":
                self.predictions[engine_id].append(payload)
            elif kind == "status":
                self.status[engine_id] = payload
                self.events[engine_id].appendleft({"kind": kind, **payload})
            elif kind in {"alert", "command"}:
                self.events[engine_id].appendleft({"kind": kind, **payload})
            else:
                return
            self.last_message_at = timestamp
            self.last_message_by_engine[engine_id] = timestamp
            self.from_cache = False
        self._remember(engine_id, kind, payload, timestamp)

    def _append_telemetry(self, engine_id: str, payload: dict[str, Any]) -> None:
        # Chỉ xóa bộ nhớ khi chu kỳ giảm hơn 5. Bản tin QoS 1 gửi lại không được xóa biểu đồ.
        cycle = int(payload["cycle"])
        history = self.telemetry[engine_id]
        if history and cycle < int(history[-1]["cycle"]) - 5:
            history.clear()
            self.predictions[engine_id].clear()
        history.append(payload)

    def _remember(self, engine_id: str, kind: str, payload: dict[str, Any], timestamp: str) -> None:
        if self.cache is None:
            return
        cycle = payload.get("cycle")
        try:
            self.cache.enqueue(
                engine_id,
                kind,
                int(cycle) if cycle is not None else None,
                timestamp,
                payload,
            )
        except Exception:
            LOGGER.exception("Could not queue dashboard cache row")

    def start(self) -> None:
        if self.cache is None:
            self.cache = DashboardCache(resolve_cache_path())
        try:
            self.cache.start()
            self._load_cache()
        except Exception:
            LOGGER.exception("Dashboard cache failed; continuing without saved history")
        # Không chặn và không ném lỗi nếu broker chưa chạy. Paho tự kết nối lại.
        try:
            self.client.reconnect_delay_set(1, 10)
            mqtt_config = self.config["mqtt"]
            self.client.connect_async(
                mqtt_config["host"], int(mqtt_config["port"]), int(mqtt_config["keepalive"])
            )
        except Exception:
            LOGGER.exception("MQTT setup failed; dashboard stays available")
        try:
            self.client.loop_start()
        except Exception:
            LOGGER.exception("MQTT loop failed; dashboard stays available")

    def stop(self) -> None:
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            LOGGER.exception("MQTT shutdown failed")
        if self.cache is not None:
            self.cache.stop()

    def _load_cache(self) -> None:
        if self.cache is None:
            return
        saved = self.cache.load()
        with self.lock:
            for engine_id, rows in saved["telemetry"].items():
                for row in rows:
                    self.telemetry[engine_id].append(row)
            for engine_id, rows in saved["predictions"].items():
                for row in rows:
                    self.predictions[engine_id].append(row)
            for engine_id, rows in saved["events"].items():
                for row in rows:
                    self.events[engine_id].appendleft(row)
            self.status.update(saved["status"])
            self.last_message_at = saved["latest_ts"]
            self.last_message_by_engine.update(saved["latest_by_engine"])
            self.from_cache = True

    def _seconds_since_last_message(self, now: datetime) -> float | None:
        if not self.last_message_at:
            return None
        seen = datetime.fromisoformat(self.last_message_at)
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=UTC)
        return (now - seen).total_seconds()

    def describe(self) -> dict[str, Any]:
        connected = bool(self.client.is_connected())
        with self.lock:
            age = self._seconds_since_last_message(datetime.now(UTC))
            mode = connection_mode(connected, age, self.stale_after_s)
            return {
                "connected": connected,
                "mode": mode,
                "last_message_at": self.last_message_at,
                "seconds_since_last_message": age,
                "from_cache": self.from_cache,
            }

    def snapshot(self, requested_engine: str | None = None) -> dict[str, Any]:
        connected = bool(self.client.is_connected())
        with self.lock:
            engines = sorted(set(self.telemetry) | set(self.predictions) | set(self.status))
            # Giữ đúng engine được hỏi kể cả khi chưa có tin, không trả dữ liệu của engine khác.
            if requested_engine:
                engine_id = requested_engine
            else:
                engine_id = engines[0] if engines else None
            last_message_at = self.last_message_by_engine.get(engine_id)
            age = None
            if last_message_at:
                seen = datetime.fromisoformat(last_message_at)
                if seen.tzinfo is None:
                    seen = seen.replace(tzinfo=UTC)
                age = (datetime.now(UTC) - seen).total_seconds()
            meta = {
                "connected": connected,
                "mode": connection_mode(connected, age, self.stale_after_s),
                "last_message_at": last_message_at,
                "seconds_since_last_message": age,
                "from_cache": self.from_cache,
            }
            if engine_id is None:
                return {
                    **meta,
                    "engines": [],
                    "engine_id": None,
                    "telemetry": [],
                    "predictions": [],
                    "status": None,
                    "events": [],
                }
            return {
                **meta,
                "engines": engines,
                "engine_id": engine_id,
                "telemetry": list(self.telemetry[engine_id]) if engine_id in self.telemetry else [],
                "predictions": list(self.predictions[engine_id]) if engine_id in self.predictions else [],
                "status": self.status.get(engine_id),
                "events": list(self.events[engine_id]) if engine_id in self.events else [],
            }


def _load_local_env() -> None:
    # Host runs do not export .env. setdefault keeps an explicit environment value.
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_local_env()
CONFIG = load_config()
STATE = DashboardState(CONFIG)
_fleet_service: FleetService | None = None


def fleet_service() -> FleetService:
    global _fleet_service
    if _fleet_service is None:
        _fleet_service = FleetService.from_config(CONFIG)
    return _fleet_service


def _fleet_call(loader):
    try:
        return loader()
    except FleetDataError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    STATE.start()
    yield
    STATE.stop()


app = FastAPI(title="AIoT Predictive Maintenance Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/fleet", include_in_schema=False)
def fleet_page() -> FileResponse:
    return FileResponse(WEB_DIR / "fleet.html")


@app.get("/api/fleet/summary")
def fleet_summary() -> dict[str, Any]:
    return _fleet_call(lambda: fleet_service().summary())


@app.get("/api/fleet/engines")
def fleet_engines() -> dict[str, Any]:
    return _fleet_call(lambda: fleet_service().engines())


@app.get("/api/fleet/risk-distribution")
def fleet_risk_distribution() -> dict[str, Any]:
    return _fleet_call(lambda: fleet_service().distribution())


@app.get("/api/fleet/risk-trend")
def fleet_risk_trend(
    window: str = Query(default="15m"), top: int = Query(default=3)
) -> dict[str, Any]:
    return _fleet_call(lambda: fleet_service().trend(window, top))


@app.get("/api/fleet/engines/{engine_id}")
def fleet_engine(engine_id: str) -> dict[str, Any]:
    engine = _fleet_call(lambda: fleet_service().engine(engine_id))
    if engine is None:
        raise HTTPException(status_code=404, detail="Engine is outside the lookback window")
    return engine


@app.get("/api/health")
def health() -> dict[str, Any]:
    # Luôn trả HTTP 200. status=degraded khi chưa live, để healthcheck không đòi MQTT.
    described = STATE.describe()
    return {
        "status": "ok" if described["mode"] == "live" else "degraded",
        "mqtt_connected": described["connected"],
        "mode": described["mode"],
    }


@app.get("/api/snapshot")
def snapshot(engine_id: str | None = Query(default=None)) -> dict[str, Any]:
    return STATE.snapshot(engine_id)


@app.post("/api/command")
def command(request: CommandRequest) -> dict[str, Any]:
    if request.action not in {"schedule_maintenance", "reset_maintenance"}:
        raise HTTPException(status_code=400, detail="Unsupported command")
    if not STATE.client.is_connected():
        raise HTTPException(status_code=503, detail="MQTT not connected")
    payload = {
        "engine_id": request.engine_id,
        "action": request.action,
        "reason": "manual dashboard command",
        "source": "operator_dashboard",
        "timestamp": utc_now(),
    }
    result = STATE.client.publish(
        engine_topic(request.engine_id, "command"), json.dumps(payload), qos=1
    )
    if result.rc != mqtt.MQTT_ERR_SUCCESS:
        raise HTTPException(status_code=503, detail="MQTT publish failed")
    return {"accepted": True, **payload}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    uvicorn.run("aiot_pm.dashboard:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
