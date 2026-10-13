from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
from pathlib import Path

import numpy as np
import paho.mqtt.client as mqtt

from .config import load_config, project_path
from .mqtt_utils import connect_with_retry, create_client
from .schema import Telemetry, engine_topic, topic_engine_id, utc_now

LOGGER = logging.getLogger(__name__)


class EngineSimulator:
    def __init__(self, dataset_path: Path, engine_number: int, interval: float, loop: bool) -> None:
        rows = np.loadtxt(dataset_path, dtype=np.float64)
        self.rows = rows[rows[:, 0].astype(int) == engine_number]
        if len(self.rows) == 0:
            raise ValueError(f"Engine {engine_number} was not found in {dataset_path.name}")
        self.engine_id = f"FD001-{engine_number:03d}"
        self.interval = interval
        self.loop = loop
        self.stop_event = threading.Event()
        self.maintenance_scheduled = False
        self.client: mqtt.Client | None = None

    def on_connect(self, client: mqtt.Client, userdata, flags, reason_code, properties) -> None:
        LOGGER.info("Simulator connected to MQTT: %s", reason_code)
        client.subscribe("aiot/engine/+/command", qos=1)
        self.publish_status("online")

    def on_message(self, client: mqtt.Client, userdata, message: mqtt.MQTTMessage) -> None:
        try:
            if topic_engine_id(message.topic) != self.engine_id:
                return
            command = json.loads(message.payload.decode("utf-8"))
            action = command.get("action")
            if action == "schedule_maintenance":
                self.maintenance_scheduled = True
                self.publish_status("maintenance_scheduled", command.get("reason", "risk alert"))
            elif action == "reset_maintenance":
                self.maintenance_scheduled = False
                self.publish_status("running", "maintenance flag reset")
            else:
                self.publish_status("command_rejected", f"unknown action: {action}")
        except (ValueError, json.JSONDecodeError) as exc:
            LOGGER.warning("Invalid command on %s: %s", message.topic, exc)

    def publish_status(self, state: str, reason: str = "") -> None:
        if self.client is None:
            return
        payload = {
            "engine_id": self.engine_id,
            "state": state,
            "maintenance_scheduled": self.maintenance_scheduled,
            "reason": reason,
            "timestamp": utc_now(),
        }
        self.client.publish(
            engine_topic(self.engine_id, "status"), json.dumps(payload), qos=1, retain=True
        )

    def run(self, host: str, port: int, keepalive: int) -> None:
        self.client = create_client(f"simulator-{self.engine_id}")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        connect_with_retry(self.client, host, port, keepalive)
        self.client.loop_start()
        try:
            while not self.stop_event.is_set():
                for row in self.rows:
                    if self.stop_event.is_set():
                        break
                    telemetry = Telemetry(
                        engine_id=self.engine_id,
                        cycle=int(row[1]),
                        operational_settings={
                            f"op_setting_{index}": float(row[1 + index])
                            for index in range(1, 4)
                        },
                        sensors={f"sensor_{index}": float(row[4 + index]) for index in range(1, 22)},
                    )
                    info = self.client.publish(
                        engine_topic(self.engine_id, "telemetry"),
                        json.dumps(telemetry.to_dict()),
                        qos=1,
                    )
                    info.wait_for_publish(timeout=2)
                    LOGGER.info("Published %s cycle %s", self.engine_id, telemetry.cycle)
                    self.stop_event.wait(self.interval)
                if not self.loop:
                    break
                self.publish_status("restarting_replay", "end of engine trajectory")
                self.stop_event.wait(1)
        finally:
            self.publish_status("offline")
            self.client.loop_stop()
            self.client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay one C-MAPSS engine over MQTT")
    parser.add_argument("--engine", type=int, default=None, help="FD001 engine number (1-100)")
    parser.add_argument("--interval", type=float, default=None, help="Seconds between cycles")
    parser.add_argument("--once", action="store_true", help="Stop after one trajectory")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()
    dataset_path = project_path(config["project"]["data_dir"]) / "train_FD001.txt"
    if not dataset_path.exists():
        raise SystemExit("Dataset not found. Run `aiot-download` first.")
    simulator = EngineSimulator(
        dataset_path,
        engine_number=args.engine or config["simulator"]["engine_id"],
        interval=args.interval or config["simulator"]["interval_seconds"],
        loop=config["simulator"]["loop"] and not args.once,
    )
    signal.signal(signal.SIGINT, lambda *_: simulator.stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: simulator.stop_event.set())
    simulator.run(config["mqtt"]["host"], config["mqtt"]["port"], config["mqtt"]["keepalive"])


if __name__ == "__main__":
    main()
