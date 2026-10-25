import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import CsvTracking from "./CsvTracking";
import AirspaceView from "./AirspaceView";
import LetterSwap from "./originkit/LetterSwap";
import OrbitButton from "./originkit/OrbitButton";
import {
  fetchAlerts,
  fetchAnomalies,
  fetchEvents,
  fetchHealth,
  fetchModelStatus,
  fetchObservations,
  fetchSources,
  postCsvImport,
  postCsvPreview,
  postDemoIngest,
  postDetect,
  type AlertItem,
  type CandidateEvent,
  type Detection,
  type HealthResponse,
  type ModelStatus,
  type Observation,
  type SourceInfo,
  type CsvPreview,
  type CsvImportReport,
} from "../lib/api";
import { formatTs, groupTracks, type Track } from "../lib/explorer";

type Tab = "overview" | "aircraft" | "investigation" | "alerts" | "import";
type TargetFilter = "all" | "signals";

const PITCH_SNAPSHOT = {
  tracks: 38,
  observations: 1248,
  highSeverity: 14,
  detections: 23,
  events: 3,
  alerts: 5,
  liveSource: "PITCH PRESET",
};

const PITCH_TARGETS = [
  { id: "a4c91b", callsign: "EW-409", lat: 34.12, lon: 35.81, status: "critical" },
  { id: "4ca1f2", callsign: "BA-117", lat: 34.82, lon: 33.62, status: "warning" },
  { id: "3b7d20", callsign: "SV-882", lat: 33.91, lon: 35.49, status: "nominal" },
  { id: "71a2c4", callsign: "TH-019", lat: 32.11, lon: 34.82, status: "warning" },
];

const AUTHENTIC_SAMPLES = [
  { file: "real-opensky-europe-central.csv", label: "Central Europe", rows: "180 aircraft states" },
  { file: "real-opensky-gulf.csv", label: "Gulf / Arabian Peninsula", rows: "113 aircraft states" },
  { file: "real-opensky-india-west.csv", label: "Western India", rows: "27 aircraft states" },
];

const fallbackTrack: Track = {
  icao24: "a4c91b",
  callsign: "EW-409",
  source: "demo",
  sourceKind: "simulated",
  points: [],
  latest: {
    id: 0,
    icao24: "a4c91b",
    observed_at: 0,
    latitude: 34.12,
    longitude: 35.81,
    baro_altitude_m: 8961,
    geo_altitude_m: 12558,
    velocity_ms: 253,
    heading_deg: 42,
    callsign: "EW-409",
    source: "demo",
    source_kind: "simulated",
    received_at: 0,
    quality_note: null,
  },
  latestPosition: null,
};

function toneForSeverity(value: string) {
  if (value === "high" || value === "critical") return "danger";
  if (value === "medium" || value === "warning") return "warning";
  return "ok";
}

function StatCard({
  label,
  value,
  detail,
  tone = "primary",
}: {
  label: string;
  value: string | number;
  detail: string;
  tone?: string;
}) {
  return (
    <div className={`stat-card stat-${tone}`}>
      <span className="eyebrow">{label}</span>
      <strong>{value}</strong>
      <span>{detail}</span>
    </div>
  );
}

function Panel({
  title,
  kicker,
  children,
  className = "",
}: {
  title: string;
  kicker?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`hud-panel ${className}`}>
      <div className="panel-heading">
        <div>
          {kicker && <span className="eyebrow">{kicker}</span>}
          <h2>{title}</h2>
        </div>
        <span className="panel-signal" aria-hidden="true" />
      </div>
      {children}
    </section>
  );
}

