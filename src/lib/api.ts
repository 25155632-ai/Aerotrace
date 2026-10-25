/** Typed API client. Only endpoints that exist on the backend are
 *  represented here; adding a function for an unbuilt endpoint would
 *  let the UI imply a feature that does not exist. */

export interface HealthResponse {
  status: "ok";
  version: string;
  database: "ready";
}

export interface AlertDelivery {
  id: number;
  alert_id: number;
  channel: string;
  status: string;
  attempted_at: number;
  provider_response: string | null;
  error: string | null;
}

export interface AlertItem {
  id: number;
  rule_id: number;
  fingerprint: string;
  status: "open" | "acknowledged" | "resolved";
  severity: "low" | "medium" | "high";
  category: string;
  title: string;
  summary: string;
  aircraft: string[];
  detected_at: number;
  start_at: number;
  end_at: number;
  latitude: number | null;
  longitude: number | null;
  provenance: "real" | "simulated" | "mixed";
  source_kinds: string[];
  supporting_event_ids: number[];
  supporting_detection_ids: number[];
  evidence: Record<string, unknown>;
  created_at: number;
  deliveries: AlertDelivery[];
}

export interface AlertRule {
  id: number;
  name: string;
  enabled: boolean;
  min_severity: "low" | "medium" | "high";
  categories: string[];
  provenance: string[];
  region: Record<string, number>;
  min_isolation_anomaly_score: number | null;
  supervised_threshold: number | null;
  cooldown_seconds: number;
  group_window_seconds: number;
  group_radius_km: number;
  channels: string[];
  in_app: boolean;
  external: boolean;
  created_at: number;
  updated_at: number;
}

export interface ModelStatus {
  name: string;
  status: string;
  version: string | null;
  artifact_path: string | null;
  metadata: Record<string, unknown>;
  last_error: string | null;
  semantics: string;
}

export async function fetchAlerts(signal?: AbortSignal): Promise<AlertItem[]> {
  const body = await fetchJson("/api/alerts?limit=100", { signal: signal ?? null });
  if (!Array.isArray(body)) throw new Error("Backend returned an unexpected alerts response");
  return body as AlertItem[];
}

export async function fetchAlertRules(signal?: AbortSignal): Promise<AlertRule[]> {
  const body = await fetchJson("/api/alert-rules", { signal: signal ?? null });
  if (!Array.isArray(body)) throw new Error("Backend returned an unexpected alert rules response");
  return body as AlertRule[];
}

export async function fetchModelStatus(signal?: AbortSignal): Promise<ModelStatus[]> {
  const body = await fetchJson("/api/ml/status", { signal: signal ?? null });
  if (!Array.isArray(body)) throw new Error("Backend returned an unexpected ML status response");
  return body as ModelStatus[];
}

export function alertWebSocketUrl(): string {
  const base = API_BASE || window.location.origin;
  const url = new URL(base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/api/ws/alerts";
  return url.toString();
}

export interface SourceInfo {
  name: string;
  kind: string;
  status: string;
  note: string;
}

function isSourceInfo(value: unknown): value is SourceInfo {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as SourceInfo).name === "string" &&
    typeof (value as SourceInfo).kind === "string" &&
    typeof (value as SourceInfo).status === "string" &&
    typeof (value as SourceInfo).note === "string"
  );
}

export async function fetchSources(signal?: AbortSignal): Promise<SourceInfo[]> {
  const body = await fetchJson("/api/sources", { signal: signal ?? null });
  if (!Array.isArray(body) || !body.every(isSourceInfo)) {
    throw new Error("Backend returned an unexpected sources response");
  }
  return body as SourceInfo[];
}

export interface Observation {
  id: number;
  icao24: string;
  observed_at: number;
  latitude: number | null;
  longitude: number | null;
  baro_altitude_m: number | null;
  geo_altitude_m: number | null;
  velocity_ms: number | null;
  heading_deg: number | null;
  callsign: string | null;
  source: string;
  source_kind: "real" | "simulated";
  received_at: number;
  quality_note: string | null;
}

export interface ObservationsParams {
  sourceKind?: "real" | "simulated";
  fromTs?: number;
  toTs?: number;
  icao24?: string;
  limit?: number;
}

