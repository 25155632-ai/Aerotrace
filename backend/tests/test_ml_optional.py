from app.ml.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, extract_track_features
from app.ml.lstm_pipeline import prepare_sequences
from app.ml.xgboost_pipeline import validate_labelled_rows


def test_feature_schema_is_deterministic_and_sequences_are_chronological():
    rows = [
        {"observed_at": 2, "latitude": 1, "longitude": 1, "velocity_ms": 10, "heading_deg": 20},
        {"observed_at": 1, "latitude": 0, "longitude": 0, "velocity_ms": 8, "heading_deg": 10},
    ]
    features = extract_track_features(rows)
    assert tuple(features) == FEATURE_NAMES
    assert FEATURE_SCHEMA_VERSION == "trajectory-v1"
    sequences = prepare_sequences(rows, sequence_length=2)
    assert len(sequences) == 1
    assert sequences[0][0]["observed_at"] == 1


def test_supervised_validation_rejects_unknown_labels():
    try:
        validate_labelled_rows([{"aircraft_id": "a", "label": "unknown"}])
    except ValueError as exc:
        assert "unknown" in str(exc)
    else:
        raise AssertionError("unknown labels must not enter a binary classifier")