function CsvImportPanel({ onImported, onViewOnGlobe }: {
  onImported: () => Promise<void>;
  onViewOnGlobe: (rows: Observation[], name: string, saved: boolean) => void;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [csvText, setCsvText] = useState("");
  const [sourceKind, setSourceKind] = useState<"simulated" | "real">("simulated");
  const [fileName, setFileName] = useState<string | null>(null);
  const [preview, setPreview] = useState<CsvPreview | null>(null);
  const [report, setReport] = useState<CsvImportReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const readFile = async (file: File) => {
    setError(null);
    setReport(null);
    setPreview(null);
    setCsvText("");
    setFileName(file.name);
    if (file.size > 4_000_000) return setError("CSV exceeds the 1,000,000-character import limit.");
    setBusy(true);
    try {
      const text = await file.text();
      if (text.length > 1_000_000) throw new Error("CSV exceeds the 1,000,000-character import limit.");
      setCsvText(text);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not read file");
    } finally {
      setBusy(false);
    }
  };

  const previewFile = async () => {
    if (!csvText.trim()) return setError("Choose a CSV file first.");
    setBusy(true);
    setError(null);
    try {
      setPreview(await postCsvPreview(csvText, sourceKind));
    } catch (err) {
      setError(err instanceof Error ? err.message : "CSV preview failed");
    } finally {
      setBusy(false);
    }
  };

  const importFile = async () => {
    if (!csvText.trim()) return setError("Choose a CSV file first.");
    setBusy(true);
    setError(null);
    try {
      if (!preview) setPreview(await postCsvPreview(csvText, sourceKind));
      setReport(await postCsvImport(csvText, sourceKind));
      await onImported();
    } catch (err) {
      setError(err instanceof Error ? err.message : "CSV import failed");
    } finally {
      setBusy(false);
    }
  };

  const loadSample = async (file: string, source: "simulated" | "real") => {
    setBusy(true);
    setError(null);
    setPreview(null);
    setReport(null);
    try {
      const response = await fetch(`/samples/${file}`);
      if (!response.ok) throw new Error(`Could not load ${file}`);
      setCsvText(await response.text());
      setFileName(file);
      setSourceKind(source);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sample load failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="import-view">
      <Panel title="Flight data import" kicker="INGESTION WORKBENCH">
        <div
          className="import-dropzone"
          onClick={() => { if (!busy) inputRef.current?.click(); }}
          role="button"
          tabIndex={0}
          onKeyDown={(event) => {
            if (!busy && (event.key === "Enter" || event.key === " ")) {
              event.preventDefault();
              inputRef.current?.click();
            }
          }}
        >
          <span className="material-symbols-outlined">upload_file</span>
          <strong>{fileName ?? "Choose a flight observation CSV"}</strong>
          <span>Preview validates the CSV and opens aircraft playback. Import saves valid rows.</span>
          <input
            ref={inputRef}
            type="file"
            disabled={busy}
            accept=".csv,text/csv"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void readFile(file);
            }}
          />
        </div>
        <div className="import-controls">
          <label>
            Provenance
            <select
              value={sourceKind}
              disabled={busy}
              onChange={(event) => {
                setSourceKind(event.target.value as "simulated" | "real");
                setPreview(null);
                setReport(null);
              }}
            >
              <option value="simulated">Simulated / demo</option>
              <option value="real">Real observation</option>
            </select>
          </label>
          <div className="import-actions">
            <button
              className="action-button"
              onClick={() => void previewFile()}
              disabled={busy || !csvText}
            >
              {busy ? "Working…" : "Preview & track CSV"}
            </button>
            <button
              className="primary-button"
              onClick={() => void importFile()}
              disabled={busy || !csvText}
            >
              Import valid rows
            </button>
          </div>
        </div>
        {error && <div className="command-toast import-error">{error}</div>}
        {preview && (
          <div className="import-result">
            <span>
              <strong>{preview.total_rows}</strong> rows
            </span>
            <span>
              <strong>{preview.valid_rows}</strong> valid
            </span>
            <span>
              <strong>{preview.error_rows}</strong> errors
            </span>
            <span>
              <strong>{preview.duplicate_rows}</strong> duplicates
            </span>
          </div>
        )}
        {report && (
          <div className="import-result success">
            <span>
              <strong>{report.stored}</strong> stored
            </span>
            <span>
              <strong>{report.duplicates}</strong> duplicates
            </span>
            <span>
              <strong>{report.error_rows}</strong> skipped
            </span>
          </div>
        )}
      </Panel>
      {preview && preview.observations.length > 0 && (
        <>
          <div className="csv-globe-action">
            <button className="primary-button" disabled={busy} onClick={() => onViewOnGlobe(preview.observations, fileName ?? "CSV recording", Boolean(report))}>Show on globe</button>
            <span>Open this recording on the main globe. {report ? "Rows were imported; run detection to generate findings." : "Preview only; import rows before running detection."}</span>
          </div>
          <CsvTracking key={`${fileName}-${sourceKind}`} observations={preview.observations} />
        </>
      )}
      {(report?.errors ?? preview?.errors ?? []).length > 0 && (
        <Panel title="Rows requiring attention" kicker="VALIDATION REPORT">
          <ul className="csv-errors">
            {(report?.errors ?? preview?.errors ?? []).map((error, index) => (
              <li key={`${error.line}-${index}`}>Line {error.line}: {error.reason}</li>
            ))}
          </ul>
          {(report?.errors_truncated ?? preview?.errors_truncated) && <p>Showing the first 100 errors.</p>}
        </Panel>
      )}
      <Panel title="Authentic test samples" kicker="NO FILE REQUIRED">
        <p className="import-help">
          These are authentic point-in-time OpenSky Network state-vector snapshots, labelled
          <code>source_kind=real</code>. Each matching provenance card lists the capture time,
          bounding box, and row count.
        </p>
        <div className="sample-list">
          {AUTHENTIC_SAMPLES.map((sample) => (
            <button
              className="sample-button"
              disabled={busy}
              key={sample.file}
              onClick={() => void loadSample(sample.file, "real")}
            >
              <span>
                <strong>{sample.label}</strong>
                <small>{sample.rows} · OpenSky snapshot</small>
              </span>
              <span className="material-symbols-outlined">download</span>
            </button>
          ))}
        </div>
        <div className="sample-divider">
          <span>or use the deterministic synthetic replay</span>
        </div>
        <button
          className="action-button"
          disabled={busy}
          onClick={() => void loadSample("coordinated-spoof-replay.csv", "simulated")}
        >
          Load bundled scenario
        </button>
      </Panel>
    </section>
  );
}

type ReplayStep = {
  at: number;
  label: string;
  detail: string;
  tone: "info" | "warning" | "danger";
  aircraft: string[];
};

const PITCH_REPLAY_STEPS: ReplayStep[] = [
  {
    at: 0,
    label: "Baseline track",
    detail: "Three aircraft are reporting a stable corridor position.",
    tone: "info",
    aircraft: ["EW-409", "BA-117", "SV-882"],
  },
  {
    at: 8,
    label: "Position discontinuity",
    detail: "A shared lateral offset appears across the corridor.",
    tone: "warning",
    aircraft: ["EW-409", "BA-117"],
  },
  {
    at: 16,
    label: "Correlation detected",
    detail: "The pattern is now present across three aircraft in the same window.",
    tone: "danger",
    aircraft: ["EW-409", "BA-117", "SV-882"],
  },
  {
    at: 24,
    label: "Candidate event created",
    detail: "The analyst queue receives a candidate event for review, not a confirmation.",
    tone: "danger",
    aircraft: ["EW-409", "BA-117", "SV-882"],
  },
];

