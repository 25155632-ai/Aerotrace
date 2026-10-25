"""API routes for ingestion and stored observations.

Honesty contract for this module:
- POST /api/ingest/demo loads labelled simulated data and says so.
- POST /api/ingest/opensky either stores real OpenSky records or
  returns an error and stores nothing. It never substitutes demo data.
- GET /api/observations returns stored rows with their provenance
  fields intact; filtering by source_kind is how callers keep real and
  simulated data apart.
"""

from __future__ import annotations

import csv
from dataclasses import asdict
import asyncio
import io
import json
import logging
import httpx
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from ..cache import CACHE
from ..config import Settings
from ..ingestion.demo_data import demo_observations
from ..ingestion.opensky import (
    BBox,
    RateLimitedError,
    SourceUnavailableError,
    fetch_states,
)
from ..ingestion.adsb_lol import fetch_nearby as fetch_adsb_lol
from ..ingestion.airplanes_live import fetch_nearby as fetch_airplanes_live
from ..ingestion.pipeline import ingest_observations
from ..detectors.service import run_detection
from ..ingestion.csv_import import (
    CsvImportError,
    parse_csv_records,
    row_errors_for_response,
)
from ..ingestion.observations import normalize_observation
from ..models import (
    AlertHealthOut,
    AlertOut,
    AlertRuleIn,
    AlertRuleOut,
    BBoxInput,
    NearbyInput,
    CsvImportIn,
    CsvImportOut,
    CsvPreviewOut,
    DetectReportOut,
    DetectionOut,
    EventOut,
    IngestReportOut,
    ObservationOut,
    RevokeEventIn,
    SourceOut,
    VerifyEventIn,
    ModelStatusOut,
    ModelScoreIn,
    ModelScoreOut,
)
from ..notifications import provider_status
from ..ml.service import model_status, score_model
from ..store import (
    get_connection,
    get_event,
    latest_source_fetch,
    list_anomalies,
    list_events,
    list_observations,
    log_source_fetch,
    observation_exists,
    revoke_event,
    verify_event,
    list_alert_rules,
    get_alert_rule,
    upsert_alert_rule,
    list_alerts,
    get_alert,
    list_delivery_attempts,
    enqueue_alert_outbox,
    list_model_registry,
)

router = APIRouter()

logger = logging.getLogger("spoofsky.ingest")


def _settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else Settings.from_env()


def _live_source_status(request: Request) -> SourceOut:
    """Live-source status from the recorded fetch log, never a
    hardcoded claim: a fresh database reports not_verified, a
    successful fetch reports verified_live with its timestamp, and a
    failed fetch reports fetch_failed. A status here describes the
    last recorded attempt, not a guarantee about the next fetch."""
    settings = _settings(request)
    with get_connection(settings.database_path) as connection:
        latest = latest_source_fetch(connection, "opensky-live")
    if latest is None:
        return SourceOut(
            name="opensky-live",
            kind="real",
            status="not_verified",
            note=(
                "No live fetch has been recorded in this database yet. "
                "Use the live ingestion control with a bounding box; a "
                "failed fetch returns an error and stores nothing."
            ),
        )
    when = datetime.fromtimestamp(latest["fetched_at"], tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )
    if latest["outcome"] == "success":
        return SourceOut(
            name="opensky-live",
            kind="real",
            status="verified_live",
            note=(
                f"Last live fetch succeeded {when} "
                f"({latest['records_stored']} records stored). "
                "Availability is not guaranteed: a later fetch may fail."
            ),
        )
    detail = f" ({latest['detail']})" if latest.get("detail") else ""
    return SourceOut(
        name="opensky-live",
        kind="real",
        status="fetch_failed",
        note=f"Last live fetch failed {when}{detail}. No records were stored by it.",
    )


