import json
import os
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient

from aiot_pm.config import load_config

root = Path(__file__).resolve().parents[1]
out = root / "submission/evidence"
out.mkdir(parents=True, exist_ok=True)
config = load_config()
started = datetime.now(UTC).isoformat()
stream = (out / "mqtt-capture.jsonl").open("w")
messages = []


def on_connect(c, u, f, r, p):
    c.subscribe("aiot/engine/+/+", qos=1)


def on_message(c, u, msg):
    row = {
        "received_at": datetime.now(UTC).isoformat(),
        "topic": msg.topic,
        "retained": msg.retain,
        "payload": json.loads(msg.payload),
    }
    messages.append(row)
    stream.write(json.dumps(row) + "\n")
    stream.flush()


c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="submission-evidence")
c.on_connect = on_connect
c.on_message = on_message
c.connect(config["mqtt"]["host"], config["mqtt"]["port"])
c.loop_start()
time.sleep(1)
subprocess.run(
    ["docker", "compose", "restart", "simulator"], cwd=root, check=True, capture_output=True
)
time.sleep(65)
(out / "snapshot-after-loop.json").write_bytes(
    urllib.request.urlopen(f"http://localhost:{os.getenv('DASHBOARD_PORT', '8010')}/api/snapshot").read()
)
c.loop_stop()
c.disconnect()
stream.close()
config = load_config()["influxdb"]
query = f'from(bucket: "telemetry") |> range(start: time(v: "{started}")) |> filter(fn: (r) => r.engine_id == "FD001-100") |> group(columns: ["_measurement", "_field"]) |> count()'
(out / "influx-query.flux").write_text(query)
with InfluxDBClient(url=config["url"], token=config["token"], org=config["org"]) as db:
    tables = db.query_api().query(query)
    records = [
        {
            "measurement": r.values["_measurement"],
            "field": r.values["_field"],
            "count": r.get_value(),
        }
        for t in tables
        for r in t.records
    ]
    (out / "influx-counts.json").write_text(json.dumps(records, indent=2))
events = [
    r
    for r in messages
    if r["topic"].endswith(("/alert", "/command", "/status")) and not r["retained"]
]
(out / "closed-loop-events.json").write_text(json.dumps(events, indent=2))
print(
    "Captured",
    len(messages),
    "MQTT messages;",
    len(events),
    "events; measurements:",
    sorted({r["measurement"] for r in records}),
)