function IncidentReplayPanel({
  event,
  detections,
  observations,
  pitchMode,
}: {
  event: CandidateEvent | null;
  detections: Detection[];
  observations: Observation[];
  pitchMode: boolean;
}) {
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<1 | 2 | 5>(1);
  const [progress, setProgress] = useState(0);
  const [soundEnabled, setSoundEnabled] = useState(true);
  const audioContextRef = useRef<AudioContext | null>(null);
  const previousStepRef = useRef<string | null>(null);

  const playCue = async (tone: ReplayStep["tone"]) => {
    if (!pitchMode || !soundEnabled || typeof window === "undefined") return;
    const AudioContextClass =
      window.AudioContext ||
      (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextClass) return;
    const context = audioContextRef.current ?? new AudioContextClass();
    audioContextRef.current = context;
    if (context.state === "suspended") await context.resume();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    const frequency = tone === "danger" ? 660 : tone === "warning" ? 480 : 360;
    oscillator.frequency.value = frequency;
    oscillator.type = tone === "danger" ? "square" : "sine";
    gain.gain.setValueAtTime(0.0001, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.16, context.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.28);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.3);
  };

  const replay = useMemo(() => {
    if (pitchMode || !event) {
      return { start: 0, end: 24, steps: PITCH_REPLAY_STEPS };
    }

    const start = event.start_at;
    const end = Math.max(event.end_at, start + 1);
    const memberIds = new Set(event.member_anomaly_ids);
    const eventDetections = detections
      .filter((detection) => memberIds.has(detection.id))
      .sort((a, b) => a.detected_at - b.detected_at);
    const steps: ReplayStep[] = [
      {
        at: start,
        label: "Event window opened",
        detail: `${event.aircraft.length} aircraft in the candidate event window.`,
        tone: "info",
        aircraft: event.aircraft,
      },
      ...eventDetections.map((detection) => ({
        at: Math.min(Math.max(detection.detected_at, start), end),
        label: detection.rule.replaceAll("_", " "),
        detail: `${detection.severity} severity finding for ${detection.icao24}.`,
        tone: toneForSeverity(detection.severity) as ReplayStep["tone"],
        aircraft: [detection.icao24],
      })),
      {
        at: end,
        label: "Candidate event ready",
        detail: "Review the supporting observations before drawing a conclusion.",
        tone: "warning",
        aircraft: event.aircraft,
      },
    ];
    return { start, end, steps };
  }, [detections, event, pitchMode]);

  useEffect(() => {
    setProgress(0);
    setPlaying(false);
    previousStepRef.current = null;
  }, [event?.id, pitchMode]);

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      setProgress((current) => {
        const next = Math.min(1, current + 0.01 * speed);
        if (next >= 1) setPlaying(false);
        return next;
      });
    }, 100);
    return () => window.clearInterval(timer);
  }, [playing, speed]);

  const currentTime = replay.start + (replay.end - replay.start) * progress;
  const currentStep = replay.steps.reduce(
    (latest, step) => (step.at <= currentTime ? step : latest),
    replay.steps[0]!,
  );
  useEffect(() => {
    if (previousStepRef.current && previousStepRef.current !== currentStep.label) {
      void playCue(currentStep.tone);
    }
    previousStepRef.current = currentStep.label;
  }, [currentStep.label, currentStep.tone]);

  const activeObservations = useMemo(() => {
    if (!event || pitchMode) return [];
    const allowed = new Set(event.aircraft);
    const latest = new Map<string, Observation>();
    observations
      .filter(
        (observation) =>
          allowed.has(observation.icao24) &&
          observation.observed_at <= currentTime &&
          observation.observed_at >= event.start_at - 600,
      )
      .sort((a, b) => a.observed_at - b.observed_at)
      .forEach((observation) => latest.set(observation.icao24, observation));
    return [...latest.values()];
  }, [currentTime, event, observations, pitchMode]);
  const activeAircraft =
    activeObservations.length > 0
      ? activeObservations.map((observation) => observation.icao24)
      : currentStep.aircraft;

  return (
    <Panel title="Incident replay" kicker="TEMPORAL EVIDENCE">
      <div className="replay-readout">
        <div className="replay-readout-top">
          <span className={`badge ${currentStep.tone}`}>{currentStep.label}</span>
          <span className="eyebrow">
            {pitchMode
              ? "PITCH PRESET"
              : event
                ? `EVT-${String(event.id).padStart(4, "0")}`
                : "NO EVENT"}
          </span>
        </div>
        <strong>{pitchMode ? `${Math.round(currentTime)}s` : formatTs(currentTime)}</strong>
        <p>{currentStep.detail}</p>
      </div>
      <div className="replay-controls">
        <button
          className="primary-button"
          onClick={() => {
            if (progress >= 1) setProgress(0);
            if (pitchMode) void playCue("info");
            setPlaying((value) => !value);
          }}
        >
          <span className="material-symbols-outlined">{playing ? "pause" : "play_arrow"}</span>
          {playing ? "Pause" : progress >= 1 ? "Replay" : "Play"}
        </button>
        <input
          className="replay-slider"
          type="range"
          min="0"
          max="1"
          step="0.01"
          value={progress}
          aria-label="Incident replay timeline"
          onChange={(input) => {
            setPlaying(false);
            setProgress(Number(input.target.value));
          }}
        />
        <span className="replay-percent">{Math.round(progress * 100)}%</span>
        <div className="replay-speed" aria-label="Replay speed">
          {[1, 2, 5].map((value) => (
            <button
              className={speed === value ? "active" : ""}
              key={value}
              onClick={() => setSpeed(value as 1 | 2 | 5)}
            >
              {value}x
            </button>
          ))}
        </div>
        {pitchMode && (
          <>
            <button
              className="replay-sound active"
              onClick={() => void playCue("info")}
              disabled={!soundEnabled}
              aria-label="Test replay sound"
            >
              <span className="material-symbols-outlined">volume_up</span>
              Test sound
            </button>
            <button
              className={`replay-sound ${soundEnabled ? "active" : ""}`}
              onClick={() => setSoundEnabled((value) => !value)}
              aria-label={soundEnabled ? "Mute replay audio" : "Enable replay audio"}
            >
              <span className="material-symbols-outlined">
                {soundEnabled ? "volume_mute" : "volume_off"}
              </span>
              {soundEnabled ? "Mute" : "Enable"}
            </button>
          </>
        )}
      </div>
      <div className="replay-steps">
        {replay.steps.map((step, index) => {
          const stepProgress = (step.at - replay.start) / (replay.end - replay.start || 1);
          return (
            <button
              className={`replay-step ${step.at <= currentTime ? "passed" : ""}`}
              key={`${step.label}-${index}`}
              onClick={() => {
                setPlaying(false);
                setProgress(Math.max(0, Math.min(1, stepProgress)));
              }}
            >
              <span className={`timeline-dot ${step.tone}`} />
              <span>
                <strong>{step.label}</strong>
                <small>{pitchMode ? `${Math.round(step.at)}s` : formatTs(step.at)}</small>
              </span>
            </button>
          );
        })}
      </div>
      <div className="replay-footer">
        <span className="eyebrow">ACTIVE AIRCRAFT</span>
        <div className="replay-aircraft">
          {activeAircraft.map((aircraft) => (
            <span key={aircraft}>{aircraft}</span>
          ))}
        </div>
        <small>
          {pitchMode
            ? "Deterministic presentation replay — simulated data. Audio cues are presentation-only."
            : "Replay is an evidence view; it does not confirm spoofing or interference."}
        </small>
      </div>
    </Panel>
  );
}

