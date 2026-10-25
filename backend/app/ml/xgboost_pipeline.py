"""Validated XGBoost training contract; no fake model is activated."""

from __future__ import annotations

from collections import Counter
from pathlib import Path


VALID_LABELS = {"normal", "spoofing"}


def validate_labelled_rows(rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("labelled dataset is empty")
    labels = [row.get("label") for row in rows]
    unknown = sorted({label for label in labels if label not in VALID_LABELS})
    if unknown:
        raise ValueError(f"unknown or unverified labels are not trainable: {unknown}")
    groups = [row.get("sequence_id") or row.get("aircraft_id") for row in rows]
    if any(not group for group in groups):
        raise ValueError("each row requires an aircraft_id or sequence_id")
    if any(not isinstance(row.get("features"), list) or not row["features"] for row in rows):
        raise ValueError("each labelled row requires a non-empty numeric features list")
    if any(any(not isinstance(value, (int, float)) for value in row["features"]) for row in rows):
        raise ValueError("features must be numeric")
    return {"rows": len(rows), "class_balance": dict(Counter(labels)), "group_count": len(set(groups))}


def _chronological_group_split(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        group = str(row.get("sequence_id") or row.get("aircraft_id"))
        groups.setdefault(group, []).append(row)
    ordered = sorted(groups.values(), key=lambda group: max(int(row.get("observed_at", 0)) for row in group))
    count = len(ordered)
    if count < 3:
        raise ValueError("at least three independent aircraft/sequence groups are required")
    train_end = max(1, int(count * 0.6))
    validation_end = max(train_end + 1, int(count * 0.8))
    return [row for group in ordered[:train_end] for row in group], [row for group in ordered[train_end:validation_end] for row in group], [row for group in ordered[validation_end:] for row in group]


def train_xgboost(rows: list[dict], artifact_path: Path, *, model_version: str = "xgb-v1") -> dict:
    """Train a real binary classifier when validated labels are supplied.

    Rows are grouped by aircraft/sequence id and split chronologically so
    overlapping windows cannot leak between partitions. The resulting score
    is not called calibrated unless a separate calibration evaluation is
    added to the artifact metadata.
    """
    metadata = validate_labelled_rows(rows)
    train, validation, test = _chronological_group_split(rows)
    if {row["label"] for row in train} != VALID_LABELS:
        raise ValueError("training partition must contain both normal and spoofing labels")
    try:
        from xgboost import XGBClassifier
        import joblib
        from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score
    except ImportError as exc:
        raise RuntimeError("install backend/requirements-ml.txt before XGBoost training") from exc
    label = {"normal": 0, "spoofing": 1}
    model = XGBClassifier(
        n_estimators=100, max_depth=4, learning_rate=0.05, subsample=0.9,
        colsample_bytree=0.9, random_state=42, eval_metric="logloss",
    )
    model.fit([row["features"] for row in train], [label[row["label"]] for row in train], eval_set=[([row["features"] for row in validation], [label[row["label"]] for row in validation])], verbose=False)
    predictions = model.predict([row["features"] for row in test])
    truth = [label[row["label"]] for row in test]
    metrics = {
        "precision": float(precision_score(truth, predictions, zero_division=0)),
        "recall": float(recall_score(truth, predictions, zero_division=0)),
        "f1": float(f1_score(truth, predictions, zero_division=0)),
        "confusion_matrix": confusion_matrix(truth, predictions).tolist(),
        "test_rows": len(test),
    }
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "model_version": model_version, "label_map": label, "calibrated": False, "metadata": metadata, "evaluation": metrics}, artifact_path)
    return {"artifact_path": str(artifact_path), "model_version": model_version, "evaluation": metrics, "metadata": metadata}