@router.get("/api/sources", response_model=list[SourceOut])
def sources(request: Request) -> list[SourceOut]:
    settings = _settings(request)
    provider_sources = [
        SourceOut(
            name="adsb.lol",
            kind="real",
            status="configured" if settings.adsb_lol_enabled else "disabled",
            note="Optional public adapter; a successful response is not proof that every observation is current.",
        ),
        SourceOut(
            name="airplanes.live",
            kind="real",
            status="configured" if settings.airplanes_live_enabled else "disabled",
            note="Optional public adapter; independently configurable fallback source.",
        ),
    ]
    return [
        SourceOut(
            name="spoofsky-demo",
            kind="simulated",
            status="available_on_demand",
            note=(
                "Labelled simulated dataset (34 records). Load it with "
                "POST /api/ingest/demo. Never presented as real data."
            ),
        ),
        _live_source_status(request),
        *provider_sources,
    ]


@router.post("/api/ingest/demo", response_model=IngestReportOut)
def ingest_demo(request: Request) -> IngestReportOut:
    settings = _settings(request)
    with get_connection(settings.database_path) as connection:
        report = ingest_observations(connection, demo_observations())
    CACHE.invalidate()
    result = report.to_dict()
    result["source"] = "spoofsky-demo"
    return IngestReportOut(**result)


@router.post("/api/ingest/opensky", response_model=IngestReportOut)
def ingest_opensky(request: Request, bbox: BBoxInput | None = None) -> IngestReportOut:
    settings = _settings(request)
    opensky_bbox = (
        BBox(lamin=bbox.lamin, lamax=bbox.lamax, lomin=bbox.lomin, lomax=bbox.lomax)
        if bbox is not None
        else None
    )
    transport = getattr(request.app.state, "opensky_transport", None)
    try:
        fetched = fetch_states(bbox=opensky_bbox, transport=transport)
    except RateLimitedError as exc:
        logger.warning("opensky ingest rate-limited: %s", exc)
        with get_connection(settings.database_path) as connection:
            log_source_fetch(
                connection, source="opensky-live", outcome="failed", detail=str(exc)
            )
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limited",
                "message": str(exc),
                "retry_after_seconds": exc.retry_after_seconds,
            },
        ) from exc
    except SourceUnavailableError as exc:
        logger.warning("opensky ingest failed: %s", exc)
        with get_connection(settings.database_path) as connection:
            log_source_fetch(
                connection, source="opensky-live", outcome="failed", detail=str(exc)
            )
        raise HTTPException(
            status_code=502,
            detail={"error": "source_unavailable", "message": str(exc)},
        ) from exc

    with get_connection(settings.database_path) as connection:
        report = ingest_observations(connection, fetched.records)
        log_source_fetch(
            connection,
            source="opensky-live",
            outcome="success",
            records_stored=report.stored,
        )
    CACHE.invalidate()
    result = report.to_dict()
    result["source"] = "opensky-live"
    result["dropped_without_icao24"] = fetched.dropped_without_icao24
    result["snapshot_time"] = fetched.snapshot_time
    return IngestReportOut(**result)


def _ingest_nearby(request: Request, payload: NearbyInput, provider: str) -> IngestReportOut:
    settings = _settings(request)
    if provider == "adsb.lol" and not settings.adsb_lol_enabled:
        raise HTTPException(status_code=409, detail="adsb.lol is disabled by configuration")
    if provider == "airplanes.live" and not settings.airplanes_live_enabled:
        raise HTTPException(status_code=409, detail="airplanes.live is disabled by configuration")
    fetcher = fetch_adsb_lol if provider == "adsb.lol" else fetch_airplanes_live
    try:
        result = fetcher(payload.latitude, payload.longitude, payload.radius_nm, timeout_seconds=settings.provider_timeout_seconds)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail={"error": "source_unavailable", "message": type(exc).__name__}) from exc
    with get_connection(settings.database_path) as connection:
        report = ingest_observations(connection, result.records)
        log_source_fetch(connection, source=provider, outcome="success", records_stored=report.stored)
    CACHE.invalidate()
    output = report.to_dict()
    output["source"] = provider
    output["snapshot_time"] = result.response_time
    return IngestReportOut(**output)


