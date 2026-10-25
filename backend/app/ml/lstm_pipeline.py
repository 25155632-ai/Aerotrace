"""Sequence preparation and optional PyTorch training boundary."""

from __future__ import annotations

from pathlib import Path


def build_lstm_model(feature_count: int):
    try:
        from torch import nn
    except ImportError as exc:
        raise RuntimeError("PyTorch is required for the LSTM model") from exc

    class TrajectoryLSTM(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.rnn = nn.LSTM(feature_count, 16, batch_first=True)
            self.output = nn.Linear(16, 1)

        def forward(self, values):
            _output, (hidden, _cell) = self.rnn(values)
            return self.output(hidden[-1]).squeeze(-1)

    return TrajectoryLSTM()


def prepare_sequences(rows: list[dict], sequence_length: int = 8) -> list[list[dict]]:
    if sequence_length < 2 or sequence_length > 256:
        raise ValueError("sequence_length must be between 2 and 256")
    ordered = sorted(rows, key=lambda row: int(row["observed_at"]))
    if any(int(b["observed_at"]) < int(a["observed_at"]) for a, b in zip(ordered, ordered[1:])):
        raise ValueError("sequence rows must be chronological")
    return [ordered[index:index + sequence_length] for index in range(0, max(0, len(ordered) - sequence_length + 1))]


def train_lstm(rows: list[dict], artifact_path: Path, *, sequence_length: int = 8, epochs: int = 10, model_version: str = "lstm-v1") -> dict:
    """Train a small supervised LSTM over precomputed feature sequences.

    The input contract is one row per timestamp with ``sequence_id``,
    ``observed_at``, numeric ``features``, and a validated ``label``. The
    output is a classifier logit/probability only when the artifact is active;
    it is not described as calibrated without a separate calibration run.
    """
    if not rows:
        raise ValueError("sequence dataset is empty")
    if any(row.get("label") not in {"normal", "spoofing"} for row in rows):
        raise ValueError("unknown or unverified labels are not trainable")
    try:
        import torch
        from torch import nn
    except ImportError as exc:
        raise RuntimeError("install backend/requirements-ml.txt before LSTM training") from exc
    torch.manual_seed(42)
    by_sequence: dict[str, list[dict]] = {}
    for row in rows:
        by_sequence.setdefault(str(row.get("sequence_id") or row.get("aircraft_id") or ""), []).append(row)
    sequences = []
    for sequence_id, sequence_rows in by_sequence.items():
        if not sequence_id:
            raise ValueError("each sequence requires sequence_id or aircraft_id")
        ordered = sorted(sequence_rows, key=lambda row: int(row["observed_at"]))
        windows = prepare_sequences(ordered, sequence_length)
        for window in windows:
            labels = {row["label"] for row in window}
            if len(labels) != 1:
                raise ValueError("a sequence window must have one target label")
            sequences.append(([[float(value) for value in row["features"]] for row in window], 1.0 if "spoofing" in labels else 0.0))
    if len(sequences) < 3:
        raise ValueError("at least three chronological sequence windows are required")
    feature_count = len(sequences[0][0][0])
    if any(len(step) != feature_count for window, _label in sequences for step in window):
        raise ValueError("all sequence feature vectors must have the same width")

    model = build_lstm_model(feature_count)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    loss_fn = nn.BCEWithLogitsLoss()
    split = max(1, int(len(sequences) * 0.8))
    train_rows = sequences[:split]
    test_rows = sequences[split:]
    if not test_rows:
        test_rows = train_rows[-1:]
    for _epoch in range(max(1, min(epochs, 100))):
        model.train()
        optimizer.zero_grad()
        inputs = torch.tensor([window for window, _label in train_rows], dtype=torch.float32)
        targets = torch.tensor([label for _window, label in train_rows], dtype=torch.float32)
        loss = loss_fn(model(inputs), targets)
        loss.backward()
        optimizer.step()
    model.eval()
    with torch.no_grad():
        test_inputs = torch.tensor([window for window, _label in test_rows], dtype=torch.float32)
        test_targets = [label for _window, label in test_rows]
        probabilities = torch.sigmoid(model(test_inputs)).tolist()
    predictions = [1.0 if value >= 0.5 else 0.0 for value in probabilities]
    accuracy = sum(int(prediction == target) for prediction, target in zip(predictions, test_targets)) / len(test_targets)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(), "model_version": model_version, "sequence_length": sequence_length, "feature_count": feature_count, "semantics": "uncalibrated supervised sequence score", "evaluation": {"accuracy": accuracy, "test_windows": len(test_rows)}}, artifact_path)
    return {"artifact_path": str(artifact_path), "model_version": model_version, "evaluation": {"accuracy": accuracy, "test_windows": len(test_rows)}, "semantics": "uncalibrated supervised sequence score"}