export interface Detection {
  id: number;
  rule: string;
  severity: string;
  kind: string;
  icao24: string;
  detected_at: number;
  latitude: number | null;
  longitude: number | null;
  evidence: Record<string, unknown>;
  supporting_observation_ids: number[];
  source_kind: "real" | "simulated" | "mixed";
  /** Distinct source kinds among the supporting observations. */
  source_kinds: string[];
}

export interface Verification {
  id: number;
  event_id: number;
  title: string;
  publisher: string | null;
  reference_url: string | null;
  accessed_date: string | null;
  note: string;
  submitted_by: string | null;
  status: string;
  created_at: number;
  revoked_reason: string | null;
}

export interface CandidateEvent {
  id: number;
  kind: string;
  severity: string;
  start_at: number;
  end_at: number;
  centroid_lat: number | null;
  centroid_lon: number | null;
  aircraft: string[];
  summary: string;
  caveat: string | null;
  status: string;
  member_anomaly_ids: number[];
  member_rules: string[];
  member_icao24: string[];
  verification: Verification | null;
  verification_history: Verification[];
}

export interface VerificationInput {
  title: string;
  publisher?: string;
  reference_url?: string;
  accessed_date?: string;
  note: string;
  submitted_by?: string;
}

async function postEventAction(
  eventId: number,
  action: "verify" | "revoke",
  body: unknown,
): Promise<CandidateEvent> {
  // Same-origin guard header required by the backend for state-changing
  // calls (see backend app/main.py). It is not authentication.
  const response = await fetch(`${API_BASE}/api/events/${eventId}/${action}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`Backend returned HTTP ${response.status}`);
  }
  const parsed = (await response.json()) as CandidateEvent;
  if (typeof parsed !== "object" || parsed === null || typeof parsed.id !== "number") {
    throw new Error("Backend returned an unexpected event response");
  }
  return parsed;
}

export interface CsvRowError {
  line: number;
  reason: string;
}

export interface CsvPreview {
  observations: Observation[];
  total_rows: number;
  valid_rows: number;
  error_rows: number;
  duplicate_rows: number;
  errors: CsvRowError[];
  errors_truncated: boolean;
}

export interface CsvImportReport {
  total_rows: number;
  valid_rows: number;
  error_rows: number;
  stored: number;
  duplicates: number;
  stale: number;
  missing_coordinates: number;
  errors: CsvRowError[];
  errors_truncated: boolean;
}

async function postCsv(
  action: "preview" | "",
  csvText: string,
  sourceKind: "real" | "simulated",
): Promise<unknown> {
  // Same-origin guard header required by the backend for state-changing
  // calls (see backend app/main.py). It is not authentication.
  const response = await fetch(
    `${API_BASE}/api/ingest/csv${action === "preview" ? "/preview" : ""}`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify({ csv_text: csvText, source_kind: sourceKind }),
    },
  );
  if (!response.ok) {
    throw new Error(`Backend returned HTTP ${response.status}`);
  }
  return response.json() as Promise<unknown>;
}

export async function postCsvPreview(
  csvText: string,
  sourceKind: "real" | "simulated",
): Promise<CsvPreview> {
  const body = (await postCsv("preview", csvText, sourceKind)) as CsvPreview;
  if (typeof body !== "object" || body === null || typeof body.total_rows !== "number") {
    throw new Error("Backend returned an unexpected CSV preview response");
  }
  return body;
}

export async function postCsvImport(
  csvText: string,
  sourceKind: "real" | "simulated",
): Promise<CsvImportReport> {
  const body = (await postCsv("", csvText, sourceKind)) as CsvImportReport;
  if (typeof body !== "object" || body === null || typeof body.stored !== "number") {
    throw new Error("Backend returned an unexpected CSV import response");
  }
  return body;
}

export interface LiveIngestReport extends IngestReport {
  dropped_without_icao24: number | null;
  snapshot_time: number | null;
}

export interface LiveBBox {
  lamin: number;
  lamax: number;
  lomin: number;
  lomax: number;
}

export async function postLiveIngest(bbox: LiveBBox): Promise<LiveIngestReport> {
  // Same-origin guard header required by the backend for state-changing
  // calls (see backend app/main.py). It is not authentication. On
  // failure the backend's detail message is surfaced instead of a bare
  // status code, so a rate limit or outage is explainable.
  const response = await fetch(`${API_BASE}/api/ingest/opensky`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
    },
    body: JSON.stringify(bbox),
  });
  if (!response.ok) {
    let message = `Backend returned HTTP ${response.status}`;
    try {
      const data = (await response.json()) as {
        detail?: { message?: string; retry_after_seconds?: number } | string;
      };
      const detail = data.detail;
      if (typeof detail === "object" && detail !== null) {
        if (detail.message) message = detail.message;
        if (detail.retry_after_seconds !== undefined) {
          message += ` Retry after ${detail.retry_after_seconds} seconds.`;
        }
      } else if (typeof detail === "string") {
        message = detail;
      }
    } catch {
      // Keep the status-based message when the body is unreadable.
    }
    throw new Error(message);
  }
  const body = (await response.json()) as LiveIngestReport;
  if (typeof body !== "object" || body === null || typeof body.stored !== "number") {
    throw new Error("Backend returned an unexpected live ingest response");
  }
  return body;
}

export function postVerifyEvent(
  eventId: number,
  input: VerificationInput,
): Promise<CandidateEvent> {
  return postEventAction(eventId, "verify", input);
}

export function postRevokeEvent(eventId: number, reason?: string): Promise<CandidateEvent> {
  return postEventAction(eventId, "revoke", { reason: reason ?? null });
}

export interface DetectReport {
  observations_analysed: number;
  aircraft_analysed: number;
  detections: Detection[];
  events: CandidateEvent[];
  summary: Record<string, unknown>;
}

export interface AnomaliesParams {
  rule?: string;
  severity?: string;
  kind?: string;
  icao24?: string;
  fromTs?: number;
  toTs?: number;
  minLat?: number;
  maxLat?: number;
  minLon?: number;
  maxLon?: number;
  ids?: number[];
  sort?: "detected_at_asc" | "detected_at_desc" | "severity_desc";
  offset?: number;
  limit?: number;
}

export interface EventsParams {
  kind?: string;
  fromTs?: number;
  toTs?: number;
  offset?: number;
  limit?: number;
}

export function anomaliesQuery(params: AnomaliesParams): URLSearchParams {
  const query = new URLSearchParams();
  if (params.rule) query.set("rule", params.rule);
  if (params.severity) query.set("severity", params.severity);
  if (params.kind) query.set("kind", params.kind);
  if (params.icao24) query.set("icao24", params.icao24);
  if (params.fromTs !== undefined) query.set("from_ts", String(params.fromTs));
  if (params.toTs !== undefined) query.set("to_ts", String(params.toTs));
  if (params.minLat !== undefined) query.set("min_lat", String(params.minLat));
  if (params.maxLat !== undefined) query.set("max_lat", String(params.maxLat));
  if (params.minLon !== undefined) query.set("min_lon", String(params.minLon));
  if (params.maxLon !== undefined) query.set("max_lon", String(params.maxLon));
  if (params.ids) for (const id of params.ids) query.append("ids", String(id));
  if (params.sort) query.set("sort", params.sort);
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  return query;
}

export function anomaliesExportUrl(params: AnomaliesParams): string {
  const query = anomaliesQuery(params);
  query.delete("sort");
  query.delete("offset");
  query.delete("limit");
  query.delete("ids");
  return `${API_BASE}/api/anomalies/export?${query.toString()}`;
}

export function eventsExportUrl(params: EventsParams): string {
  const query = new URLSearchParams();
  if (params.kind) query.set("kind", params.kind);
  if (params.fromTs !== undefined) query.set("from_ts", String(params.fromTs));
  if (params.toTs !== undefined) query.set("to_ts", String(params.toTs));
  return `${API_BASE}/api/events/export?${query.toString()}`;
}

export async function fetchAnomalies(
  params: AnomaliesParams,
  signal?: AbortSignal,
): Promise<Detection[]> {
  const body = await fetchJson(`/api/anomalies?${anomaliesQuery(params).toString()}`, { signal: signal ?? null });
  if (!Array.isArray(body)) {
    throw new Error("Backend returned an unexpected anomalies response");
  }
  return body as Detection[];
}

export async function fetchEvents(
  params: EventsParams,
  signal?: AbortSignal,
): Promise<CandidateEvent[]> {
  const query = new URLSearchParams();
  if (params.kind) query.set("kind", params.kind);
  if (params.fromTs !== undefined) query.set("from_ts", String(params.fromTs));
  if (params.toTs !== undefined) query.set("to_ts", String(params.toTs));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  const body = await fetchJson(`/api/events?${query.toString()}`, { signal: signal ?? null });
  if (!Array.isArray(body)) {
    throw new Error("Backend returned an unexpected events response");
  }
  return body as CandidateEvent[];
}

export async function fetchObservationsByIds(
  ids: number[],
  signal?: AbortSignal,
): Promise<Observation[]> {
  if (ids.length === 0) return [];
  const query = new URLSearchParams();
  for (const id of ids) query.append("ids", String(id));
  query.set("limit", "1000");
  const body = await fetchJson(`/api/observations?${query.toString()}`, { signal: signal ?? null });
  if (!Array.isArray(body)) {
    throw new Error("Backend returned an unexpected observations response");
  }
  return body as Observation[];
}

export async function postDetect(): Promise<DetectReport> {
  // Same-origin guard header required by the backend for state-changing
  // calls (see backend app/main.py). It is not authentication.
  const body = await fetchJson("/api/detect", {
    method: "POST",
    headers: { "X-Requested-With": "XMLHttpRequest" },
  });
  if (
    typeof body !== "object" ||
    body === null ||
    !Array.isArray((body as DetectReport).detections)
  ) {
    throw new Error("Backend returned an unexpected detect response");
  }
  return body as DetectReport;
}

export interface IngestReport {
  total: number;
  stored: number;
  duplicates: number;
  stale: number;
  missing_coordinates: number;
  rejected: { index: number; reason: string }[];
  source: string | null;
}

async function fetchJson(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    throw new Error(`Backend returned HTTP ${response.status}`);
  }
  return response.json() as Promise<unknown>;
}

export async function fetchObservations(
  params: ObservationsParams,
  signal?: AbortSignal,
): Promise<Observation[]> {
  const query = new URLSearchParams();
  if (params.sourceKind) query.set("source_kind", params.sourceKind);
  if (params.fromTs !== undefined) query.set("from_ts", String(params.fromTs));
  if (params.toTs !== undefined) query.set("to_ts", String(params.toTs));
  if (params.icao24) query.set("icao24", params.icao24);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  const body = await fetchJson(`/api/observations?${query.toString()}`, { signal: signal ?? null });
  if (!Array.isArray(body)) {
    throw new Error("Backend returned an unexpected observations response");
  }
  return body as Observation[];
}

export async function postDemoIngest(): Promise<IngestReport> {
  // Same-origin guard header required by the backend for state-changing
  // calls (see backend app/main.py). It is not authentication.
  const body = await fetchJson("/api/ingest/demo", {
    method: "POST",
    headers: { "X-Requested-With": "XMLHttpRequest" },
  });
  if (
    typeof body !== "object" ||
    body === null ||
    typeof (body as IngestReport).total !== "number"
  ) {
    throw new Error("Backend returned an unexpected ingest response");
  }
  return body as IngestReport;
}

// Relative in dev (Vite proxies /api to the backend); override with
// VITE_API_BASE_URL when the frontend is served from another origin.
const API_BASE = import.meta.env["VITE_API_BASE_URL"] ?? "";

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(`${API_BASE}/api/health`, {
      signal: signal ?? controller.signal,
    });
    if (!response.ok) {
      throw new Error(`Backend returned HTTP ${response.status}`);
    }
    const body: unknown = await response.json();
    if (typeof body !== "object" || body === null || (body as HealthResponse).status !== "ok") {
      throw new Error("Backend returned an unexpected health response");
    }
    return body as HealthResponse;
  } finally {
    clearTimeout(timeout);
  }
}