@router.post("/api/ingest/adsb-lol", response_model=IngestReportOut)
def ingest_adsb_lol(payload: NearbyInput, request: Request) -> IngestReportOut:
    return _ingest_nearby(request, payload, "adsb.lol")


@router.post("/api/ingest/airplanes-live", response_model=IngestReportOut)
def ingest_airplanes_live(payload: NearbyInput, request: Request) -> IngestReportOut:
    return _ingest_nearby(request, payload, "airplanes.live")


@router.post("/api/ingest/csv/preview", response_model=CsvPreviewOut)
def csv_preview(payload: CsvImportIn, request: Request) -> CsvPreviewOut:
    """Validate a CSV file without storing anything. Duplicate counts
    are checked against current stored rows; the commit endpoint
    re-validates, so a preview can never commit stale results."""
    try:
        parsed = parse_csv_records(payload.csv_text, payload.source_kind)
    except CsvImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    settings = _settings(request)
    duplicates = 0
    with get_connection(settings.database_path) as connection:
        for record, _line in parsed.valid:
            obs = normalize_observation(record)
            if observation_exists(
                connection,
                obs.icao24,
                obs.observed_at,
                obs.source,
                obs.latitude,
                obs.longitude,
            ):
                duplicates += 1
    errors, truncated = row_errors_for_response(parsed.errors)
    return CsvPreviewOut(
        observations=[
            ObservationOut(id=-line, quality_note=None, **asdict(normalize_observation(record)))
            for record, line in parsed.valid
        ],
        total_rows=parsed.total_rows,
        valid_rows=len(parsed.valid),
        error_rows=len(parsed.errors),
        duplicate_rows=duplicates,
        errors=errors,
        errors_truncated=truncated,
    )


@router.post("/api/ingest/csv", response_model=CsvImportOut)
def csv_import(payload: CsvImportIn, request: Request) -> CsvImportOut:
    """Commit a previewed CSV file. Valid rows go through the standard
    pipeline (partial-import strategy: invalid rows are reported, not
    committed); the response maps pipeline rejections back to CSV line
    numbers so failures stay locatable in the original file."""
    try:
        parsed = parse_csv_records(payload.csv_text, payload.source_kind)
    except CsvImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    settings = _settings(request)
    with get_connection(settings.database_path) as connection:
        report = ingest_observations(
            connection, [record for record, _line in parsed.valid]
        )
    CACHE.invalidate()
    line_by_index = {i: line for i, (_record, line) in enumerate(parsed.valid)}
    mapped_errors = [
        {"line": line_by_index.get(r.index, -1), "reason": r.reason}
        for r in report.rejected
    ]
    # Pipeline rejections of already-validated rows should not happen;
    # if they do, they are still reported rather than swallowed.
    all_errors = [
        {"line": e.line, "reason": e.reason} for e in parsed.errors
    ] + mapped_errors
    shown = all_errors[:100]
    return CsvImportOut(
        total_rows=parsed.total_rows,
        valid_rows=len(parsed.valid),
        error_rows=len(all_errors),
        stored=report.stored,
        duplicates=report.duplicates,
        stale=report.stale,
        missing_coordinates=report.missing_coordinates,
        errors=shown,
        errors_truncated=len(all_errors) > 100,
    )


@router.post("/api/detect", response_model=DetectReportOut)
def detect(
    request: Request,
    source_kind: Literal["real", "simulated"] | None = Query(default=None),
    icao24: str | None = Query(default=None),
) -> DetectReportOut:
    """Run the detection engine over stored observations and persist
    the results, replacing any previous run's results. Returns the
    full structured report; stored rows are also readable via
    GET /api/anomalies and GET /api/events."""
    settings = _settings(request)
    connection = get_connection(settings.database_path)
    try:
        report = run_detection(
            connection,
            source_kind=source_kind,
            icao24=icao24.lower() if icao24 else None,
        )
    finally:
        connection.close()
    CACHE.invalidate()
    return DetectReportOut(**report)


