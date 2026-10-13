import hashlib
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import onnxruntime as ort
import psutil
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from aiot_pm.benchmark import evaluate_model_isolated
from aiot_pm.data import build_windows, load_cmapss

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "submission/evidence"
OUT.mkdir(parents=True, exist_ok=True)


def dump(name, obj):
    (OUT / name).write_text(json.dumps(obj, indent=2))


def main():
    m = json.loads((ROOT / "models/metadata.json").read_text())
    threshold = m["decision_threshold"]
    dump(
        "device.json",
        {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version,
            "onnxruntime": ort.__version__,
            "cpu": (subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
            ).strip() if sys.platform == "darwin" else platform.processor() or "unknown"),
            "ram_bytes": psutil.virtual_memory().total,
            "cpu_logical": psutil.cpu_count(),
            "scope": "Current development host; defense device not confirmed",
        },
    )
    for run in range(4):
        rows = [
            evaluate_model_isolated(
                ROOT / "models" / f"tiny_cnn_{v}.onnx",
                ROOT / "data/processed/validation_windows.npz",
                threshold,
            )
            for v in ["fp32", "int8"]
        ]
        dump(f"benchmark-{'warmup' if run == 0 else str(run)}.json", rows)
    rows = []
    for i in range(2):
        samples = [json.loads((OUT / f"benchmark-{r}.json").read_text())[i] for r in [1, 2, 3]]
        rows.append(
            {
                k: float(np.median([s[k] for s in samples])) if isinstance(v, (int, float)) else v
                for k, v in samples[0].items()
            }
        )
    dump("benchmark-median.json", rows)
    train = load_cmapss(ROOT / "data/raw/train_FD001.txt")
    test = load_cmapss(ROOT / "data/raw/test_FD001.txt")
    endpoints = np.loadtxt(ROOT / "data/raw/RUL_FD001.txt").astype(int)
    # Test trajectories end before failure: add official endpoint RUL to each within-engine offset.
    test["rul"] = test["rul"] + test["unit_id"].map(
        {i + 1: int(v) for i, v in enumerate(endpoints)}
    )
    features = m["feature_names"]
    test[features] = (test[features] - np.asarray(m["scaler_mean"])) / np.asarray(m["scaler_scale"])
    x, y = build_windows(test, features, m["window_size"], m["warning_rul"])
    last = np.cumsum([len(g) - m["window_size"] + 1 for _, g in test.groupby("unit_id")]) - 1
    evals = {}
    for variant in ["fp32", "int8"]:
        session = ort.InferenceSession(
            str(ROOT / "models" / f"tiny_cnn_{variant}.onnx"), providers=["CPUExecutionProvider"]
        )
        logits = session.run(None, {"sensor_window": x})[0].reshape(-1)
        p = 1 / (1 + np.exp(-logits))
        pred = p >= threshold
        evals[variant] = {}
        for scope, ix in [("all_windows", np.arange(len(y))), ("last_window_per_engine", last)]:
            yy = y[ix]
            pp = p[ix]
            yp = pred[ix]
            evals[variant][scope] = {
                "samples": len(ix),
                "positives": int(yy.sum()),
                "accuracy": accuracy_score(yy, yp),
                "precision": precision_score(yy, yp, zero_division=0),
                "recall": recall_score(yy, yp, zero_division=0),
                "f1": f1_score(yy, yp, zero_division=0),
                "roc_auc": roc_auc_score(yy, pp),
                "confusion_matrix_tn_fp_fn_tp": confusion_matrix(yy, yp, labels=[0, 1])
                .ravel()
                .tolist(),
            }
    dump(
        "official-test-evaluation.json",
        {
            "threshold_frozen": threshold,
            "rul_method": "max_observed_cycle - cycle + official RUL_FD001 endpoint",
            "models": evals,
        },
    )
    from aiot_pm.model import TinyConvNet

    archive = np.load(ROOT / "data/processed/validation_windows.npz")
    dump(
        "dataset-model.json",
        {
            "train_rows": len(train),
            "test_rows": len(test),
            "train_engines": len(m["train_engine_ids"]),
            "validation_engines": len(m["validation_engine_ids"]),
            "validation_windows": len(archive["labels"]),
            "validation_positives": int(archive["labels"].sum()),
            "training_windows": sum(
                len(g) - 29 for i, g in train.groupby("unit_id") if i in m["train_engine_ids"]
            ),
            "parameters": sum(p.numel() for p in TinyConvNet(14).parameters()),
            "threshold": threshold,
            "demo_engine_in_training": 100 in m["train_engine_ids"],
            "sha256": {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [
                    ROOT / "models/tiny_cnn_fp32.onnx",
                    ROOT / "models/tiny_cnn_int8.onnx",
                    ROOT / "models/metadata.json",
                    ROOT / "data/raw/test_FD001.txt",
                    ROOT / "data/raw/RUL_FD001.txt",
                ]
            },
        },
    )
    print(json.dumps(evals, indent=2))


if __name__ == "__main__":
    main()
