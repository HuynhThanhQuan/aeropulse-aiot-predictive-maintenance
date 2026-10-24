from __future__ import annotations

import copy
import json
import random
import shutil
import tempfile
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
from onnxruntime.quantization import (
    CalibrationDataReader,
    QuantFormat,
    QuantType,
    quantize_static,
)
from onnxruntime.quantization.shape_inference import quant_pre_process
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from .config import load_config, project_path
from .data import PreparedData, prepare_data
from .model import TinyConvNet

# Luồng offline: train PyTorch → export ONNX FP32 → calibration INT8 → chọn ngưỡng.
# Hướng dẫn các điểm tune: docs/source-guide-vi.md.

class ArrayCalibrationReader(CalibrationDataReader):
    def __init__(self, values: np.ndarray, input_name: str = "sensor_window") -> None:
        self.input_name = input_name
        self.iterator = iter(values)

    def get_next(self) -> dict[str, np.ndarray] | None:
        try:
            value = next(self.iterator)
        except StopIteration:
            return None
        # Calibration chạy forward trên cửa sổ train đã chuẩn hóa, không cập nhật trọng số.
        # Thêm trục batch: [14, 30] → [1, 14, 30]; tên input phải khớp graph ONNX.
        return {self.input_name: value[np.newaxis, ...].astype(np.float32)}


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def predict_torch(model: nn.Module, values: np.ndarray, batch_size: int) -> np.ndarray:
    model.eval()
    predictions: list[np.ndarray] = []
    loader = DataLoader(torch.from_numpy(values), batch_size=batch_size, shuffle=False)
    with torch.no_grad():
        for batch in loader:
            predictions.append(torch.sigmoid(model(batch)).cpu().numpy())
    return np.concatenate(predictions)


def choose_threshold(labels: np.ndarray, probabilities: np.ndarray) -> float:
    # Grid search 81 ngưỡng 0.10..0.90, bước 0.01, tối đa F1 trên VALIDATION.
    # Nếu hòa F1, argmax chọn ngưỡng thấp nhất. Không dùng official test để chọn ngưỡng.
    # Muốn ưu tiên recall: đổi mục tiêu sang F2 hoặc recall với ràng buộc precision,
    # rồi đánh giá lại; đây là tune quyết định, không phải học lại trọng số ONNX.
    candidates = np.linspace(0.10, 0.90, 81)
    scores = [f1_score(labels, probabilities >= threshold) for threshold in candidates]
    return float(candidates[int(np.argmax(scores))])


def classification_metrics(
    labels: np.ndarray, probabilities: np.ndarray, threshold: float
) -> dict[str, float]:
    predicted = probabilities >= threshold
    return {
        "accuracy": float(accuracy_score(labels, predicted)),
        "precision": float(precision_score(labels, predicted, zero_division=0)),
        "recall": float(recall_score(labels, predicted, zero_division=0)),
        "f1": float(f1_score(labels, predicted, zero_division=0)),
        "roc_auc": float(roc_auc_score(labels, probabilities)),
    }