@router.get("/api/alerts", response_model=list[AlertOut])
def alerts(
    request: Request,
    severity: Literal["low", "medium", "high"] | None = Query(default=None),
    category: str | None = Query(default=None, max_length=80),
    provenance: Literal["real", "simulated", "mixed"] | None = Query(default=None),
    status: Literal["open", "acknowledged", "resolved"] | None = Query(default=None),
    from_ts: int | None = Query(default=None, ge=0),
    to_ts: int | None = Query(default=None, ge=0),
    offset: int = Query(default=0, ge=0),
    limit: int | None = Query(default=None, ge=1, le=1000),
) -> list[AlertOut]:
    _validate_range(from_ts, to_ts)
    settings = _settings(request)
    with get_connection(settings.database_path) as connection:
        rows = list_alerts(
            connection, severity=severity, category=category, provenance=provenance,
            status=status, from_ts=from_ts, to_ts=to_ts,
            offset=offset, limit=settings.max_page_size if limit is None else limit,
        )
        for row in rows:
            row["deliveries"] = list_delivery_attempts(connection, row["id"])
    return [AlertOut(**row) for row in rows]


@router.get("/api/alerts/{alert_id}", response_model=AlertOut)
def alert_detail(alert_id: int, request: Request) -> AlertOut:
    settings = _settings(request)
    with get_connection(settings.database_path) as connection:
        row = get_alert(connection, alert_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Unknown alert")
        row["deliveries"] = list_delivery_attempts(connection, alert_id)
    return AlertOut(**row)


@router.get("/api/alert-rules", response_model=list[AlertRuleOut])
def alert_rules(request: Request) -> list[AlertRuleOut]:
    settings = _settings(request)
    with get_connection(settings.database_path) as connection:
        return [AlertRuleOut(**row) for row in list_alert_rules(connection)]


@router.post("/api/alert-rules", response_model=AlertRuleOut)
def create_alert_rule(payload: AlertRuleIn, request: Request) -> AlertRuleOut:
    settings = _settings(request)
    with get_connection(settings.database_path) as connection:
        try:
            row = upsert_alert_rule(connection, payload.model_dump(), None)
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Alert rule name is already in use") from exc
    return AlertRuleOut(**row)


@router.put("/api/alert-rules/{rule_id}", response_model=AlertRuleOut)
def update_alert_rule(rule_id: int, payload: AlertRuleIn, request: Request) -> AlertRuleOut:
    settings = _settings(request)
    with get_connection(settings.database_path) as connection:
        if get_alert_rule(connection, rule_id) is None:
            raise HTTPException(status_code=404, detail="Unknown alert rule")
        try:
            row = upsert_alert_rule(connection, payload.model_dump(), rule_id)
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Alert rule name is already in use") from exc
    return AlertRuleOut(**row)


@router.post("/api/alert-rules/{rule_id}/enabled", response_model=AlertRuleOut)
def set_alert_rule_enabled(rule_id: int, enabled: bool, request: Request) -> AlertRuleOut:
    settings = _settings(request)
    with get_connection(settings.database_path) as connection:
        row = get_alert_rule(connection, rule_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Unknown alert rule")
        row["enabled"] = enabled
        row = upsert_alert_rule(connection, row, rule_id)
    return AlertRuleOut(**row)


@router.get("/api/alerting/health", response_model=AlertHealthOut)
def alerting_health(request: Request) -> AlertHealthOut:
    settings = _settings(request)
    with get_connection(settings.database_path) as connection:
        pending = connection.execute(
            "SELECT COUNT(*) FROM alert_outbox WHERE status IN ('pending', 'processing')"
        ).fetchone()[0]
    return AlertHealthOut(
        enabled=True,
        broker="local-durable-outbox" if not settings.redis_url else "local-durable-outbox+redis-optional",
        pending_outbox=int(pending),
        providers=provider_status(settings),
    )


@router.post("/api/alerts/{alert_id}/retry", response_model=AlertOut)
def retry_alert(alert_id: int, request: Request) -> AlertOut:
    settings = _settings(request)
    with get_connection(settings.database_path) as connection:
        row = get_alert(connection, alert_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Unknown alert")
        enqueue_alert_outbox(connection, alert_id, {"type": "alert.retry", "alert": row})
        connection.commit()
        row["deliveries"] = list_delivery_attempts(connection, alert_id)
    return AlertOut(**row)


@router.websocket("/api/ws/alerts")
async def alerts_websocket(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    settings = getattr(websocket.app.state, "settings", Settings.from_env())
    if origin != settings.frontend_origin:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    manager = websocket.app.state.alert_manager
    queue = await manager.connect()
    try:
        await websocket.send_json({"type": "connection.status", "status": "connected", "delivery": "websocket_only"})
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=25)
                await websocket.send_json(payload)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "connection.heartbeat", "status": "connected"})
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(queue)


@router.get("/api/ml/status", response_model=list[ModelStatusOut])
def ml_status(request: Request) -> list[ModelStatusOut]:
    settings = _settings(request)
    with get_connection(settings.database_path) as connection:
        rows = list_model_registry(connection)
    result = []
    for row in rows:
        runtime = model_status(row["name"])
        if row["status"] == "not_installed" and runtime["installed"]:
            row["status"] = "awaiting_training_data"
        result.append(ModelStatusOut(**row, semantics=runtime["semantics"]))
    return result


@router.post("/api/ml/score", response_model=ModelScoreOut)
def ml_score(payload: ModelScoreIn, request: Request) -> ModelScoreOut:
    settings = _settings(request)
    result = score_model(payload.model, [item.model_dump() for item in payload.observations], settings)
    return ModelScoreOut(**result)


def _validate_range(
    from_ts: int | None, to_ts: int | None
) -> None:
    if from_ts is not None and to_ts is not None and from_ts > to_ts:
        raise HTTPException(
            status_code=422,
            detail="from_ts must not be after to_ts",
        )


def _validate_bbox(
    min_lat: float | None,
    max_lat: float | None,
    min_lon: float | None,
    max_lon: float | None,
) -> None:
    if min_lat is not None and max_lat is not None and min_lat > max_lat:
        raise HTTPException(
            status_code=422,
            detail="min_lat must not be after max_lat",
        )
    if min_lon is not None and max_lon is not None and min_lon > max_lon:
        raise HTTPException(
            status_code=422,
            detail="min_lon must not be after max_lon",
        )


def _utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _csv_response(filename: str, header: list[str], rows: list[list[object]]) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/anomalies", response_model=list[DetectionOut])
def anomalies(
    request: Request,
    rule: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    icao24: str | None = Query(default=None),
    from_ts: int | None = Query(default=None, ge=0),
    to_ts: int | None = Query(default=None, ge=0),
    min_lat: float | None = Query(default=None, ge=-90, le=90),
    max_lat: float | None = Query(default=None, ge=-90, le=90),
    min_lon: float | None = Query(default=None, ge=-180, le=180),
    max_lon: float | None = Query(default=None, ge=-180, le=180),
    ids: list[int] | None = Query(default=None),
    sort: Literal["detected_at_asc", "detected_at_desc", "severity_desc"] = Query(
        default="detected_at_desc"
    ),
    offset: int = Query(default=0, ge=0),
    limit: int | None = Query(default=None, ge=1, le=1000),
) -> list[DetectionOut]:
    _validate_range(from_ts, to_ts)
    _validate_bbox(min_lat, max_lat, min_lon, max_lon)
    settings = _settings(request)
    effective_limit = settings.max_page_size if limit is None else limit
    cache_key = f"{settings.database_path}|anomalies?{request.url.query}"
    cached = CACHE.get(cache_key)
    if cached is not None:
        rows = [dict(row) for row in cached]  # type: ignore[union-attr]
    else:
        with get_connection(settings.database_path) as connection:
            rows = list_anomalies(
                connection,
                rule=rule,
                severity=severity,
                kind=kind,
                icao24=icao24.lower() if icao24 else None,
                from_ts=from_ts,
                to_ts=to_ts,
                min_lat=min_lat,
                max_lat=max_lat,
                min_lon=min_lon,
                max_lon=max_lon,
                ids=ids,
                sort=sort,
                offset=offset,
                limit=effective_limit,
            )
        CACHE.put(cache_key, rows)
    return [DetectionOut(**row) for row in rows]


@router.get("/api/anomalies/export")
def anomalies_export(
    request: Request,
    rule: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    icao24: str | None = Query(default=None),
    from_ts: int | None = Query(default=None, ge=0),
    to_ts: int | None = Query(default=None, ge=0),
    min_lat: float | None = Query(default=None, ge=-90, le=90),
    max_lat: float | None = Query(default=None, ge=-90, le=90),
    min_lon: float | None = Query(default=None, ge=-180, le=180),
    max_lon: float | None = Query(default=None, ge=-180, le=180),
) -> Response:
    """CSV export of the filtered anomalies (all matching rows, not
    just one page). Filters are exactly the list endpoint's filters."""
    _validate_range(from_ts, to_ts)
    _validate_bbox(min_lat, max_lat, min_lon, max_lon)
    settings = _settings(request)
    with get_connection(settings.database_path) as connection:
        rows = list_anomalies(
            connection,
            rule=rule,
            severity=severity,
            kind=kind,
            icao24=icao24.lower() if icao24 else None,
            from_ts=from_ts,
            to_ts=to_ts,
            min_lat=min_lat,
            max_lat=max_lat,
            min_lon=min_lon,
            max_lon=max_lon,
            sort="detected_at_desc",
            limit=10000,
        )
    csv_rows = [
        [
            row["id"],
            row["icao24"],
            row["detected_at"],
            _utc(row["detected_at"]),
            row["rule"],
            row["severity"],
            row["kind"],
            row["latitude"],
            row["longitude"],
            row["source_kind"],
            ";".join(str(i) for i in row["supporting_observation_ids"]),
            json.dumps(row["evidence"], sort_keys=True),
        ]
        for row in rows
    ]
    return _csv_response(
        "aerotrace-anomalies.csv",
        [
            "id", "icao24", "detected_at", "detected_at_utc", "rule",
            "severity", "kind", "latitude", "longitude", "source_kind",
            "supporting_observation_ids", "evidence_json",
        ],
        csv_rows,
    )


@router.get("/api/events", response_model=list[EventOut])
def events(
    request: Request,
    kind: str | None = Query(default=None),
    from_ts: int | None = Query(default=None, ge=0),
    to_ts: int | None = Query(default=None, ge=0),
    offset: int = Query(default=0, ge=0),
    limit: int | None = Query(default=None, ge=1, le=1000),
) -> list[EventOut]:
    _validate_range(from_ts, to_ts)
    settings = _settings(request)
    effective_limit = settings.max_page_size if limit is None else limit
    cache_key = f"{settings.database_path}|events?{request.url.query}"
    cached = CACHE.get(cache_key)
    if cached is not None:
        rows = [dict(row) for row in cached]  # type: ignore[union-attr]
    else:
        with get_connection(settings.database_path) as connection:
            rows = list_events(
                connection,
                kind=kind,
                from_ts=from_ts,
                to_ts=to_ts,
                offset=offset,
                limit=effective_limit,
            )
        CACHE.put(cache_key, rows)
    for row in rows:
        row.setdefault("caveat", None)
    return [EventOut(**row) for row in rows]


@router.post("/api/events/{event_id}/verify", response_model=EventOut)
def verify_event_endpoint(
    event_id: int, payload: VerifyEventIn, request: Request
) -> EventOut:
    """Record a human-submitted citation and mark the event verified.
    Verifying a verified event supersedes the previous record; nothing
    is deleted. The citation URL is stored, never fetched. There is no
    account system: `submitted_by` is a self-asserted label, and the
    response never claims the submitter's identity was checked."""
    settings = _settings(request)
    with get_connection(settings.database_path) as connection:
        try:
            verify_event(
                connection,
                event_id=event_id,
                title=payload.title,
                publisher=payload.publisher,
                reference_url=payload.reference_url,
                accessed_date=payload.accessed_date,
                note=payload.note,
                submitted_by=payload.submitted_by,
            )
        except LookupError:
            raise HTTPException(status_code=404, detail="Unknown event") from None
        event = get_event(connection, event_id)
    assert event is not None
    event.setdefault("caveat", None)
    CACHE.invalidate()
    return EventOut(**event)


@router.post("/api/events/{event_id}/revoke", response_model=EventOut)
def revoke_event_endpoint(
    event_id: int, payload: RevokeEventIn, request: Request
) -> EventOut:
    """Return a verified event to candidate. The revoked record is
    kept with its reason, so a mistaken verification stays auditable."""
    settings = _settings(request)
    with get_connection(settings.database_path) as connection:
        try:
            revoke_event(connection, event_id=event_id, reason=payload.reason)
        except LookupError:
            raise HTTPException(status_code=404, detail="Unknown event") from None
        except ValueError:
            raise HTTPException(
                status_code=409, detail="Event is not verified"
            ) from None
        event = get_event(connection, event_id)
    assert event is not None
    event.setdefault("caveat", None)
    CACHE.invalidate()
    return EventOut(**event)


@router.get("/api/events/export")
def events_export(
    request: Request,
    kind: str | None = Query(default=None),
    from_ts: int | None = Query(default=None, ge=0),
    to_ts: int | None = Query(default=None, ge=0),
) -> Response:
    """CSV export of the filtered events (all matching rows)."""
    _validate_range(from_ts, to_ts)
    settings = _settings(request)
    with get_connection(settings.database_path) as connection:
        rows = list_events(
            connection,
            kind=kind,
            from_ts=from_ts,
            to_ts=to_ts,
            limit=10000,
        )
    csv_rows = [
        [
            row["id"],
            row["kind"],
            row["severity"],
            row["start_at"],
            _utc(row["start_at"]),
            row["end_at"],
            _utc(row["end_at"]),
            row["centroid_lat"],
            row["centroid_lon"],
            ";".join(row["aircraft"]),
            row["status"],
            row["summary"],
            ";".join(str(i) for i in row["member_anomaly_ids"]),
        ]
        for row in rows
    ]
    return _csv_response(
        "aerotrace-events.csv",
        [
            "id", "kind", "severity", "start_at", "start_at_utc", "end_at",
            "end_at_utc", "centroid_lat", "centroid_lon", "aircraft",
            "status", "summary", "member_anomaly_ids",
        ],
        csv_rows,
    )


@router.get("/api/observations", response_model=list[ObservationOut])
def observations(
    request: Request,
    icao24: str | None = Query(default=None),
    source_kind: Literal["real", "simulated"] | None = Query(default=None),
    from_ts: int | None = Query(default=None, ge=0),
    to_ts: int | None = Query(default=None, ge=0),
    ids: list[int] | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=1000),
) -> list[ObservationOut]:
    _validate_range(from_ts, to_ts)
    settings = _settings(request)
    effective_limit = settings.max_page_size if limit is None else limit
    cache_key = f"{settings.database_path}|observations?{request.url.query}"
    cached = CACHE.get(cache_key)
    if cached is not None:
        rows = [dict(row) for row in cached]  # type: ignore[union-attr]
    else:
        with get_connection(settings.database_path) as connection:
            rows = list_observations(
                connection,
                icao24=icao24.lower() if icao24 else None,
                source_kind=source_kind,
                from_ts=from_ts,
                to_ts=to_ts,
                ids=ids,
                limit=effective_limit,
            )
        CACHE.put(cache_key, rows)
    return [ObservationOut(**row) for row in rows]
