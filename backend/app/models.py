"""Pydantic schemas for the API boundary.

Only endpoints that exist are represented here. An endpoint's response
model is a promise: if a schema is in this file, its route serves it.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    database: Literal["ready"]


class SourceOut(BaseModel):
    name: str
    kind: Literal["real", "simulated"]
    status: str
    note: str


class BBoxInput(BaseModel):
    lamin: float = Field(ge=-90.0, le=90.0)
    lamax: float = Field(ge=-90.0, le=90.0)
    lomin: float = Field(ge=-180.0, le=180.0)
    lomax: float = Field(ge=-180.0, le=180.0)

    @model_validator(mode="after")
    def _check_order(self) -> "BBoxInput":
        if self.lamin >= self.lamax or self.lomin >= self.lomax:
            raise ValueError("bbox minimum must be below maximum")
        return self


class NearbyInput(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_nm: int = Field(ge=1, le=250)


class RejectionOut(BaseModel):
    index: int
    reason: str


class IngestReportOut(BaseModel):
    total: int
    stored: int
    duplicates: int
    stale: int
    missing_coordinates: int
    rejected: list[RejectionOut] = []
    source: str | None = None
    dropped_without_icao24: int | None = None
    snapshot_time: int | None = None


class ObservationOut(BaseModel):
    id: int
    icao24: str
    observed_at: int
    latitude: float | None
    longitude: float | None
    baro_altitude_m: float | None
    geo_altitude_m: float | None
    velocity_ms: float | None
    heading_deg: float | None
    callsign: str | None
    source: str
    source_kind: Literal["real", "simulated"]
    received_at: int
    quality_note: str | None


class DetectionOut(BaseModel):
    id: int
    rule: str
    severity: str
    kind: str
    icao24: str
    detected_at: int
    latitude: float | None
    longitude: float | None
    evidence: dict
    supporting_observation_ids: list[int] = []
    source_kind: str
    source_kinds: list[str] = []


class VerificationOut(BaseModel):
    """One verification act. Rows are never deleted: superseded and
    revoked rows stay in the history. The URL is stored, never fetched
    by the backend, so a citation cannot turn the server into a proxy
    for an attacker-chosen address."""

    id: int
    event_id: int
    title: str
    publisher: str | None = None
    reference_url: str | None = None
    accessed_date: str | None = None
    note: str
    submitted_by: str | None = None
    status: str  # current | superseded | revoked
    created_at: int
    revoked_reason: str | None = None


class EventOut(BaseModel):
    id: int
    kind: str
    severity: str
    start_at: int
    end_at: int
    centroid_lat: float | None
    centroid_lon: float | None
    aircraft: list[str]
    summary: str
    caveat: str | None = None
    status: str
    member_anomaly_ids: list[int] = []
    # Stored-event reads do not carry the full member rule list; the
    # run report (DetectReportOut) does, via member_rules.
    member_rules: list[str] = []
    member_icao24: list[str] = []
    verification: VerificationOut | None = None
    verification_history: list[VerificationOut] = []


class VerifyEventIn(BaseModel):
    """Human-submitted citation for verifying an event. Only the
    citation is validated here; the backend never checks the cited
    source itself, and there is no account system, so `submitted_by`
    is a self-asserted label, not a verified identity."""

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    publisher: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    reference_url: str | None = None
    accessed_date: str | None = None
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    submitted_by: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None

    @field_validator("publisher", "reference_url", "accessed_date", "submitted_by", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @field_validator("reference_url")
    @classmethod
    def _url_must_be_http(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("reference_url must be an http(s) URL")
        return value.strip()

    @field_validator("accessed_date")
    @classmethod
    def _date_must_be_iso(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            date.fromisoformat(value.strip())
        except ValueError:
            raise ValueError("accessed_date must be YYYY-MM-DD") from None
        return value.strip()


class CsvImportIn(BaseModel):
    """CSV import request. Uploaded data defaults to simulated: the
    caller must explicitly assert 'real', and even then Aerotrace does
    not verify the claim — the stored source stays 'csv-import' so
    uploaded rows are always distinguishable from live data."""

    csv_text: Annotated[str, StringConstraints(max_length=1_000_000)]
    source_kind: Literal["real", "simulated"] = "simulated"


class CsvRowErrorOut(BaseModel):
    line: int
    reason: str


class CsvPreviewOut(BaseModel):
    observations: list[ObservationOut] = Field(default_factory=list)
    total_rows: int
    valid_rows: int
    error_rows: int
    duplicate_rows: int
    errors: list[CsvRowErrorOut] = []
    errors_truncated: bool = False


class CsvImportOut(BaseModel):
    total_rows: int
    valid_rows: int
    error_rows: int
    stored: int
    duplicates: int
    stale: int
    missing_coordinates: int
    errors: list[CsvRowErrorOut] = []
    errors_truncated: bool = False


class RevokeEventIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def _empty_reason_to_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value


class DetectReportOut(BaseModel):
    observations_analysed: int
    aircraft_analysed: int
    detections: list[DetectionOut]
    events: list[EventOut]
    summary: dict


Severity = Literal["low", "medium", "high"]
AlertChannel = Literal["in_app", "email", "sms"]


class AlertRuleIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    enabled: bool = True
    min_severity: Severity = "high"
    categories: list[Annotated[str, StringConstraints(max_length=80)]] = Field(default_factory=list, max_length=20)
    provenance: list[Literal["real", "simulated", "mixed"]] = Field(default_factory=list, max_length=3)
    region: dict | None = None
    min_isolation_anomaly_score: float | None = Field(default=None, ge=-1, le=1)
    supervised_threshold: float | None = Field(default=None, ge=0, le=1)
    cooldown_seconds: int = Field(default=300, ge=0, le=86400)
    group_window_seconds: int = Field(default=600, ge=0, le=86400)
    group_radius_km: float = Field(default=100, ge=0, le=20000)
    channels: list[AlertChannel] = Field(default_factory=lambda: ["in_app"], max_length=3)
    in_app: bool = True
    external: bool = False

    @model_validator(mode="after")
    def _validate_channels(self) -> "AlertRuleIn":
        if not self.channels:
            raise ValueError("at least one notification channel is required")
        if self.external and not any(channel in ("email", "sms") for channel in self.channels):
            raise ValueError("external rules must include email or sms")
        if self.region is not None:
            required = {"min_lat", "max_lat", "min_lon", "max_lon"}
            if set(self.region) != required:
                raise ValueError("region must contain min_lat, max_lat, min_lon, max_lon")
            values = {key: float(self.region[key]) for key in required}
            if not (-90 <= values["min_lat"] <= values["max_lat"] <= 90):
                raise ValueError("invalid region latitude bounds")
            if not (-180 <= values["min_lon"] <= values["max_lon"] <= 180):
                raise ValueError("invalid region longitude bounds")
        return self


class AlertRuleOut(AlertRuleIn):
    id: int
    created_at: int
    updated_at: int


class AlertOut(BaseModel):
    id: int
    rule_id: int
    fingerprint: str
    status: Literal["open", "acknowledged", "resolved"]
    severity: Severity
    category: str
    title: str
    summary: str
    aircraft: list[str]
    detected_at: int
    start_at: int
    end_at: int
    latitude: float | None
    longitude: float | None
    provenance: Literal["real", "simulated", "mixed"]
    source_kinds: list[str]
    supporting_event_ids: list[int]
    supporting_detection_ids: list[int]
    evidence: dict
    created_at: int
    deliveries: list[dict] = Field(default_factory=list)


class AlertHealthOut(BaseModel):
    enabled: bool
    broker: str
    pending_outbox: int
    providers: dict[str, dict]


class ModelStatusOut(BaseModel):
    name: str
    status: str
    version: str | None = None
    artifact_path: str | None = None
    metadata: dict = Field(default_factory=dict)
    last_error: str | None = None
    semantics: str


class ModelScoreIn(BaseModel):
    model: Literal["isolation_forest", "xgboost", "lstm"]
    observations: list[ObservationOut] = Field(min_length=1, max_length=200)


class ModelScoreOut(BaseModel):
    model: str
    status: str
    score: float | None = None
    score_semantics: str
    model_version: str | None = None
    evidence: dict = Field(default_factory=dict)
