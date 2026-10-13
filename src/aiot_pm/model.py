from __future__ import annotations

from torch import Tensor, nn


class TinyConvNet(nn.Module):
    def __init__(self, input_features: int) -> None:
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv1d(input_features, 24, kernel_size=5, padding=2),
            nn.BatchNorm1d(24),
            nn.ReLU(),
            nn.MaxPool1d(kernel_size=2),
            nn.Conv1d(24, 32, kernel_size=3, padding=1),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(1),
        )
        self.classifier = nn.Linear(32, 1)

    def forward(self, values: Tensor) -> Tensor:
        features = self.features(values).squeeze(-1)
        return self.classifier(features).squeeze(-1)
