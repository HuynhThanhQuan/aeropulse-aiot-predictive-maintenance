# Five-Minute Demo Runbook

## Before the defense

- Run `make test` and `make benchmark` on the presentation laptop.
- Run `docker compose up -d --build` and check `docker compose ps`.
- Confirm the dashboard and InfluxDB open locally without internet access.
- Record a backup screen capture showing the full closed loop.
- Keep `results/benchmark.json` and the model metadata available for Q&A.

## Demo timeline

| Time | Action | Evidence |
| --- | --- | --- |
| 0:00-0:30 | Show service health | MQTT, InfluxDB, edge, dashboard, controller and simulator are running |
| 0:30-1:30 | Show telemetry | Cycle and selected sensor update every 0.25 seconds |
| 1:30-2:30 | Show edge inference | Probability, risk class and measured per-message latency update |
| 2:30-3:30 | Show closed loop | Alert creates a maintenance command and simulator acknowledgement |
| 3:30-4:15 | Reset from dashboard | Manual command returns maintenance flag to clear |
| 4:15-5:00 | Show persistence | InfluxDB contains telemetry, prediction, alert and status measurements |

If a container fails, use `docker compose logs --tail=80 SERVICE_NAME`. Do not retrain during the live demo; use the checked model artifacts.
