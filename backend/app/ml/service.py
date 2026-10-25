"""Truthful model lifecycle and optional inference boundary."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from .features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, feature_vector

SEMANTICS = {
    "isolation_forest": "Isolation Forest anomaly score; not a spoofing probability.",
    "xgboost": "Uncalibrated supervised classifier score unless artifact metadata explicitly records calibration.",
    "lstm": "Sequence-model output with semantics defined by the trained artifact; never assumed to be a spoofing probability.",
}


def model_status(name: str) -> dict:
    if name == "isolation_forest":
        installed = importlib.util.find_spec("sklearn") is not None
    elif name == "xgboost":
        installed = importlib.util.find_spec("xgboost") is not None
    else:
        installed = importlib.util.find_spec("torch") is not None
    return {"name": name, "installed": installed, "semantics": SEMANTICS[name]}


def _artifact_for(name: str, settings) -> Path | None:
    return {
        "isolation_forest": settings.isolation_forest_artifact,
        "xgboost": settings.xgboost_artifact,
        "lstm": settings.lstm_artifact,
    }[name]


def score_model(name: str, observations: list[dict], settings) -> dict:
    status = model_status(name)
    artifact = _artifact_for(name, settings)
    if not status["installed"]:
        return {"model": name, "status": "not_installed", "score": None, "score_semantics": status["semantics"], "evidence": {"reason": "optional dependency is not installed", "feature_schema_version": FEATURE_SCHEMA_VERSION}}
    if artifact is None or not artifact.exists():
        return {"model": name, "status": "not_trained", "score": None, "score_semantics": status["semantics"], "evidence": {"reason": "validated model artifact is not configured", "feature_schema_version": FEATURE_SCHEMA_VERSION}}
    try:
        vector = [feature_vector(observations)]
        if name in {"isolation_forest", "xgboost"}:
            import joblib
            artifact_payload = joblib.load(artifact)
            if name == "isolation_forest" and artifact_payload.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
                return {"model": name, "status": "artifact_incompatible", "score": None, "score_semantics": status["semantics"], "evidence": {"reason": "feature schema mismatch"}}
            if name == "isolation_forest":
                score = float(artifact_payload["model"].decision_function(vector)[0])
            else:
                if not artifact_payload.get("label_map") or not artifact_payload.get("model_version"):
                    return {"model": name, "status": "artifact_incompatible", "score": None, "score_semantics": status["semantics"], "evidence": {"reason": "classifier artifact metadata is incomplete"}}
                score = float(artifact_payload["model"].predict_proba(vector)[0][1])
        else:
            import torch
            payload = torch.load(artifact, map_location="cpu", weights_only=True)
            sequence_length = int(payload["sequence_length"])
            feature_count = int(payload["feature_count"])
            if len(vector[0]) != feature_count:
                return {"model": name, "status": "artifact_incompatible", "score": None, "score_semantics": status["semantics"], "evidence": {"reason": "feature width mismatch"}}
            # The active LSTM artifact is only scored when enough chronological
            # windows are supplied; no interpolation is performed.
            if len(observations) < sequence_length:
                return {"model": name, "status": "insufficient_sequence", "score": None, "score_semantics": status["semantics"], "evidence": {"required_length": sequence_length}}
            from .lstm_pipeline import train_lstm  # keeps the architecture contract centralized
            del train_lstm
            return {"model": name, "status": "artifact_loaded_inference_not_implemented", "score": None, "score_semantics": status["semantics"], "evidence": {"reason": "artifact metadata validated; sequence loader requires the training feature contract"}}
        return {"model": name, "status": "available", "score": score, "model_version": artifact_payload.get("model_version"), "score_semantics": status["semantics"], "evidence": {"feature_schema_version": FEATURE_SCHEMA_VERSION, "feature_names": FEATURE_NAMES, "observation_count": len(observations)}}
    except Exception as exc:
        return {"model": name, "status": "inference_failed", "score": None, "score_semantics": status["semantics"], "evidence": {"reason": type(exc).__name__}}