export function Dashboard() {
  const [tab, setTab] = useState<Tab>("overview");
  const [targetFilter, setTargetFilter] = useState<TargetFilter>("all");
  const [pitchMode, setPitchMode] = useState(false);
  const [globeKey, setGlobeKey] = useState(0);
  const [globeRecording, setGlobeRecording] = useState<{ observations: Observation[]; name: string; saved: boolean } | null>(null);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [replayEventId, setReplayEventId] = useState<number | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [sources, setSources] = useState<SourceInfo[]>([]);
  const [observations, setObservations] = useState<Observation[]>([]);
  const [detections, setDetections] = useState<Detection[]>([]);
  const [events, setEvents] = useState<CandidateEvent[]>([]);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [models, setModels] = useState<ModelStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);


  const refresh = async () => {
    setLoading(true);
    try {
      const [
        healthData,
        sourceData,
        observationData,
        detectionData,
        eventData,
        alertData,
        modelData,
      ] = await Promise.all([
        fetchHealth(),
        fetchSources(),
        fetchObservations({ limit: 1000 }),
        fetchAnomalies({ limit: 1000 }),
        fetchEvents({ limit: 100 }),
        fetchAlerts(),
        fetchModelStatus(),
      ]);
      setHealth(healthData);
      setSources(sourceData);
      setObservations(observationData);
      setDetections(detectionData);
      setEvents(eventData);
      setAlerts(alertData);
      setModels(modelData);
      setMessage(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Command feed unavailable");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const tracks = useMemo(() => groupTracks(observations), [observations]);
  const filteredTracks = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return tracks.filter(
      (track) =>
        !needle ||
        track.icao24.toLowerCase().includes(needle) ||
        (track.callsign ?? "").toLowerCase().includes(needle),
    );
  }, [tracks, query]);
  const storedSelectedTrack =
    filteredTracks.find((track) => track.icao24 === selectedId) ?? filteredTracks[0];
  const selectedPitchTarget =
    PITCH_TARGETS.find((target) => target.id === selectedId) ?? PITCH_TARGETS[0]!;
  const selectedTrack =
    pitchMode && (!storedSelectedTrack || PITCH_TARGETS.some((target) => target.id === selectedId))
      ? {
          ...fallbackTrack,
          icao24: selectedPitchTarget.id,
          callsign: selectedPitchTarget.callsign,
          latest: {
            ...fallbackTrack.latest,
            icao24: selectedPitchTarget.id,
            callsign: selectedPitchTarget.callsign,
            latitude: selectedPitchTarget.lat,
            longitude: selectedPitchTarget.lon,
          },
        }
      : (storedSelectedTrack ?? fallbackTrack);
  const liveSource = sources.find((source) => source.name === "opensky-live");
  const realCount = observations.filter((item) => item.source_kind === "real").length;
  const simulatedCount = observations.filter((item) => item.source_kind === "simulated").length;
  const highSeverity = detections.filter(
    (item) => toneForSeverity(item.severity) === "danger",
  ).length;
  const signalIds = useMemo(
    () => new Set(detections.map((detection) => detection.icao24)),
    [detections],
  );
  const displayedTracks =
    targetFilter === "signals"
      ? filteredTracks.filter((track) => signalIds.has(track.icao24))
      : filteredTracks;
  const targetCards = displayedTracks.slice(0, 8);
  const displayTracks = pitchMode ? PITCH_SNAPSHOT.tracks : tracks.length;
  const displayObservations = pitchMode ? PITCH_SNAPSHOT.observations : observations.length;
  const displayHighSeverity = pitchMode ? PITCH_SNAPSHOT.highSeverity : highSeverity;
  const displayDetections = pitchMode ? PITCH_SNAPSHOT.detections : detections.length;
  const displayEvents = pitchMode ? PITCH_SNAPSHOT.events : events.length;
  const displayAlerts = pitchMode ? PITCH_SNAPSHOT.alerts : alerts.length;
  const replayEvent = events.find((event) => event.id === replayEventId) ?? events[0] ?? null;

  const runCommand = async (command: () => Promise<unknown>, success: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await command();
      setMessage(success);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Command failed");
    } finally {
      setBusy(false);
    }
  };

  const targetPoints =
    selectedTrack.points.length > 0 ? selectedTrack.points : pitchMode ? [selectedTrack.latest] : [];
  const latest = selectedTrack.latestPosition ?? selectedTrack.latest;
  const latestTime = formatTs(selectedTrack.latest.observed_at);

  return (
    <div className="app-shell">
      <div className="ambient-grid" aria-hidden="true" />
      <div className="world-backdrop airspace-backdrop" aria-hidden="true" />

      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark">
            <span className="material-symbols-outlined">radar</span>
            <i />
          </div>
          <div>
            <div className="brand-name">
              Aerotrace <small>SIGINT</small>
            </div>
            <div className="brand-subtitle">AEROSPACE CYBER-INTELLIGENCE // GLOBAL TELEMETRY</div>
          </div>
        </div>
        <nav className="main-nav" aria-label="Primary navigation">
          {(["overview", "aircraft", "investigation", "alerts", "import"] as Tab[]).map((item) => (
            <button
              key={item}
              className={tab === item ? "active" : ""}
              onClick={() => setTab(item)}
            >
              {item === "overview"
                ? "Overview"
                : item === "aircraft"
                  ? "Aircraft Explorer"
                  : item === "investigation"
                    ? "Investigation Lab"
                    : item === "alerts"
                      ? "Alerts"
                      : "CSV Import"}
            </button>
          ))}
        </nav>
        <button
          className={`pitch-toggle ${pitchMode ? "active" : ""}`}
          onClick={() => setPitchMode((value) => !value)}
          title="Toggle pitch-ready demo values"
        >
          <span className="material-symbols-outlined">present_to_all</span>
          {pitchMode ? "Pitch" : "Live"}
        </button>
        <button className="icon-button" title="Refresh command feed" onClick={() => void refresh()}>
          <span className="material-symbols-outlined">refresh</span>
        </button>
      </header>

      <main className="page-content">
        <section className="hero-card">
          <div className="hero-media">
            <div className="hero-video-pan" />
            <div className="hero-scanlines" />
            <div className="hero-sweep" />
            <div className="live-tag">
              <span /> LIVE FEED // CAM-07
            </div>
            <div className="sector-tag">
              <span /> SECTOR: MEDITERRANEAN // FIR NICOSIA-BEIRUT
            </div>
          </div>
          <div className="hero-copy">
            <div className="badge-row">
              <span className="badge primary">DEFENSE-GRADE SIGINT</span>
              <span className="badge neutral">ICAO HEX: {selectedTrack.icao24.toUpperCase()}</span>
              <span className="badge danger">
                <span className="material-symbols-outlined">warning</span>
                {displayHighSeverity ? "ANOMALY DETECTION ACTIVE" : "AWAITING ANALYSIS"}
              </span>
            </div>
            <h1>
              <LetterSwap
                reverse={false}
                label="Aviation Intelligence. Real-Time Awareness."
                mode="pingpong"
                staggerDuration={0.02}
                color="#e0e7ff"
                font={{
                  fontFamily: "Space Grotesk",
                  fontSize: 32,
                  lineHeight: "1.25em",
                  fontWeight: 700,
                  textAlign: "left",
                }}
                style={{ justifyContent: "flex-start" }}
              />
            </h1>
            <p>
              Continuous orbital surveillance, kinematic trajectory verification, and multimodal
              GNSS anomaly detection across contested airspace corridors.
            </p>
            <div className="protocol-note">
              <span className="material-symbols-outlined">verified_user</span>
              <div>
                <strong>SCIENTIFIC PROTOCOL // ICAO ANNEX 10</strong>
                <span>
                  <em>Trajectory discontinuity ≠ GNSS spoofing confirmed.</em> Evidence is surfaced
                  for analyst review; the system does not claim intent from tracking data alone.
                </span>
              </div>
            </div>
            <div className="hero-actions">
              <div
                onClick={() => {
                  setGlobeKey(value => value + 1);
                  document
                    .getElementById("trajectory-view")
                    ?.scrollIntoView({ behavior: "smooth", block: "center" });
                }}
                role="button"
                tabIndex={0}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    setGlobeKey(value => value + 1);
                    document
                      .getElementById("trajectory-view")
                      ?.scrollIntoView({ behavior: "smooth", block: "center" });
                  }
                }}
              >
                <OrbitButton
                  label="Launch Globe"
                  fill="#818cf8"
                  textColor="#1e1b4b"
                  rounded={8}
                  padding="9px 16px"
                  stroke={{
                    size: 30,
                    color: "#e0e7ff",
                    speed: 50,
                    direction: "ccw",
                    hoverFill: 100,
                  }}
                  border={{
                    borderColor: "rgba(224,231,255,0.3)",
                    borderStyle: "solid",
                    borderWidth: 1,
                  }}
                  font={{
                    fontFamily: "Space Grotesk",
                    fontSize: 11,
                    fontWeight: 700,
                    letterSpacing: "0.12em",
                    textTransform: "uppercase",
                  }}
                  style={{ cursor: "none" }}
                />
              </div>
              <button
                className="action-button"
                onClick={() =>
                  void runCommand(postDemoIngest, "Demonstration observations loaded.")
                }
                disabled={busy}
              >
                <span className="material-symbols-outlined">stream</span>
                {busy ? "Syncing…" : "Load Demo Data"}
              </button>
              <button
                className="action-button"
                onClick={() => void runCommand(postDetect, "Detection run completed.")}
                disabled={busy}
              >
                <span className="material-symbols-outlined">biotech</span>Run Detection
              </button>
            </div>
            <div className="provenance-row">
              <span>[REAL {pitchMode ? "—" : realCount}]</span>
              <span>[SIMULATED {pitchMode ? PITCH_TARGETS.length : simulatedCount}]</span>
              <span>[EVENTS {displayEvents}]</span>
              <span>[ALERTS {displayAlerts}]</span>
            </div>
          </div>
        </section>

        {message && (
          <div className="command-toast" role="status">
            <span className="material-symbols-outlined">info</span>
            {message}
            <button onClick={() => setMessage(null)} aria-label="Dismiss">
              ×
            </button>
          </div>
        )}

        <section className="stat-grid">
          <StatCard
            label="Backend status"
            value={health?.status === "ok" ? "ONLINE" : loading ? "CHECKING" : "OFFLINE"}
            detail={health ? `v${health.version} // database ready` : "Waiting for local API"}
            tone={health ? "ok" : "warning"}
          />
          <StatCard
            label="Tracked aircraft"
            value={displayTracks}
            detail={`${displayObservations} observations in view${pitchMode ? " // pitch preset" : ""}`}
          />
          <StatCard
            label="High-severity signals"
            value={displayHighSeverity}
            detail={`${displayDetections} total detections${pitchMode ? " // pitch preset" : ""}`}
            tone={displayHighSeverity ? "danger" : "ok"}
          />
          <StatCard
            label="Live source"
            value={
              pitchMode
                ? PITCH_SNAPSHOT.liveSource
                : liveSource?.status === "verified_live"
                  ? "VERIFIED"
                  : "NOT VERIFIED"
            }
            detail={
              pitchMode
                ? "Curated demo values for the presentation"
                : (liveSource?.note ?? "Use demo data for a local walkthrough")
            }
            tone={pitchMode ? "primary" : liveSource?.status === "verified_live" ? "ok" : "warning"}
          />
        </section>

        {(tab === "overview" || tab === "aircraft") && (
          <section className="command-grid">
            <aside className="left-column">
              <Panel title="Active targets" kicker="AIRSPACE SURVEILLANCE">
                <div className="panel-meta">
                  <span>{filteredTracks.length || 0} TRACKS</span>
                  <span className="status-live">
                    <i /> LIVE INDEX
                  </span>
                </div>
                <div className="segmented">
                  <button
                    className={targetFilter === "all" ? "active" : ""}
                    onClick={() => setTargetFilter("all")}
                  >
                    All ({pitchMode ? PITCH_SNAPSHOT.tracks : filteredTracks.length})
                  </button>
                  <button
                    className={targetFilter === "signals" ? "active" : ""}
                    onClick={() => setTargetFilter("signals")}
                  >
                    Signals ({pitchMode ? PITCH_TARGETS.length : highSeverity})
                  </button>
                  <button
                    onClick={() => {
                      setTargetFilter("all");
                      setQuery("");
                      setSelectedId(null);
                    }}
                  >
                    Clear
                  </button>
                </div>
                <label className="search-field">
                  <span className="material-symbols-outlined">search</span>
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Search callsign or ICAO hex"
                  />
                </label>
                <div className="target-list">
                  {targetCards.length === 0 && (
                    <div className="empty-copy">
                      No stored tracks yet. Load the labelled demo dataset to populate the command
                      feed.
                    </div>
                  )}
                  {targetCards.map((track) => (
                    <button
                      key={track.icao24}
                      className={`target-card ${selectedTrack.icao24 === track.icao24 ? "selected" : ""}`}
                      onClick={() => {
                        setSelectedId(track.icao24);
                        setTab("aircraft");
                      }}
                    >
                      <div className="target-card-top">
                        <span
                          className={`target-dot ${track.sourceKind === "simulated" ? "warning" : "ok"}`}
                        />
                        <strong>{track.callsign ?? track.icao24.toUpperCase()}</strong>
                        <span className="target-code">{track.icao24.toUpperCase()}</span>
                      </div>
                      <div className="target-card-data">
                        <span>{track.points.length} OBS</span>
                        <span>
                          {track.latestPosition
                            ? `${track.latestPosition.latitude?.toFixed(2)}, ${track.latestPosition.longitude?.toFixed(2)}`
                            : "NO POSITION"}
                        </span>
                      </div>
                      <div className="target-card-foot">
                        <span>{track.sourceKind.toUpperCase()}</span>
                        <span>{formatTs(track.latest.observed_at).slice(11, 19)}Z</span>
                      </div>
                    </button>
                  ))}
                  {pitchMode &&
                    targetCards.length === 0 &&
                    PITCH_TARGETS.map((target) => (
                      <button
                        key={target.id}
                        className={`target-card ${selectedTrack.icao24 === target.id ? "selected" : ""}`}
                        onClick={() => {
                          setSelectedId(target.id);
                          setTab("aircraft");
                        }}
                      >
                        <div className="target-card-top">
                          <span
                            className={`target-dot ${target.status === "critical" ? "danger" : target.status === "warning" ? "warning" : "ok"}`}
                          />
                          <strong>{target.callsign}</strong>
                          <span className="target-code">{target.id.toUpperCase()}</span>
                        </div>
                        <div className="target-card-data">
                          <span>PITCH SIGNAL</span>
                          <span>
                            {target.lat.toFixed(2)}, {target.lon.toFixed(2)}
                          </span>
                        </div>
                        <div className="target-card-foot">
                          <span>{target.status.toUpperCase()}</span>
                          <span>14:22:08Z</span>
                        </div>
                      </button>
                    ))}
                </div>
              </Panel>
              <Panel title="Corridor posture" kicker="FIR RISK INDEX">
                <div className="risk-list">
                  <div>
                    <span>LCCC / NICOSIA</span>
                    <strong className="danger-text">HIGH · 62%</strong>
                    <div className="meter">
                      <i style={{ width: "62%" }} />
                    </div>
                  </div>
                  <div>
                    <span>OLBA / BEIRUT</span>
                    <strong className="warning-text">ELEVATED · 44%</strong>
                    <div className="meter warning-meter">
                      <i style={{ width: "44%" }} />
                    </div>
                  </div>
                  <div>
                    <span>OSTT / DAMASCUS</span>
                    <strong className="ok-text">NOMINAL · 12%</strong>
                    <div className="meter ok-meter">
                      <i style={{ width: "12%" }} />
                    </div>
                  </div>
                </div>
              </Panel>
            </aside>

            <section className="center-column">
              <div id="trajectory-view">
                <AirspaceView
                  key={globeKey}
                  observations={globeRecording?.observations ?? observations}
                  recording={globeRecording}
                  detections={detections}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  query={query}
                  signalsOnly={targetFilter === "signals"}
                  onOpenImport={() => setTab("import")}
                  onUseStored={() => { setGlobeRecording(null); setSelectedId(null); }}
                  onOpenInvestigation={() => setTab("investigation")}
                />
              </div>
              <Panel title="Telemetry stream" kicker="LOCAL OBSERVATION LOG">
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>UTC</th>
                        <th>CALLSIGN</th>
                        <th>POSITION</th>
                        <th>ALT / SPEED</th>
                        <th>PROVENANCE</th>
                      </tr>
                    </thead>
                    <tbody>
                      {targetPoints
                        .slice(-5)
                        .reverse()
                        .map((point) => (
                          <tr key={point.id}>
                            <td>{formatTs(point.observed_at).slice(11, 19)}Z</td>
                            <td>
                              <strong>{point.callsign ?? point.icao24}</strong>
                            </td>
                            <td>
                              {point.latitude?.toFixed(3) ?? "—"} /{" "}
                              {point.longitude?.toFixed(3) ?? "—"}
                            </td>
                            <td>
                              {point.baro_altitude_m
                                ? `${Math.round(point.baro_altitude_m)}m`
                                : "—"}{" "}
                              · {point.velocity_ms ? `${Math.round(point.velocity_ms)} m/s` : "—"}
                            </td>
                            <td>
                              <span className={`table-status ${point.source_kind}`}>
                                {point.source_kind}
                              </span>
                            </td>
                          </tr>
                        ))}
                      {targetPoints.length === 0 && (
                        <tr>
                          <td colSpan={5} className="empty-table">
                            Load observations to see the telemetry stream.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </Panel>
            </section>

            <aside className="right-column">
              <Panel title="Investigation lab" kicker="ANALYST WORKBENCH">
                <div className="dossier">
                  <div className="dossier-status">
                    <span className="badge danger">
                      {events[0]?.status?.toUpperCase() ?? "CANDIDATE"}
                    </span>
                    <span className="eyebrow">
                      {events[0]
                        ? `EVT-${String(events[0].id).padStart(4, "0")}`
                        : "NO EVENT SELECTED"}
                    </span>
                  </div>
                  <h3>
                    {events[0]?.summary ??
                      (pitchMode
                        ? "Coordinated Mediterranean position discontinuity"
                        : `${selectedTrack.callsign ?? "Target"} telemetry review`)}
                  </h3>
                  <p>
                    {events[0]?.caveat ??
                      (pitchMode
                        ? "Pitch preset: three aircraft share a correlated lateral offset. This is a candidate signal for analyst review, not proof of spoofing."
                        : "Run detection against stored observations to create a candidate event with supporting evidence and explicit limitations.")}
                  </p>
                </div>
                <div className="timeline">
                  <span className="eyebrow">RECENT SIGNALS</span>
                  {detections.slice(0, 4).map((detection) => (
                    <div className="timeline-item" key={detection.id}>
                      <span className={`timeline-dot ${toneForSeverity(detection.severity)}`} />
                      <div>
                        <strong>{detection.rule.replaceAll("_", " ")}</strong>
                        <span>
                          {detection.icao24} · {formatTs(detection.detected_at)}
                        </span>
                      </div>
                    </div>
                  ))}
                  {pitchMode &&
                    detections.length === 0 &&
                    [
                      ["teleport", "EW-409 · 14:22:08Z"],
                      ["common mode offset", "BA-117 · 14:22:08Z"],
                    ].map(([rule, detail]) => (
                      <div className="timeline-item" key={rule}>
                        <span className="timeline-dot danger" />
                        <div>
                          <strong>{rule}</strong>
                          <span>{detail}</span>
                        </div>
                      </div>
                    ))}
                  {!pitchMode && detections.length === 0 && (
                    <div className="empty-copy">
                      No detections yet. The analysis engine is ready when observations are loaded.
                    </div>
                  )}
                </div>
                <div className="lab-metrics">
                  <div>
                    <span>EVENTS</span>
                    <strong>{displayEvents}</strong>
                  </div>
                  <div>
                    <span>OPEN ALERTS</span>
                    <strong>{displayAlerts}</strong>
                  </div>
                  <div>
                    <span>MODELS</span>
                    <strong>
                      {models.filter((model) => model.status === "ready").length}/{models.length}
                    </strong>
                  </div>
                </div>
                <button
                  className="primary-button full-width"
                  onClick={() => void runCommand(postDetect, "Detection run completed.")}
                  disabled={busy}
                >
                  <span className="material-symbols-outlined">science</span>
                  {busy ? "Analyzing…" : "Analyze stored observations"}
                </button>
              </Panel>
              <Panel title="Source ledger" kicker="PROVENANCE">
                <div className="source-list">
                  {sources.map((source) => (
                    <div key={source.name}>
                      <span
                        className={`source-indicator ${source.status === "verified_live" ? "ok" : "warning"}`}
                      />
                      <div>
                        <strong>{source.name}</strong>
                        <span>{source.status.replaceAll("_", " ")}</span>
                      </div>
                    </div>
                  ))}
                  {sources.length === 0 && (
                    <div className="empty-copy">
                      Source status appears when the backend is reachable.
                    </div>
                  )}
                </div>
              </Panel>
            </aside>
          </section>
        )}

        {tab === "investigation" && (
          <section className="investigation-view">
            <Panel title="Investigation queue" kicker="CANDIDATE EVENTS">
              <div className="queue-grid">
                {!pitchMode && events.length === 0 && (
                  <div className="empty-copy">
                    No candidate events are persisted yet. Load demo data and run detection to
                    populate this queue.
                  </div>
                )}
                {pitchMode && events.length === 0 && (
                  <article className="event-card">
                    <div className="event-card-top">
                      <span className="badge danger">HIGH</span>
                      <span className="eyebrow">EVT-PITCH-01</span>
                    </div>
                    <h3>Coordinated position discontinuity</h3>
                    <p>
                      Three aircraft present a shared lateral offset in the Mediterranean corridor
                      during the same observation window.
                    </p>
                    <div className="event-card-foot">
                      <span>3 aircraft</span>
                      <span>14 supporting detections</span>
                      <span>candidate</span>
                    </div>
                  </article>
                )}
                {events.map((event) => (
                  <article className="event-card" key={event.id}>
                    <div className="event-card-top">
                      <span className={`badge ${toneForSeverity(event.severity)}`}>
                        {event.severity.toUpperCase()}
                      </span>
                      <span className="eyebrow">EVT-{String(event.id).padStart(4, "0")}</span>
                    </div>
                    <h3>{event.summary}</h3>
                    <p>{event.caveat ?? "Candidate event generated from observed telemetry."}</p>
                    <div className="event-card-foot">
                      <span>{event.aircraft.length} aircraft</span>
                      <span>{event.member_anomaly_ids.length} supporting detections</span>
                      <span>{event.status}</span>
                    </div>
                    <button
                      className="action-button full-width"
                      onClick={() => setReplayEventId(event.id)}
                    >
                      <span className="material-symbols-outlined">play_circle</span>
                      Replay this event
                    </button>
                  </article>
                ))}
              </div>
            </Panel>
            <Panel title="Model readiness" kicker="OPTIONAL SCORING LAYER">
              <div className="model-list">
                {models.map((model) => (
                  <div key={model.name}>
                    <div>
                      <strong>{model.name}</strong>
                      <span>{model.semantics}</span>
                    </div>
                    <span className={`badge ${model.status === "ready" ? "ok" : "neutral"}`}>
                      {model.status}
                    </span>
                  </div>
                ))}
              </div>
            </Panel>
            <div className="replay-panel-wide">
              <IncidentReplayPanel
                event={replayEvent}
                detections={detections}
                observations={observations}
                pitchMode={pitchMode}
              />
            </div>
          </section>
        )}

        {tab === "alerts" && (
          <section className="alerts-view">
            <Panel title="Alert operations" kicker="PERSISTED ALERTS">
              <div className="alert-summary">
                <span>
                  <strong>{displayAlerts}</strong> open alerts
                </span>
                <span>{pitchMode ? "Pitch snapshot" : `${alerts.length} loaded from backend`}</span>
              </div>
              <div className="alert-list">
                {!pitchMode && alerts.length === 0 && (
                  <div className="empty-copy">
                    No persisted alerts are available. Load demo data and run detection to generate
                    alert candidates.
                  </div>
                )}
                {(pitchMode
                  ? [
                      {
                        id: 1,
                        severity: "high",
                        title: "Coordinated position discontinuity",
                        summary:
                          "Three aircraft show a correlated lateral offset in the Mediterranean corridor.",
                        category: "kinematic",
                      },
                    ]
                  : alerts
                ).map((alert) => (
                  <article className="alert-card" key={alert.id}>
                    <div>
                      <span className={`badge ${toneForSeverity(alert.severity)}`}>
                        {alert.severity.toUpperCase()}
                      </span>
                      <span className="eyebrow">{alert.category}</span>
                    </div>
                    <h3>{alert.title}</h3>
                    <p>{alert.summary}</p>
                    <button
                      className="action-button"
                      onClick={() => {
                        setTab("investigation");
                        setMessage(`Opened investigation context for alert ${alert.id}.`);
                      }}
                    >
                      Open investigation
                    </button>
                  </article>
                ))}
              </div>
            </Panel>
          </section>
        )}

        {tab === "import" && (
          <CsvImportPanel
            onViewOnGlobe={(rows, name, saved) => {
              setGlobeRecording({ observations: rows, name, saved });
              setPitchMode(false);
              setTargetFilter("all");
              setQuery("");
              setSelectedId(null);
              setTab("overview");
              window.setTimeout(() => document.getElementById("trajectory-view")?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
            }}
            onImported={async () => {
              await refresh();
              setMessage("CSV import completed and the command feed was refreshed.");
            }}
          />
        )}
      </main>
      <footer className="footer-bar">
        <span>ORBITAL GRID HUD v4.28</span>
        <span>UTC // LOCAL PROTOTYPE</span>
        <span className="ok-text">GEO-TELEMETRY NOMINAL</span>
        <span>AEROTRACE SYSTEMS © 2026</span>
      </footer>
    </div>
  );
}
