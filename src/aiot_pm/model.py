from __future__ import annotations

from torch import Tensor, nn


class TinyConvNet(nn.Module):
    def __init__(self, input_features: int) -> None:
        super().__init__()
        # Input [batch, sensor, cycle] = [B, 14, 30]; Conv1d trượt theo thời gian.
        # Tune 24/32 kênh hoặc kernel ở đây phải train và export lại cả bộ artifact.
        self.features = nn.Sequential(
            nn.Conv1d(input_features, 24, kernel_size=5, padding=2),
            nn.BatchNorm1d(24),
            nn.ReLU(),
            # Với cửa sổ 30: [B,24,30] → [B,24,15], giảm độ dài thời gian.
            nn.MaxPool1d(kernel_size=2),
            nn.Conv1d(24, 32, kernel_size=3, padding=1),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            # Gộp thời gian thành [B,32,1], tạo vector 32 đặc trưng cho mỗi cửa sổ.
            nn.AdaptiveAvgPool1d(1),
        )
        self.classifier = nn.Linear(32, 1)

    def forward(self, values: Tensor) -> Tensor:
        features = self.features(values).squeeze(-1)
        # Một logit mỗi cửa sổ, không phải số chu kỳ RUL dự đoán.
        return self.classifier(features).squeeze(-1)
