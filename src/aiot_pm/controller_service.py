from __future__ import annotations

import json
import logging
import signal
import threading

import paho.mqtt.client as mqtt

from .config import load_config
from .mqtt_utils import connect_with_retry, create_client
from .schema import engine_topic, topic_engine_id, utc_now

LOGGER = logging.getLogger(__name__)


class MaintenanceController:
    def __init__(self) -> None:
        self.active_alerts: set[str] = set()
        self.stop_event = threading.Event()

    def on_connect(self, client: mqtt.Client, userdata, flags, reason_code, properties) -> None:
        LOGGER.info("Controller connected to MQTT: %s", reason_code)
        client.subscribe("aiot/engine/+/alert", qos=1)
        client.subscribe("aiot/engine/+/status", qos=1)

    def on_message(self, client: mqtt.Client, userdata, message: mqtt.MQTTMessage) -> None:
        try:
            engine_id = topic_engine_id(message.topic)
            payload = json.loads(message.payload.decode("utf-8"))
            kind = message.topic.rsplit("/", 1)[-1]
            if kind == "alert" and payload.get("severity") == "high":
                if engine_id in self.active_alerts:
                    return
                command = {
                    "engine_id": engine_id,
                    "action": "schedule_maintenance",
                    "reason": payload.get("message", "high failure risk"),
                    "source": "automatic_closed_loop_controller",
                    "timestamp": utc_now(),
                }
                client.publish(engine_topic(engine_id, "command"), json.dumps(command), qos=1)
                self.active_alerts.add(engine_id)
                LOGGER.warning("Scheduled maintenance for %s", engine_id)
            elif kind == "status" and not payload.get("maintenance_scheduled", False):
                self.active_alerts.discard(engine_id)
        except (ValueError, json.JSONDecodeError) as exc:
            LOGGER.warning("Invalid controller message on %s: %s", message.topic, exc)

    def run(self, host: str, port: int, keepalive: int) -> None:
        client = create_client("closed-loop-maintenance-controller")
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        connect_with_retry(client, host, port, keepalive)
        client.loop_start()
        self.stop_event.wait()
        client.loop_stop()
        client.disconnect()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()
    controller = MaintenanceController()
    signal.signal(signal.SIGINT, lambda *_: controller.stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: controller.stop_event.set())
    controller.run(config["mqtt"]["host"], config["mqtt"]["port"], config["mqtt"]["keepalive"])


if __name__ == "__main__":
    main()
