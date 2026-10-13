from __future__ import annotations

import logging
import time
from typing import Any

import paho.mqtt.client as mqtt

LOGGER = logging.getLogger(__name__)


def create_client(client_id: str, userdata: Any = None) -> mqtt.Client:
    return mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        userdata=userdata,
        protocol=mqtt.MQTTv311,
    )


def connect_with_retry(
    client: mqtt.Client, host: str, port: int, keepalive: int = 60, retries: int = 30
) -> None:
    for attempt in range(1, retries + 1):
        try:
            client.connect(host, port, keepalive)
            return
        except OSError as exc:
            if attempt == retries:
                raise
            LOGGER.warning(
                "MQTT broker unavailable at %s:%s (%s); retrying %s/%s",
                host,
                port,
                exc,
                attempt,
                retries,
            )
            time.sleep(min(0.5 * attempt, 5))
