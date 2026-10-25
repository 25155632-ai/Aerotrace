import { useEffect, useMemo, useState } from "react";
import type { Observation } from "../lib/api";
import { formatTs, groupTracks } from "../lib/explorer";
import { observationsAt, recordingProjection } from "../lib/csv-tracking";

export default function CsvTracking({ observations }: { observations: Observation[] }) {
  const ordered = useMemo(() => [...observations].sort((a, b) => a.observed_at - b.observed_at || a.id - b.id), [observations]);
  const start = ordered[0]?.observed_at ?? 0;
  const end = ordered.at(-1)?.observed_at ?? start;
  const [time, setTime] = useState(start);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const project = useMemo(() => recordingProjection(ordered), [ordered]);
  const visible = useMemo(() => observationsAt(ordered, time), [ordered, time]);
  const tracks = useMemo(() => groupTracks(visible), [visible]);
  const allTracks = useMemo(() => groupTracks(ordered), [ordered]);
  const snapshot = allTracks.every((track) => new Set(track.points.map((point) => point.observed_at)).size < 2);
  const filtered = tracks.filter((track) => `${track.icao24} ${track.callsign ?? ""}`.toLowerCase().includes(query.toLowerCase()));
  const active = tracks.find((track) => track.icao24 === selected) ?? filtered[0];

  useEffect(() => {
    setTime(start);
    setPlaying(false);
    setSelected(null);
  }, [observations, start]);

  useEffect(() => {
    if (!playing) return;
    let previous = performance.now();
    const interval = window.setInterval(() => {
      const now = performance.now();
      const elapsed = ((now - previous) / 1000) * speed;
      previous = now;
      setTime((value) => Math.min(end, value + elapsed));
    }, 100);
    return () => window.clearInterval(interval);
  }, [playing, speed, end]);

  useEffect(() => { if (time >= end) setPlaying(false); }, [time, end]);

  const toggle = () => {
    if (!playing && time >= end) setTime(start);
    setPlaying((value) => !value);
  };

  return (
    <section className="hud-panel csv-tracking" aria-label="CSV aircraft tracking">
      <div className="panel-heading">
        <div><span className="eyebrow">RECORDED TELEMETRY</span><h2>CSV aircraft tracking</h2></div>
        <span className="csv-replay-badge">{playing ? "REPLAY RUNNING" : "CSV REPLAY"}</span>
      </div>
      <p className="import-help">Positions update at their recorded timestamps. This recording is not a live flight feed; no movement is predicted between samples.</p>
      {snapshot && <p className="csv-snapshot">Single-position snapshot per aircraft. Upload repeated observations of the same ICAO24 at different times to show movement.</p>}
      <div className="csv-tracking-stats">
        <span><strong>{tracks.length} / {allTracks.length}</strong> aircraft visible</span>
        <span><strong>{visible.length} / {ordered.length}</strong> observations</span>
        <span><strong>{tracks.filter((track) => track.latestPosition).length}</strong> positioned</span>
        <time>{formatTs(time)}</time>
      </div>
      <div className="csv-playback-controls">
        <button className="primary-button" disabled={end === start} onClick={toggle}>{playing ? "Pause" : time >= end && end > start ? "Replay" : "Play"}</button>
        <button className="action-button" onClick={() => { setPlaying(false); setTime(start); }}>Reset</button>
        <label>Speed <select aria-label="CSV playback speed" value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>
          {[1, 10, 60, 300].map((value) => <option key={value} value={value}>{value}×</option>)}
        </select></label>
        <input className="csv-timeline" type="range" aria-label="CSV playback timeline" min={start} max={end || start + 1} step="0.1" value={time} disabled={end === start} onChange={(event) => { setPlaying(false); setTime(Number(event.target.value)); }} />
      </div>
      <div className="csv-track-map">
        {project ? <svg viewBox="0 0 100 60" role="img" aria-label="Recorded aircraft positions and track trails, fitted to CSV coordinates">
          <title>CSV coordinate plot — recorded positions only</title>
          <rect width="100" height="60" fill="#091428" />
          {[10,20,30,40,50,60,70,80,90].map((x) => <line key={`x${x}`} x1={x} y1="0" x2={x} y2="60" stroke="#20364f" strokeWidth="0.15" />)}
          {[10,20,30,40,50].map((y) => <line key={`y${y}`} x1="0" y1={y} x2="100" y2={y} stroke="#20364f" strokeWidth="0.15" />)}
          {filtered.map((track) => {
            const point = track.latestPosition;
            if (!point) return null;
            const position = project(point.latitude!, point.longitude!);
            const path = track.points.filter((row) => row.latitude !== null && row.longitude !== null).map((row) => { const p = project(row.latitude!, row.longitude!); return `${p.x},${p.y}`; }).join(" ");
            const isActive = track.icao24 === active?.icao24;
            return <g key={track.icao24}>
              <polyline points={path} fill="none" stroke={isActive ? "#f6c568" : "#48bede"} strokeWidth={isActive ? 0.4 : 0.2} opacity="0.8" />
              <g role="button" tabIndex={0} aria-label={`Select aircraft ${track.callsign ?? track.icao24}`} onClick={() => setSelected(track.icao24)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelected(track.icao24); } }} className="csv-marker">
                <title>{track.callsign ?? track.icao24} · {point.latitude}, {point.longitude} · {formatTs(point.observed_at)}</title>
                <circle cx={position.x} cy={position.y} r="1.5" fill="transparent" />
                <circle cx={position.x} cy={position.y} r={isActive ? 0.7 : 0.45} fill={isActive ? "#f6c568" : "#60d8ef"} />
                {isActive && <text x={position.x + 1} y={position.y - 1} fill="#f6c568" fontSize="1.8">{track.callsign ?? track.icao24}</text>}
              </g>
            </g>;
          })}
        </svg> : <p>No valid coordinates in this recording. Unpositioned aircraft remain listed below.</p>}
        <span className="csv-map-caption">COORDINATE PLOT · VIEW FITS RECORDING · TRAILS SHOW OBSERVED POINTS</span>
      </div>
      {active && <div className="csv-track-detail">
        <strong>{active.callsign ?? active.icao24} · {active.icao24.toUpperCase()}</strong>
        <span>Last report: {formatTs(active.latest.observed_at)}</span>
        <span>Position: {active.latestPosition ? `${active.latestPosition.latitude!.toFixed(5)}°, ${active.latestPosition.longitude!.toFixed(5)}°` : "Unavailable"}</span>
        <span>Altitude: {active.latest.baro_altitude_m === null ? "Unavailable" : `${Math.round(active.latest.baro_altitude_m)} m`}</span>
        <span>Speed: {active.latest.velocity_ms === null ? "Unavailable" : `${active.latest.velocity_ms.toFixed(1)} m/s`}</span>
        <span>{active.sourceKind} · uploaded CSV · {Math.max(0, Math.floor(time - active.latest.observed_at))} s since report</span>
      </div>}
      <label className="csv-search-label">Find aircraft<input type="search" placeholder="Callsign or ICAO24" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <div className="csv-track-list" aria-label="Aircraft in recording">
        {filtered.map((track) => <button key={track.icao24} className={`action-button ${track.icao24 === active?.icao24 ? "csv-selected" : ""}`} aria-pressed={track.icao24 === active?.icao24} onClick={() => setSelected(track.icao24)}>
          <strong>{track.callsign ?? track.icao24}</strong><span>{track.icao24.toUpperCase()} · {track.points.length} reports{!track.latestPosition ? " · no position" : ""}</span>
        </button>)}
        {!filtered.length && <p>No aircraft match at this playback time.</p>}
      </div>
    </section>
  );
}
