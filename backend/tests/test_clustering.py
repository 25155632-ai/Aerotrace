"""Clustering and interpretation: DBSCAN grouping, correlated, single,
distant, position-loss, plus DBSCAN-specific behaviour (chaining,
noise, time-only neighbours)."""

from app.detectors.clustering import (
    clusters_for_interpretation,
    dbscan_clusters,
    interpret_clusters,
    interpret_quality_clusters,
)
from app.detectors.detectors import Detection


def detection(icao24, at, lat, lon, rule="teleport", severity="high", kind="anomaly"):
    return Detection(
        rule=rule,
        severity=severity,
        kind=kind,
        icao24=icao24,
        detected_at=at,
        latitude=lat,
        longitude=lon,
        evidence={},
    )


def test_correlated_anomalies_from_three_aircraft_become_one_event():
    detections = [
        detection("111aaa", 1000, 20.4, 74.4),
        detection("222bbb", 1000, 20.6, 74.6),
        detection("333ccc", 1000, 20.8, 74.8),
    ]
    clusters = clusters_for_interpretation(detections)
    assert len(clusters) == 1
    events = interpret_clusters(clusters)
    assert len(events) == 1
    assert events[0].kind == "correlated_anomalies"
    assert events[0].aircraft == ["111aaa", "222bbb", "333ccc"]
    assert "not confirmation" in events[0].caveat


def test_single_high_anomaly_becomes_single_aircraft_event():
    events = interpret_clusters(
        clusters_for_interpretation([detection("def456", 1000, 25.0, 80.0)])
    )
    assert len(events) == 1
    assert events[0].kind == "single_aircraft_anomaly"


def test_single_medium_anomaly_alone_is_not_an_event():
    events = interpret_clusters(
        clusters_for_interpretation(
            [detection("abc123", 1000, 19.0, 72.0, severity="medium")]
        )
    )
    assert events == []


def test_distant_anomalies_do_not_correlate():
    detections = [
        detection("aaa111", 1000, 19.0, 72.0),
        detection("bbb222", 1000, 40.0, 120.0),
    ]
    clusters = clusters_for_interpretation(detections)
    assert len(clusters) == 2
    events = interpret_clusters(clusters)
    assert len(events) == 2
    assert all(e.kind == "single_aircraft_anomaly" for e in events)


def test_position_loss_cluster_from_multiple_aircraft():
    findings = [
        detection("aaa111", 1000, 19.0, 72.0, rule="track_gap", severity="low", kind="data_quality"),
        detection("bbb222", 1100, 19.5, 72.5, rule="missing_position", severity="low", kind="data_quality"),
    ]
    events = interpret_quality_clusters(clusters_for_interpretation(findings))
    assert len(events) == 1
    assert events[0].kind == "position_loss_cluster"
    assert events[0].aircraft == ["aaa111", "bbb222"]


def test_single_aircraft_quality_findings_are_not_a_position_loss_event():
    findings = [
        detection("aaa111", 1000, 19.0, 72.0, rule="track_gap", severity="low", kind="data_quality"),
    ]
    assert interpret_quality_clusters(clusters_for_interpretation(findings)) == []


def test_dbscan_chains_through_a_core_point_but_not_through_noise():
    # A-B neighbours, B-C neighbours, A-C not neighbours: all three
    # still one cluster because B is a core point bridging them.
    # Times are equal; longitudes step ~55 km apart at lat 20.
    chained = [
        detection("aaa111", 1000, 20.0, 74.00),
        detection("bbb222", 1000, 20.0, 74.55),
        detection("ccc333", 1000, 20.0, 75.10),
    ]
    clusters, noise = dbscan_clusters(chained)
    assert len(clusters) == 1 and noise == []
    assert len(clusters[0].members) == 3


def test_dbscan_marks_an_isolated_detection_as_noise():
    detections = [
        detection("aaa111", 1000, 20.0, 74.0),
        detection("bbb222", 1000, 20.0, 74.5),
        detection("ccc333", 9000, 40.0, 120.0),
    ]
    clusters, noise = dbscan_clusters(detections)
    assert len(clusters) == 1
    assert [d.icao24 for d in noise] == ["ccc333"]


def test_dbscan_positionless_detections_neighbour_on_time_alone():
    detections = [
        detection("aaa111", 1000, None, None, rule="track_gap", severity="low", kind="data_quality"),
        detection("bbb222", 1100, None, None, rule="track_gap", severity="low", kind="data_quality"),
    ]
    clusters, noise = dbscan_clusters(detections)
    assert len(clusters) == 1 and noise == []


def test_dbscan_min_samples_three_leaves_a_pair_as_noise():
    detections = [
        detection("aaa111", 1000, 20.0, 74.0),
        detection("bbb222", 1000, 20.0, 74.5),
    ]
    clusters, noise = dbscan_clusters(detections, min_samples=3)
    assert clusters == [] and len(noise) == 2
