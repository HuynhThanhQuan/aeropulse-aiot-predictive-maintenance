from __future__ import annotations

import json
import logging
import math
import signal
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

import onnxruntime as ort
import paho.mqtt.client as mqtt

from .config import load_config, project_path
from .mqtt_utils import connect_with_retry, create_client
from .preprocessing import normalize_window
from .schema import Prediction, engine_topic, topic_engine_id, utc_now

LOGGER = logging.getLogger(__name__)


class EdgeInferenceService:
    def __init__(self, model_path: Path, metadata_path: Path, cooldown: float) -> None:
        self.metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        # Tạo session một lần để tái sử dụng; không nạp model lại cho mỗi MQTT message.
        self.session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.window_size = int(self.metadata["window_size"])
        self.threshold = float(self.metadata["decision_threshold"])
        self.feature_names = list(self.metadata["feature_names"])
        self.mean = list(self.metadata["scaler_mean"])
        self.scale = list(self.metadata["scaler_scale"])
        self.model_name = model_path.name
        self.cooldown = cooldown
        # Mỗi engine có lịch sử riêng; không trộn sensor của các engine vào một cửa sổ.
        self.buffers: dict[str, deque[dict[str, float]]] = defaultdict(
            lambda: deque(maxlen=self.window_size)
        )
        self.last_alert: dict[str, float] = defaultdict(lambda: 0.0)
        self.last_cycle: dict[str, int] = {}
        self.client: mqtt.Client | None = None
        self.stop_event = threading.Event()

    def on_connect(self, client: mqtt.Client, userdata, flags, reason_code, properties) -> None:
        LOGGER.info("Edge service connected to MQTT: %s", reason_code)
        client.subscribe("aiot/engine/+/telemetry", qos=1)

    def on_message(self, client: mqtt.Client, userdata, message: mqtt.MQTTMessage) -> None:
        try:
            engine_id = topic_engine_id(message.topic)
            payload = json.loads(message.payload.decode("utf-8"))
            sensors = payload["sensors"]
            if any(name not in sensors for name in self.feature_names):
                raise ValueError("Telemetry is missing one or more model sensor features")
            buffer = self.buffers[engine_id]
            cycle = int(payload["cycle"])
            # Khi cycle quay lại hoặc lặp, xóa cửa sổ để tránh nối sai chuỗi thời gian.
            if engine_id in self.last_cycle and cycle <= self.last_cycle[engine_id]:
                buffer.clear()
            self.last_cycle[engine_id] = cycle
            buffer.append(sensors)
            if len(buffer) < self.window_size:
                return

            values = normalize_window(list(buffer), self.feature_names, self.mean, self.scale)
            # Chỉ đo session.run; latency này chưa gồm MQTT, chuẩn hóa, DB và giao diện.
            started = time.perf_counter_ns()
            logit = float(self.session.run(None, {self.input_name: values})[0].reshape(-1)[0])
            latency_ms = (time.perf_counter_ns() - started) / 1_000_000
            # Sigmoid chuyển logit thành score [0,1]; chặn logit để tránh overflow exp.
            # Đây là score phân loại RUL <= warning_rul, chưa bảo đảm xác suất hiệu chuẩn.
            probability = 1.0 / (1.0 + math.exp(-max(min(logit, 30), -30)))
            risk_level = self.risk_level(probability)
            prediction = Prediction(
                engine_id=engine_id,
                cycle=cycle,
                failure_probability=probability,
                predicted_class=int(probability >= self.threshold),
                risk_level=risk_level,
                model=self.model_name,
                latency_ms=latency_ms,
            )
            client.publish(
                engine_topic(engine_id, "prediction"),
                json.dumps(prediction.to_dict()),
                qos=1,
            )
            if risk_level == "high" and time.monotonic() - self.last_alert[engine_id] >= self.cooldown:
                alert = {
                    "engine_id": engine_id,
                    "cycle": cycle,
                    "severity": "high",
                    "failure_probability": probability,
                    "message": "Failure risk exceeded the validated decision threshold",
                    "timestamp": utc_now(),
                }
                client.publish(engine_topic(engine_id, "alert"), json.dumps(alert), qos=1)
                self.last_alert[engine_id] = time.monotonic()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            LOGGER.warning("Rejected telemetry on %s: %s", message.topic, exc)
        except Exception:
            LOGGER.exception("Inference failed for message on %s", message.topic)

    def risk_level(self, probability: float) -> str:
        if probability >= self.threshold:
            return "high"
        # watch là vùng đệm hiển thị; chỉ high kích hoạt alert. Cooldown chỉ giãn
        # các alert lặp lại, không thay đổi score hoặc predicted_class.
        if probability >= max(0.25, self.threshold * 0.70):
            return "watch"
        return "normal"

    def run(self, host: str, port: int, keepalive: int) -> None:
        self.client = create_client("edge-inference-int8")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        connect_with_retry(self.client, host, port, keepalive)
        self.client.loop_start()
        self.stop_event.wait()
        self.client.loop_stop()
        self.client.disconnect()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()
    model_path = project_path(config["edge"]["model_path"])
    metadata_path = project_path(config["edge"]["metadata_path"])
    if not model_path.exists() or not metadata_path.exists():
        raise SystemExit("Model artifacts not found. Run `aiot-train` first.")
    service = EdgeInferenceService(
        model_path, metadata_path, config["edge"]["alert_cooldown_seconds"]
    )
    signal.signal(signal.SIGINT, lambda *_: service.stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: service.stop_event.set())
    service.run(config["mqtt"]["host"], config["mqtt"]["port"], config["mqtt"]["keepalive"])


if __name__ == "__main__":
    main()
