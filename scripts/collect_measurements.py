import hashlib
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import onnxruntime as ort
import pandas as pd
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


def host_cpu() -> str:
    if sys.platform == "darwin":
        return subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
        ).strip()
    if sys.platform == "win32":
        return str(subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"],
            text=True,
        ).strip())
    return platform.processor() or "unknown"


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
            "cpu": host_cpu(),
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
    observed_rul = test["rul"].to_numpy()
    endpoint_rul = test["unit_id"].map({i + 1: int(v) for i, v in enumerate(endpoints)}).to_numpy()
    if np.isnan(endpoint_rul).any():
        raise RuntimeError("RUL_FD001.txt does not cover every test engine")
    test["rul"] = observed_rul + endpoint_rul
    rebuilt = (
        test.groupby("unit_id")["cycle"].transform("max") - test["cycle"] + endpoint_rul
    )
    if not np.array_equal(test["rul"].to_numpy(), rebuilt.to_numpy()):
        raise RuntimeError("Official test RUL does not match max cycle - cycle + endpoint RUL")
    features = m["feature_names"]
    test[features] = (test[features] - np.asarray(m["scaler_mean"])) / np.asarray(m["scaler_scale"])
    x, y = build_windows(test, features, m["window_size"], m["warning_rul"])
    window_size = m["window_size"]
    last_rows = []
    for _, engine in test.groupby("unit_id", sort=True):
        end = engine.iloc[-1]
        if len(engine) >= window_size:
            last_rows.append(end)
    if len(last_rows) != len(endpoints):
        raise RuntimeError(
            f"Expected one final window per engine, got {len(last_rows)} for {len(endpoints)} endpoints"
        )
    last = np.cumsum([len(g) - window_size + 1 for _, g in test.groupby("unit_id")]) - 1
    probabilities = {}
    evals = {}
    for variant in ["fp32", "int8"]:
        session = ort.InferenceSession(
            str(ROOT / "models" / f"tiny_cnn_{variant}.onnx"), providers=["CPUExecutionProvider"]
        )
        logits = session.run(None, {"sensor_window": x})[0].reshape(-1)
        p = 1 / (1 + np.exp(-logits))
        probabilities[variant] = p
        pred = p >= threshold
        evals[variant] = {}
        for scope, ix in [("all_windows", np.arange(len(y))), ("last_window_per_engine", last)]:
            yy = y[ix]
            pp = p[ix]
            yp = pred[ix]
            matrix = confusion_matrix(yy, yp, labels=[0, 1])
            tn, fp, fn, tp = matrix.ravel().tolist()
            evals[variant][scope] = {
                "samples": len(ix),
                "positives": int(yy.sum()),
                "accuracy": accuracy_score(yy, yp),
                "precision": precision_score(yy, yp, zero_division=0),
                "recall": recall_score(yy, yp, zero_division=0),
                "f1": f1_score(yy, yp, zero_division=0),
                "roc_auc": roc_auc_score(yy, pp),
                "confusion_matrix": {
                    "labels": {"rows": "true 0, true 1", "columns": "predicted 0, predicted 1"},
                    "matrix": matrix.tolist(),
                    "tn": tn,
                    "fp": fp,
                    "fn": fn,
                    "tp": tp,
                },
            }
    pd.DataFrame(
        {
            "engine_id": [int(row.unit_id) for row in last_rows],
            "cycle": [int(row.cycle) for row in last_rows],
            "true_rul": [float(row.rul) for row in last_rows],
            "true_label": y[last].astype(int),
            "fp32_probability": probabilities["fp32"][last],
            "fp32_predicted_class": (probabilities["fp32"][last] >= threshold).astype(int),
            "int8_probability": probabilities["int8"][last],
            "int8_predicted_class": (probabilities["int8"][last] >= threshold).astype(int),
        }
    ).to_csv(OUT / "official-test-last-window.csv", index=False)
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
