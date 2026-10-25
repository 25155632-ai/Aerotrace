"""Clustering and event interpretation. Separate from detection.

Clustering is DBSCAN over (time, space): two detections are
neighbours when they are within eps_time_s seconds AND eps_km
kilometres (time alone when either lacks a position). Core points
have at least min_samples neighbours (themselves included);
clusters are density-connected components plus their border
points. Unlike the previous greedy grouping, DBSCAN does not depend
on input order except for border points claimed by two adjacent
clusters, and a lone detection cannot join a cluster by chaining
through a single neighbour unless that neighbour is itself a core
point. Noise detections are wrapped as singleton clusters by
clusters_for_interpretation() so interpretation keeps its rules.

Interpretation rules (also documented, not hidden in code):
- A cluster with >= 2 distinct aircraft becomes a
  "correlated_anomalies" candidate event: several aircraft showing
  unusual movement in the same time/place window is the pattern that
  most warrants investigation, because a single-aircraft glitch
  cannot explain it. It is still not proof of interference.
- A cluster with a single aircraft but a high-severity detection
  becomes a "single_aircraft_anomaly" candidate event.
- A cluster of only medium/low single-aircraft detections is NOT an
  event: the detections stand on their own without event packaging.
- Data-quality clusters (gaps / missing positions) from >= 2 distinct
  aircraft become a "position_loss_cluster" event, labelled a
  potential interference indicator — position loss clusters are also
  caused by ordinary receiver-coverage loss, and the event text says so.

Every event carries the same caveat: it is a candidate for
investigation, not confirmation of GPS spoofing or jamming.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .detectors import Detection
from .features import haversine_km

CAVEAT = (
    "This is a candidate event for investigation, not confirmation of "
    "GPS spoofing or jamming. Tracking anomalies alone cannot prove "
    "interference."
)


@dataclass
class Cluster:
    members: list[Detection] = field(default_factory=list)

    @property
    def aircraft(self) -> list[str]:
        return sorted({m.icao24 for m in self.members})

    @property
    def start_at(self) -> int:
        return min(m.detected_at for m in self.members)

    @property
    def end_at(self) -> int:
        return max(m.detected_at for m in self.members)

    @property
    def centroid(self) -> tuple[float, float] | None:
        positioned = [
            (m.latitude, m.longitude)
            for m in self.members
            if m.latitude is not None and m.longitude is not None
        ]
        if not positioned:
            return None
        return (
            sum(lat for lat, _ in positioned) / len(positioned),
            sum(lon for _, lon in positioned) / len(positioned),
        )

    @property
    def max_severity(self) -> str:
        order = {"low": 0, "medium": 1, "high": 2}
        return max(self.members, key=lambda m: order[m.severity]).severity


@dataclass
class CandidateEvent:
    kind: str
    severity: str
    start_at: int
    end_at: int
    centroid_lat: float | None
    centroid_lon: float | None
    aircraft: list[str]
    members: list[Detection]
    summary: str
    caveat: str = CAVEAT

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "start_at": self.start_at,
            "end_at": self.end_at,
            "centroid_lat": self.centroid_lat,
            "centroid_lon": self.centroid_lon,
            "aircraft": self.aircraft,
            "member_rules": [m.rule for m in self.members],
            "member_icao24": [m.icao24 for m in self.members],
            "summary": self.summary,
            "caveat": self.caveat,
        }


def _dbscan_neighbors(
    index: int,
    detections: list[Detection],
    eps_time_s: int,
    eps_km: float,
) -> list[int]:
    """Indices within eps of detections[index]. Time is always
    required; space is required only when both detections are
    positioned — a positionless detection neighbours on time alone,
    mirroring the greedy placement rule."""
    anchor = detections[index]
    out: list[int] = []
    for j, other in enumerate(detections):
        if abs(anchor.detected_at - other.detected_at) > eps_time_s:
            continue
        if (
            anchor.latitude is None
            or anchor.longitude is None
            or other.latitude is None
            or other.longitude is None
        ):
            out.append(j)
            continue
        if haversine_km(anchor.latitude, anchor.longitude, other.latitude, other.longitude) <= eps_km:
            out.append(j)
    return out


def dbscan_clusters(
    detections: list[Detection],
    *,
    eps_time_s: int = 600,
    eps_km: float = 100.0,
    min_samples: int = 2,
) -> tuple[list[Cluster], list[Detection]]:
    """DBSCAN over (time, space): two detections are neighbours when
    they are within eps_time_s seconds AND eps_km kilometres (time
    alone when either lacks a position). A point with at least
    min_samples neighbours (itself included) is a core point;
    clusters are density-connected components of core points plus
    their border points. Everything else is returned as noise.

    Input order matters only for border points claimed by two
    adjacent clusters (standard DBSCAN); callers pass detections
    sorted by (detected_at, icao24, rule), so results are
    deterministic. Noise is returned separately so callers can
    decide what to do with it — the service wraps each noise
    detection as a singleton cluster, preserving the previous
    interpretation rule that a lone high-severity detection still
    becomes a single-aircraft event.
    """
    n = len(detections)
    labels: list[int | None] = [None] * n  # None=unvisited, -1=noise, >=0 cluster
    clusters: list[Cluster] = []
    for i in range(n):
        if labels[i] is not None:
            continue
        neighbors = _dbscan_neighbors(i, detections, eps_time_s, eps_km)
        if len(neighbors) < min_samples:
            labels[i] = -1
            continue
        cluster_id = len(clusters)
        labels[i] = cluster_id
        members = [detections[i]]
        queue = list(neighbors)
        qi = 0
        while qi < len(queue):
            j = queue[qi]
            qi += 1
            if labels[j] == -1:
                labels[j] = cluster_id
                members.append(detections[j])
            elif labels[j] is not None:
                continue
            else:
                labels[j] = cluster_id
                members.append(detections[j])
                j_neighbors = _dbscan_neighbors(j, detections, eps_time_s, eps_km)
                if len(j_neighbors) >= min_samples:
                    queue.extend(
                        k for k in j_neighbors if labels[k] is None or labels[k] == -1
                    )
        clusters.append(Cluster(members=members))
    noise = [detections[i] for i in range(n) if labels[i] == -1]
    return clusters, noise


def clusters_for_interpretation(
    detections: list[Detection],
    *,
    eps_time_s: int = 600,
    eps_km: float = 100.0,
    min_samples: int = 2,
) -> list[Cluster]:
    """Clusters ready for interpret_*(): DBSCAN clusters plus one
    singleton cluster per noise detection, so interpretation keeps
    its rule that a lone high-severity detection still becomes a
    single-aircraft event while lone medium/low detections stay
    un-evented. This is the function the service uses."""
    clusters, noise = dbscan_clusters(
        detections, eps_time_s=eps_time_s, eps_km=eps_km, min_samples=min_samples
    )
    return clusters + [Cluster(members=[d]) for d in noise]


def interpret_clusters(
    clusters: list[Cluster],
    *,
    time_window_s: int = 600,
    max_distance_km: float = 100.0,
) -> list[CandidateEvent]:
    events: list[CandidateEvent] = []
    for cluster in clusters:
        centroid = cluster.centroid
        if len(cluster.aircraft) >= 2:
            events.append(
                CandidateEvent(
                    kind="correlated_anomalies",
                    severity=cluster.max_severity,
                    start_at=cluster.start_at,
                    end_at=cluster.end_at,
                    centroid_lat=centroid[0] if centroid else None,
                    centroid_lon=centroid[1] if centroid else None,
                    aircraft=cluster.aircraft,
                    members=cluster.members,
                    summary=(
                        f"Correlated anomalies: {len(cluster.aircraft)} aircraft "
                        f"showed {len(cluster.members)} anomalies within "
                        f"{time_window_s} seconds and {max_distance_km:.0f} km. "
                        "Potential interference indicator; investigate before "
                        "drawing any conclusion."
                    ),
                )
            )
        elif cluster.max_severity == "high":
            events.append(
                CandidateEvent(
                    kind="single_aircraft_anomaly",
                    severity="high",
                    start_at=cluster.start_at,
                    end_at=cluster.end_at,
                    centroid_lat=centroid[0] if centroid else None,
                    centroid_lon=centroid[1] if centroid else None,
                    aircraft=cluster.aircraft,
                    members=cluster.members,
                    summary=(
                        f"Single-aircraft anomaly: {cluster.aircraft[0]} showed a "
                        "high-severity navigation anomaly. Potential interference "
                        "indicator; investigate before drawing any conclusion."
                    ),
                )
            )
    return events


def interpret_quality_clusters(clusters: list[Cluster]) -> list[CandidateEvent]:
    events: list[CandidateEvent] = []
    for cluster in clusters:
        if len(cluster.aircraft) < 2:
            continue
        centroid = cluster.centroid
        events.append(
            CandidateEvent(
                kind="position_loss_cluster",
                severity="low",
                start_at=cluster.start_at,
                end_at=cluster.end_at,
                centroid_lat=centroid[0] if centroid else None,
                centroid_lon=centroid[1] if centroid else None,
                aircraft=cluster.aircraft,
                members=cluster.members,
                summary=(
                    f"Position-loss cluster: {len(cluster.aircraft)} aircraft "
                    "showed gaps or missing positions in the same window. "
                    "Potential interference indicator; ordinary receiver-"
                    "coverage loss also causes this pattern."
                ),
            )
        )
    return events
