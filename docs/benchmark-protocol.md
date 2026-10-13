# Benchmark Protocol

Use this protocol on the exact laptop or edge device used during the defense.

1. Record device model, CPU, RAM, operating system, Python version and ONNX Runtime version.
2. Stop unrelated high-load applications.
3. Run `make benchmark` three times after one warm-up run.
4. Keep every generated `benchmark.json` with a timestamped copy.
5. Report median results across the three runs and explain the aggregation.
6. Compare FP32 and INT8 using the same validation windows and decision threshold.
7. Report F1 and ROC-AUC together; class imbalance can make accuracy misleading.
8. State that RSS is process memory on the edge gateway, not MCU Flash.

Required table fields:

| Variant | F1 | ROC-AUC | Size KiB | Mean latency ms | P95 latency ms | RSS after load MiB |
| --- | --- | --- | --- | --- | --- | --- |
| FP32 | measured | measured | measured | measured | measured | measured |
| INT8 | measured | measured | measured | measured | measured | measured |

Never manually replace a measured result with an expected value. If INT8 is slower or loses accuracy, report it and analyze the likely cause.