def train_model(data: PreparedData, config: dict) -> tuple[TinyConvNet, list[dict[str, float]]]:
    settings = config["training"]
    model = TinyConvNet(len(data.feature_names))
    positives = max(float(data.y_train.sum()), 1.0)
    negatives = max(float(len(data.y_train) - data.y_train.sum()), 1.0)
    # Lớp cảnh báo ít hơn: tăng trọng số loss của mẫu dương theo tỷ lệ âm/dương.
    # Loss nhận LOGIT; sigmoid chỉ dùng khi đánh giá/suy luận, không áp dụng trước loss.
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(negatives / positives))
    optimizer = torch.optim.Adam(model.parameters(), lr=settings["learning_rate"])
    train_dataset = TensorDataset(torch.from_numpy(data.x_train), torch.from_numpy(data.y_train))
    train_loader = DataLoader(
        train_dataset, batch_size=settings["batch_size"], shuffle=True, drop_last=False
    )

    # Chọn checkpoint bằng validation F1, không dùng training loss hay test score.
    best_state = copy.deepcopy(model.state_dict())
    best_f1 = -1.0
    stale_epochs = 0
    history: list[dict[str, float]] = []
    for epoch in range(1, settings["epochs"] + 1):
        model.train()
        running_loss = 0.0
        for features, labels in train_loader:
            optimizer.zero_grad(set_to_none=True)
            logits = model(features)
            loss = criterion(logits, labels)
            loss.backward()
            optimizer.step()
            running_loss += float(loss.item()) * len(labels)

        validation_probabilities = predict_torch(model, data.x_validation, settings["batch_size"])
        threshold = choose_threshold(data.y_validation, validation_probabilities)
        metrics = classification_metrics(data.y_validation, validation_probabilities, threshold)
        row = {
            "epoch": float(epoch),
            "train_loss": running_loss / len(train_dataset),
            "validation_f1": metrics["f1"],
            "threshold": threshold,
        }
        history.append(row)
        print(
            f"epoch={epoch:02d} loss={row['train_loss']:.4f} "
            f"val_f1={metrics['f1']:.4f} threshold={threshold:.2f}"
        )
        if metrics["f1"] > best_f1 + 1e-4:
            best_f1 = metrics["f1"]
            best_state = copy.deepcopy(model.state_dict())
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= settings["patience"]:
                print("Early stopping")
                break

    model.load_state_dict(best_state)
    model.eval()
    return model, history


def export_models(model: TinyConvNet, data: PreparedData, config: dict) -> tuple[Path, Path]:
    model_dir = project_path(config["project"]["model_dir"])
    model_dir.mkdir(parents=True, exist_ok=True)
    fp32_path = model_dir / "tiny_cnn_fp32.onnx"
    int8_path = model_dir / "tiny_cnn_int8.onnx"
    example = torch.from_numpy(data.x_train[:1])
    # ONNX Runtime shape inference opens paths with a non-Unicode API on Windows.
    # Stage export under an ASCII temp directory, then copy the artifacts back.
    with tempfile.TemporaryDirectory(prefix="aiot-onnx-") as temp_dir:
        staged_fp32 = Path(temp_dir) / "tiny_cnn_fp32.onnx"
        staged_int8 = Path(temp_dir) / "tiny_cnn_int8.onnx"
        preprocessed_path = Path(temp_dir) / "tiny_cnn_fp32_preprocessed.onnx"
        # Export chỉ đổi cách biểu diễn graph + trọng số đã học; không huấn luyện lại.
        # Batch linh hoạt, nhưng số sensor và độ dài cửa sổ vẫn cố định lúc export.
        # opset=17 là phiên bản tập toán tử; không phải mức nén hay tham số độ chính xác.
        torch.onnx.export(
            model,
            example,
            staged_fp32,
            input_names=["sensor_window"],
            output_names=["failure_logit"],
            dynamic_axes={"sensor_window": {0: "batch"}, "failure_logit": {0: "batch"}},
            opset_version=17,
            dynamo=False,
        )
        # Suy luận shape và tối ưu graph trước quantization (ví dụ fusion nếu hỗ trợ).
        quant_pre_process(staged_fp32, preprocessed_path)
        # Lấy tối đa calibration_samples cửa sổ TRAIN rải đều theo chỉ số.
        # Đây chưa phải lấy mẫu phân tầng theo engine/rủi ro; độ đại diện cần kiểm tra.
        # Calibration đo dải activation để tính scale/zero-point, không cần nhãn.
        count = min(config["training"]["calibration_samples"], len(data.x_train))
        indexes = np.linspace(0, len(data.x_train) - 1, count, dtype=int)
        # PTQ tĩnh: lượng tử hóa sau train, không phải quantization-aware training (QAT).
        # QDQ chèn QuantizeLinear/DequantizeLinear; x ≈ scale * (q - zero_point).
        # Activation uint8, weight int8; per_channel dùng scale riêng từng kênh weight.
        # Input/output graph vẫn float32; không tự ép sensor_window sang int8.
        # Các lựa chọn QDQ/dtype/per_channel đang nằm trong code, chưa có CLI/config.
        quantize_static(
            model_input=str(preprocessed_path),
            model_output=str(staged_int8),
            calibration_data_reader=ArrayCalibrationReader(data.x_train[indexes]),
            quant_format=QuantFormat.QDQ,
            activation_type=QuantType.QUInt8,
            weight_type=QuantType.QInt8,
            per_channel=True,
        )
        shutil.copy2(staged_fp32, fp32_path)
        shutil.copy2(staged_int8, int8_path)
    return fp32_path, int8_path


