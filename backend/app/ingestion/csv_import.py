"""CSV import: the file-upload path into the existing observation
pipeline — not a second ingestion pathway.

Format (documented in docs/CSV_IMPORT.md):
- First row is a header. Required columns: icao24, observed_at.
  Optional: latitude, longitude, baro_altitude_m, geo_altitude_m,
  baro_altitude_ft, geo_altitude_ft, velocity_ms, velocity_kt,
  heading_deg, callsign. Unknown columns are ignored.
- observed_at accepts epoch seconds or ISO 8601 (naive ISO is
  rejected, exactly as for other sources).
- Every row is validated with the same normalize_observation used by
  demo and live ingestion, so a CSV row cannot smuggle in a value the
  pipeline would reject elsewhere.

Import strategy is explicitly partial, not atomic: valid rows commit
and invalid rows are reported with their CSV line numbers, matching
the pipeline's per-record outcomes. Preview runs the same parse
without writing anything, so the user sees failures before committing.

Bounds: at most MAX_ROWS data rows; the request body itself is capped
at 1 MB by the same-origin middleware (see app/main.py).

Formula injection: stored observations carry no free-text fields —
icao24 is 6 hex characters, callsigns match a restricted charset, and
every other field is numeric or an enumerated kind — so a cell such as
"=2+2" fails numeric validation and is reported, never stored.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from .observations import ObservationError, normalize_observation

MAX_ROWS = 10_000
MAX_ERRORS_RETURNED = 100
IMPORT_SOURCE = "csv-import"

KNOWN_COLUMNS = (
    "icao24",
    "observed_at",
    "latitude",
    "longitude",
    "baro_altitude_m",
    "geo_altitude_m",
    "baro_altitude_ft",
    "geo_altitude_ft",
    "velocity_ms",
    "velocity_kt",
    "heading_deg",
    "callsign",
)


class CsvImportError(ValueError):
    """File-level problem: the file cannot be parsed at all."""


@dataclass
class CsvRowError:
    line: int
    reason: str


@dataclass
class CsvParseResult:
    """Valid raw records paired with their CSV line numbers, plus
    per-row errors. Raw records (not normalized objects) are kept so
    the commit path feeds the standard pipeline unchanged."""

    valid: list[tuple[dict, int]] = field(default_factory=list)
    errors: list[CsvRowError] = field(default_factory=list)
    total_rows: int = 0


def parse_csv_records(csv_text: str, source_kind: str) -> CsvParseResult:
    if csv_text is None or csv_text.strip() == "":
        raise CsvImportError("CSV file is empty")
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        fieldnames = reader.fieldnames
        if fieldnames is None:
            raise CsvImportError("CSV file has no header row")
        header = {name.strip() for name in fieldnames if name is not None}
    except csv.Error as exc:
        raise CsvImportError(f"CSV file could not be parsed: {exc}") from exc
    missing = {"icao24", "observed_at"} - header
    if missing:
        raise CsvImportError(
            "CSV header must include: " + ", ".join(sorted(missing))
        )
    rows: list[dict] = []
    try:
        for row in reader:
            if row is None:
                continue
            values = {
                (key.strip() if isinstance(key, str) else key): value
                for key, value in row.items()
                if key is not None
            }
            if all(
                value is None or (isinstance(value, str) and value.strip() == "")
                for value in values.values()
            ):
                continue  # Blank lines are skipped, not errors.
            rows.append(values)
    except csv.Error as exc:
        raise CsvImportError(f"CSV file could not be parsed: {exc}") from exc
    if len(rows) > MAX_ROWS:
        raise CsvImportError(
            f"CSV file has {len(rows)} data rows, over the {MAX_ROWS} limit"
        )
    result = CsvParseResult(total_rows=len(rows))
    for offset, values in enumerate(rows):
        line = offset + 2  # Header is line 1.
        record = {
            column: values.get(column)
            for column in KNOWN_COLUMNS
            if values.get(column) is not None
        }
        record["source"] = IMPORT_SOURCE
        record["source_kind"] = source_kind
        try:
            normalize_observation(record)
        except ObservationError as exc:
            result.errors.append(CsvRowError(line=line, reason=exc.reason))
            continue
        result.valid.append((record, line))
    return result


def row_errors_for_response(errors: list[CsvRowError]) -> tuple[list[dict], bool]:
    """Cap the returned error list; the total error count is reported
    separately so capping never hides that errors were dropped."""
    shown = errors[:MAX_ERRORS_RETURNED]
    return (
        [{"line": e.line, "reason": e.reason} for e in shown],
        len(errors) > MAX_ERRORS_RETURNED,
    )
