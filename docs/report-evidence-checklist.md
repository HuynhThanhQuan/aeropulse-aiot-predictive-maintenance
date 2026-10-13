# Report Evidence Checklist

This is an evidence checklist, not report text. Write the report in the team's own words.

## Data pipeline

- Dataset provenance and citation.
- FD001 dimensions, engine count and selected sensor rationale.
- MQTT topic table and example payload captured from the running broker.
- InfluxDB schema and screenshot of persisted records.

## Edge AI

- Engine-level split and leakage prevention.
- Window size, warning horizon and class distribution.
- 1D-CNN layer table and parameter count.
- Static quantization calibration method and representative sample count.
- Proof that the deployed service loads `tiny_cnn_int8.onnx`.

## Benchmark

- Raw `benchmark.json` committed with device metadata.
- FP32 versus INT8 accuracy and performance table.
- Confusion matrix or precision/recall discussion.
- Limitations of RSS measurement and simulated data.

## Application and demo

- Screenshot showing live telemetry and risk.
- MQTT alert, command and status messages for one closed-loop event.
- Screenshot or query output from InfluxDB.
- Backup demo recording.

## Presentation and Q&A

- Every member can explain one service and the full data flow.
- Every member can define RUL, warning horizon, F1 and static INT8 quantization.
- AI Disclosure appears in both README and final slide.
- Contribution table maps members to code, experiments and presentation sections.
