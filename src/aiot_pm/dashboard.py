from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any

import paho.mqtt.client as mqtt
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT, load_config
from .mqtt_utils import connect_with_retry, create_client
from .schema import engine_topic, topic_engine_id, utc_now

LOGGER = logging.getLogger(__name__)
WEB_DIR = ROOT / "web"


class CommandRequest(BaseModel):
    engine_id: str
    action: str


class DashboardState:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.lock = threading.Lock()
        self.telemetry: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=180))
        self.predictions: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=180))
        self.status: dict[str, dict] = {}
        self.events: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=40))
        self.client = create_client("aiot-web-dashboard")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def on_connect(self, client: mqtt.Client, userdata, flags, reason_code, properties) -> None:
        LOGGER.info("Dashboard connected to MQTT: %s", reason_code)
        for topic in (
            "aiot/engine/+/telemetry",
            "aiot/engine/+/prediction",
            "aiot/engine/+/alert",
            "aiot/engine/+/status",
            "aiot/engine/+/command",
        ):
            client.subscribe(topic, qos=1)

    def on_message(self, client: mqtt.Client, userdata, message: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            engine_id = topic_engine_id(message.topic)
            kind = message.topic.rsplit("/", 1)[-1]
            with self.lock:
                if kind == "telemetry":
                    if self.telemetry[engine_id] and int(payload["cycle"]) <= int(
                        self.telemetry[engine_id][-1]["cycle"]
                    ):
                        self.telemetry[engine_id].clear()
                        self.predictions[engine_id].clear()
                    self.telemetry[engine_id].append(payload)
                elif kind == "prediction":
                    self.predictions[engine_id].append(payload)
                elif kind == "status":
                    self.status[engine_id] = payload
                    self.events[engine_id].appendleft({"kind": kind, **payload})
                elif kind in {"alert", "command"}:
                    self.events[engine_id].appendleft({"kind": kind, **payload})
        except (ValueError, json.JSONDecodeError):
            LOGGER.warning("Ignored invalid dashboard message on %s", message.topic)

    def start(self) -> None:
        mqtt_config = self.config["mqtt"]
        connect_with_retry(
            self.client, mqtt_config["host"], mqtt_config["port"], mqtt_config["keepalive"]
        )
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def snapshot(self, requested_engine: str | None = None) -> dict[str, Any]:
        with self.lock:
            engines = sorted(set(self.telemetry) | set(self.predictions) | set(self.status))
            engine_id = requested_engine if requested_engine in engines else (engines[0] if engines else None)
            if engine_id is None:
                return {
                    "connected": bool(self.client.is_connected()),
                    "engines": [],
                    "engine_id": None,
                    "telemetry": [],
                    "predictions": [],
                    "status": None,
                    "events": [],
                }
            return {
                "connected": bool(self.client.is_connected()),
                "engines": engines,
                "engine_id": engine_id,
                "telemetry": list(self.telemetry[engine_id]),
                "predictions": list(self.predictions[engine_id]),
                "status": self.status.get(engine_id),
                "events": list(self.events[engine_id]),
            }


CONFIG = load_config()
STATE = DashboardState(CONFIG)


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


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "mqtt_connected": STATE.client.is_connected()}


@app.get("/api/snapshot")
def snapshot(engine_id: str | None = Query(default=None)) -> dict[str, Any]:
    return STATE.snapshot(engine_id)


@app.post("/api/command")
def command(request: CommandRequest) -> dict[str, Any]:
    if request.action not in {"schedule_maintenance", "reset_maintenance"}:
        raise HTTPException(status_code=400, detail="Unsupported command")
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
