from __future__ import annotations

import gc
import json
import multiprocessing as mp
import time
from pathlib import Path
from queue import Empty

import numpy as np
import onnxruntime as ort
import psutil
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score

from .config import load_config, project_path


def sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-values))


def evaluate_model(
    model_path: Path,
    values: np.ndarray,
    labels: np.ndarray,
    threshold: float,
    latency_runs: int = 300,
) -> dict[str, float | int | str]:
    process = psutil.Process()
    gc.collect()
    rss_before = process.memory_info().rss
    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    rss_after_load = process.memory_info().rss
    input_name = session.get_inputs()[0].name
    logits = session.run(None, {input_name: values.astype(np.float32)})[0].reshape(-1)
    probabilities = sigmoid(logits)
    predicted = probabilities >= threshold

    warmups = min(20, len(values))
    for index in range(warmups):
        session.run(None, {input_name: values[index : index + 1].astype(np.float32)})
    latencies: list[float] = []
    for index in range(min(latency_runs, len(values))):
        started = time.perf_counter_ns()
        session.run(None, {input_name: values[index : index + 1].astype(np.float32)})
        latencies.append((time.perf_counter_ns() - started) / 1_000_000)
    rss_after_inference = process.memory_info().rss

    return {
        "model": model_path.name,
        "model_size_kb": model_path.stat().st_size / 1024,
        "accuracy": float(accuracy_score(labels, predicted)),
        "precision": float(precision_score(labels, predicted, zero_division=0)),
        "recall": float(recall_score(labels, predicted, zero_division=0)),
        "f1": float(f1_score(labels, predicted, zero_division=0)),
        "roc_auc": float(roc_auc_score(labels, probabilities)),
        "latency_mean_ms": float(np.mean(latencies)),
        "latency_p50_ms": float(np.percentile(latencies, 50)),
        "latency_p95_ms": float(np.percentile(latencies, 95)),
        "throughput_fps": float(1000 / np.mean(latencies)),
        "rss_before_mb": rss_before / (1024**2),
        "rss_after_load_mb": rss_after_load / (1024**2),
        "rss_after_inference_mb": rss_after_inference / (1024**2),
        "samples_evaluated": len(labels),
        "latency_runs": len(latencies),
    }


def benchmark_worker(
    model_path: str, processed_path: str, threshold: float, output: mp.Queue
) -> None:
    archive = np.load(processed_path)
    result = evaluate_model(
        Path(model_path), archive["values"], archive["labels"], threshold
    )
    output.put(result)


def evaluate_model_isolated(
    model_path: Path, processed_path: Path, threshold: float
) -> dict[str, float | int | str]:
    context = mp.get_context("spawn")
    output = context.Queue()
    process = context.Process(
        target=benchmark_worker,
        args=(str(model_path), str(processed_path), threshold, output),
    )
    process.start()
    process.join(timeout=120)
    if process.is_alive():
        process.terminate()
        process.join()
        raise TimeoutError(f"Benchmark timed out for {model_path.name}")
    if process.exitcode != 0:
        raise RuntimeError(f"Benchmark worker failed for {model_path.name}")
    try:
        return output.get(timeout=5)
    except Empty as exc:
        raise RuntimeError(f"Benchmark worker returned no result for {model_path.name}") from exc


def markdown_table(rows: list[dict[str, float | int | str]]) -> str:
    columns = [
        ("model", "Model"),
        ("model_size_kb", "Size (KiB)"),
        ("f1", "F1"),
        ("roc_auc", "ROC-AUC"),
        ("latency_mean_ms", "Mean latency (ms)"),
        ("latency_p95_ms", "P95 latency (ms)"),
        ("rss_after_load_mb", "RSS after load (MiB)"),
    ]
    lines = [
        "| " + " | ".join(title for _, title in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        rendered: list[str] = []
        for key, _ in columns:
            value = row[key]
            rendered.append(f"{value:.4f}" if isinstance(value, float) else str(value))
        lines.append("| " + " | ".join(rendered) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    config = load_config()
    processed_path = project_path(config["project"]["processed_dir"]) / "validation_windows.npz"
    metadata_path = project_path(config["edge"]["metadata_path"])
    model_dir = project_path(config["project"]["model_dir"])
    if not processed_path.exists() or not metadata_path.exists():
        raise SystemExit("Training artifacts not found. Run `aiot-train` first.")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    threshold = float(metadata["decision_threshold"])

    rows = [
        evaluate_model_isolated(model_dir / "tiny_cnn_fp32.onnx", processed_path, threshold),
        evaluate_model_isolated(model_dir / "tiny_cnn_int8.onnx", processed_path, threshold),
    ]
    rows[1]["size_reduction_percent"] = float(
        100 * (1 - float(rows[1]["model_size_kb"]) / float(rows[0]["model_size_kb"]))
    )
    results_dir = project_path(config["project"]["results_dir"])
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "benchmark.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (results_dir / "benchmark.md").write_text(markdown_table(rows), encoding="utf-8")
    print(markdown_table(rows))
    print("Raw benchmark data written to results/benchmark.json")


if __name__ == "__main__":
    main()