def onnx_probabilities(path: Path, values: np.ndarray) -> np.ndarray:
    # ONNX Runtime thực thi graph trên CPU; đầu ra là logit, cần sigmoid để lấy score.
    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    logits = session.run(None, {"sensor_window": values.astype(np.float32)})[0].reshape(-1)
    return 1.0 / (1.0 + np.exp(-logits))


def main() -> None:
    config = load_config()
    seed = int(config["training"]["seed"])
    seed_everything(seed)
    dataset_path = project_path(config["project"]["data_dir"]) / "train_FD001.txt"
    if not dataset_path.exists():
        raise SystemExit("Dataset not found. Run `aiot-download` first.")

    data = prepare_data(
        dataset_path,
        sensors=config["data"]["sensors"],
        window_size=config["data"]["window_size"],
        warning_rul=config["data"]["warning_rul"],
        validation_fraction=config["data"]["validation_engine_fraction"],
        seed=seed,
    )
    print(
        f"Prepared {len(data.x_train)} training and {len(data.x_validation)} validation windows"
    )
    model, history = train_model(data, config)
    model_dir = project_path(config["project"]["model_dir"])
    model_dir.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), model_dir / "tiny_cnn_state.pt")
    fp32_path, int8_path = export_models(model, data, config)

    # Lượng tử hóa có thể làm lệch score: chọn lại ngưỡng trên bản INT8 cuối cùng.
    # Ngưỡng là kết quả validation, không hard-code 0.64 hay chỉnh theo engine demo.
    int8_probabilities = onnx_probabilities(int8_path, data.x_validation)
    threshold = choose_threshold(data.y_validation, int8_probabilities)
    metrics = classification_metrics(data.y_validation, int8_probabilities, threshold)
    # Model + metadata là một bộ: thứ tự sensor, scaler và threshold phải đi cùng nhau.
    # main() ghi đè artifact ở các thư mục config; thử tune trong bản sao riêng.
    metadata = {
        "project": "C-MAPSS FD001 predictive maintenance",
        "model_type": "1D-CNN ONNX static INT8 QDQ",
        "feature_names": data.feature_names,
        "window_size": config["data"]["window_size"],
        "warning_rul": config["data"]["warning_rul"],
        "scaler_mean": data.scaler.mean_.tolist(),
        "scaler_scale": data.scaler.scale_.tolist(),
        "decision_threshold": threshold,
        "validation_metrics_int8": metrics,
        "train_engine_ids": data.train_engine_ids,
        "validation_engine_ids": data.validation_engine_ids,
        "input_name": "sensor_window",
        "output_name": "failure_logit",
    }
    (model_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    results_dir = project_path(config["project"]["results_dir"])
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "training_history.json").write_text(
        json.dumps(history, indent=2), encoding="utf-8"
    )
    processed_dir = project_path(config["project"]["processed_dir"])
    processed_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        processed_dir / "validation_windows.npz",
        values=data.x_validation,
        labels=data.y_validation,
    )
    print(f"Exported FP32 model: {fp32_path}")
    print(f"Exported static INT8 model: {int8_path}")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
