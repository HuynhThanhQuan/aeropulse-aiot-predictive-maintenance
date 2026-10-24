from __future__ import annotations

import json
import logging
import signal
import threading
from datetime import datetime

import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point, WriteOptions

from .config import load_config
from .mqtt_utils import connect_with_retry, create_client
from .schema import topic_engine_id

LOGGER = logging.getLogger(__name__)


class InfluxStorageService:
    def __init__(self, url: str, token: str, org: str, bucket: str) -> None:
        self.org = org
        self.bucket = bucket
        self.influx = InfluxDBClient(url=url, token=token, org=org)
        # Ghi theo lô để giảm request; dashboard đọc DB có thể trễ so với MQTT trực tiếp.
        # batch_size tính theo điểm dữ liệu; flush_interval tính bằng millisecond.
        self.writer = self.influx.write_api(
            write_options=WriteOptions(batch_size=100, flush_interval=1000)
        )
        self.stop_event = threading.Event()

    def on_connect(self, client: mqtt.Client, userdata, flags, reason_code, properties) -> None:
        LOGGER.info("Storage service connected to MQTT: %s", reason_code)
        for topic in (
            "aiot/engine/+/telemetry",
            "aiot/engine/+/prediction",
            "aiot/engine/+/alert",
            "aiot/engine/+/status",
        ):
            client.subscribe(topic, qos=1)

    def on_message(self, client: mqtt.Client, userdata, message: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            engine_id = topic_engine_id(message.topic)
            kind = message.topic.rsplit("/", 1)[-1]
            point = self.to_point(kind, engine_id, payload)
            self.writer.write(bucket=self.bucket, org=self.org, record=point)
        except Exception:
            LOGGER.exception("Failed to store message from %s", message.topic)

    @staticmethod
    def to_point(kind: str, engine_id: str, payload: dict) -> Point:
        point = Point(kind).tag("engine_id", engine_id)
        timestamp = payload.get("timestamp")
        if timestamp:
            point = point.time(datetime.fromisoformat(timestamp))
        if kind == "telemetry":
            point = point.field("cycle", int(payload["cycle"]))
            for name, value in payload["sensors"].items():
                point = point.field(name, float(value))
        elif kind == "prediction":
            point = (
                point.field("cycle", int(payload["cycle"]))
                .field("failure_probability", float(payload["failure_probability"]))
                .field("latency_ms", float(payload["latency_ms"]))
                .field("predicted_class", int(payload["predicted_class"]))
                .tag("risk_level", str(payload["risk_level"]))
            )
        elif kind == "alert":
            point = (
                point.field("cycle", int(payload["cycle"]))
                .field("failure_probability", float(payload["failure_probability"]))
                .field("message", str(payload["message"]))
                .tag("severity", str(payload["severity"]))
            )
        elif kind == "status":
            point = (
                point.field("maintenance_scheduled", bool(payload["maintenance_scheduled"]))
                .field("reason", str(payload.get("reason", "")))
                .tag("state", str(payload["state"]))
            )
        return point

    def run(self, host: str, port: int, keepalive: int) -> None:
        client = create_client("influx-storage-service")
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        connect_with_retry(client, host, port, keepalive)
        client.loop_start()
        self.stop_event.wait()
        client.loop_stop()
        client.disconnect()
        self.writer.close()
        self.influx.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()
    influx = config["influxdb"]
    service = InfluxStorageService(influx["url"], influx["token"], influx["org"], influx["bucket"])
    signal.signal(signal.SIGINT, lambda *_: service.stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: service.stop_event.set())
    service.run(config["mqtt"]["host"], config["mqtt"]["port"], config["mqtt"]["keepalive"])


if __name__ == "__main__":
    main()
