"""One-shot consistency check of the current training artifacts. Not part of the pipeline."""
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
from sklearn.metrics import f1_score

from aiot_pm.data import prepare_data
from aiot_pm.model import TinyConvNet

ROOT = Path(__file__).resolve().parents[1]
meta = json.loads((ROOT / "models/metadata.json").read_text(encoding="utf-8"))
archive = np.load(ROOT / "data/processed/validation_windows.npz")
values, labels = archive["values"], archive["labels"]

print("npz keys", archive.files)
print("values", values.shape, values.dtype, "labels", labels.shape, "positives", int(labels.sum()))
print("window", meta["window_size"], "features", len(meta["feature_names"]), "threshold", meta["decision_threshold"])

cfg_sensors = [2, 3, 4, 7, 8, 9, 11, 12, 13, 14, 15, 17, 20, 21]
print("feature order matches config", meta["feature_names"] == [f"sensor_{s}" for s in cfg_sensors])

data = prepare_data(
    ROOT / "data/raw/train_FD001.txt",
    sensors=cfg_sensors,
    window_size=30,
    warning_rul=30,
    validation_fraction=0.2,
    seed=42,
)
print("engine split matches", data.train_engine_ids == meta["train_engine_ids"] and data.validation_engine_ids == meta["validation_engine_ids"])
print("scaler mean max abs", float(np.max(np.abs(data.scaler.mean_ - np.asarray(meta["scaler_mean"])))))
print("scaler scale max abs", float(np.max(np.abs(data.scaler.scale_ - np.asarray(meta["scaler_scale"])))))
print("val windows equal", np.allclose(data.x_validation, values), "labels equal", np.array_equal(data.y_validation, labels))
print("val count", len(data.x_validation), "train count", len(data.x_train), "val positives", int(data.y_validation.sum()))

state = torch.load(ROOT / "models/tiny_cnn_state.pt", map_location="cpu", weights_only=True)
model = TinyConvNet(14)
model.load_state_dict(state)
model.eval()
with torch.no_grad():
    torch_p = torch.sigmoid(model(torch.from_numpy(values))).numpy()

for variant in ["fp32", "int8"]:
    path = ROOT / "models" / f"tiny_cnn_{variant}.onnx"
    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    inp, out = session.get_inputs()[0], session.get_outputs()[0]
    print(variant, "input", inp.name, inp.shape, inp.type, "output", out.name, out.shape, out.type)
    logits = session.run(None, {"sensor_window": values.astype(np.float32)})[0].reshape(-1)
    p = 1 / (1 + np.exp(-logits))
    pred = p >= meta["decision_threshold"]
    print(
        variant,
        "finite", bool(np.isfinite(p).all()),
        "range", float(p.min()), float(p.max()),
        "f1@meta", float(f1_score(labels, pred)),
        "max abs vs torch", float(np.max(np.abs(p - torch_p))),
    )
