"""Optional Isolation Forest training with explicit artifact metadata."""

from __future__ import annotations

from pathlib import Path

from .features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, feature_vector


def train_isolation_forest(tracks: list[list[dict]], artifact_path: Path, *, model_version: str = "if-v1") -> dict:
    try:
        from sklearn.ensemble import IsolationForest
        import joblib
    except ImportError as exc:
        raise RuntimeError("install the optional ML dependencies before training Isolation Forest") from exc
    vectors = [feature_vector(track) for track in tracks if track]
    if len(vectors) < 2:
        raise ValueError("at least two non-empty tracks are required")
    model = IsolationForest(random_state=42, n_estimators=100, contamination="auto")
    model.fit(vectors)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "model_version": model_version, "feature_schema_version": FEATURE_SCHEMA_VERSION, "feature_names": FEATURE_NAMES, "training_rows": len(vectors)}, artifact_path)
    return {"artifact_path": str(artifact_path), "model_version": model_version, "feature_schema_version": FEATURE_SCHEMA_VERSION, "training_rows": len(vectors)}
