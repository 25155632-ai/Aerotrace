# Packaged replay scenario (synthetic data)

`coordinated-spoof-replay.csv` is a small, deterministic, **synthetic**
dataset for demonstrating replay mode, the common-mode detector, and
the position-loss density layer. It is **not** a real incident and
must never be presented as one: every row imports with
`source_kind=simulated`, and the UI labels it simulated.

## What it contains (46 rows, 5 aircraft, base time 1710000000)

- **Coordinated trio** (`c01a01`, `c01a02`, `c01a03`): three aircraft
  flying east in formation ~45 km apart. At the same step, all three
  jump by the same vector (+2.0° latitude, +1.0° longitude, ~245 km
  north-east), then continue east. Detection yields teleport,
  implausible-acceleration, and `common_mode_offset` detections for
  all three, grouped by DBSCAN into one `correlated_anomalies` event.
- **Coverage-gap pair** (`6a9001`, `6a9002`): two aircraft flying
  east whose positions stop for 1200 s and then resume in the same
  window. Detection yields one `track_gap` finding each, grouped
  into one `position_loss_cluster` event — the event the density
  layer draws from.

## How to use it

In the Aircraft explorer, click **Load replay scenario** (previewed
first, then imported like any CSV), then **Play replay** to step
through the stored observations, and **Run detection** in
Investigation to see the two events. Toggle **Position-loss
density** to draw the gap pair's supporting positions as a grid cell.

## Replaying a real documented event

The replay control works on any stored observations, not just this
file: import a real event's observations as CSV (kind `real` only
when the source genuinely is real tracking data, with its provenance
recorded), then press Play. This package ships no real event,
because no documented event's ADS-B records were available to this
project with a verifiable open source — inventing one would violate
the project's evidence rules.

## Authentic import fixtures

These additional CSV files are real point-in-time state-vector snapshots
fetched from the OpenSky Network `/api/states/all` endpoint on October 25,
2026 UTC. They contain aircraft state vectors, not incident labels or proof of
interference:

- `real-opensky-europe-central.csv` — Central Europe, 180 rows
- `real-opensky-gulf.csv` — Gulf / Arabian Peninsula, 113 rows
- `real-opensky-india-west.csv` — Western India, 27 rows

Each CSV has a matching `.md` provenance card with its exact capture timestamp
and bounding box. Import these with **Real observation** selected. The records
are authentic snapshots, but they become historical as soon as the capture
time passes; do not describe them as current live traffic.

## Verification

`backend/tests/test_replay_scenario.py` imports this exact file
through the real CSV path and asserts the two events above, so the
scenario cannot silently rot.
