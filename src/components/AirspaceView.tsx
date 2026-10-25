import { useEffect, useId, useMemo, useRef, useState } from "react";
import { geoEquirectangular, geoGraticule10, geoOrthographic, geoPath, type GeoPermissibleObjects } from "d3-geo";
import type { Detection, Observation } from "../lib/api";
import { groupTracks, formatTs } from "../lib/explorer";
import { findingsAt, frontHemisphere, positioned, recordingCenter, severityColor } from "../lib/airspace";

type Recording = { name: string; saved: boolean };
type Props = {
  observations: Observation[];
  detections: Detection[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onOpenInvestigation: () => void;
  onOpenImport: () => void;
  onUseStored: () => void;
  query: string;
  signalsOnly: boolean;
  recording: Recording | null;
};

export default function AirspaceView({ observations, detections, selectedId, onSelect,
  onOpenInvestigation, onOpenImport, onUseStored, query, signalsOnly, recording }: Props) {
  const ordered = useMemo(() => [...observations].sort((a, b) => a.observed_at - b.observed_at || a.id - b.id), [observations]);
  const start = ordered[0]?.observed_at ?? 0;
  const end = ordered.at(-1)?.observed_at ?? start;
  const [time, setTime] = useState(end);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(10);
  const [view, setView] = useState<"globe" | "map">("globe");
  const [center, setCenter] = useState<[number, number]>(() => recordingCenter(ordered));
  const [zoom, setZoom] = useState(1);
  const [trails, setTrails] = useState(true);
  const [grid, setGrid] = useState(true);
  const [land, setLand] = useState<GeoPermissibleObjects | null>(null);
  const [landFailed, setLandFailed] = useState(false);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const drag = useRef<{ x: number; y: number; center: [number, number] } | null>(null);
  const id = useId().replace(/:/g, "");

  useEffect(() => {
    setTime(end); setPlaying(false); setCenter(recordingCenter(ordered)); setZoom(1);
  }, [ordered, end]);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/maps/land-110m.geojson", { signal: controller.signal })
      .then(response => { if (!response.ok) throw new Error("Land outline unavailable"); return response.json(); })
      .then(data => { if (data?.type !== "FeatureCollection" || !Array.isArray(data.features)) throw new Error("Invalid land geometry"); setLand(data as GeoPermissibleObjects); })
      .catch(error => { if (error.name !== "AbortError") setLandFailed(true); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!playing) return;
    let previous = performance.now();
    const timer = window.setInterval(() => {
      const now = performance.now();
      const seconds = (now - previous) / 1000 * speed;
      previous = now;
      setTime(current => Math.min(end, current + seconds));
    }, 200);
    return () => window.clearInterval(timer);
  }, [playing, speed, end]);
  useEffect(() => { if (time >= end) setPlaying(false); }, [time, end]);

  const revealed = useMemo(() => ordered.filter(row => row.observed_at <= time), [ordered, time]);
  const tracks = useMemo(() => groupTracks(revealed), [revealed]);
  const findings = useMemo(() => findingsAt(detections, ordered, time), [detections, ordered, time]);
  const severity = useMemo(() => {
    const levels: Record<string, string> = {};
    const weight: Record<string, number> = { low: 1, medium: 2, high: 3 };
    for (const finding of findings) {
      if ((weight[finding.severity] ?? 0) > (weight[levels[finding.icao24] ?? ""] ?? 0)) levels[finding.icao24] = finding.severity;
    }
    return levels;
  }, [findings]);
  const filtered = tracks.filter(track => (!signalsOnly || severity[track.icao24]) &&
    `${track.icao24} ${track.callsign ?? ""}`.toLowerCase().includes(query.trim().toLowerCase()));
  const selected = tracks.find(track => track.icao24 === selectedId) ?? filtered[0];
  const positionedTracks = filtered.filter(track => track.latestPosition);
  const projection = useMemo(() => view === "globe"
    ? geoOrthographic().translate([400, 220]).scale(190 * zoom).rotate([-center[0], -center[1]]).clipAngle(90)
    : geoEquirectangular().translate([400, 220]).scale(118 * zoom).rotate([-center[0], 0]).center([0, center[1]]), [view, zoom, center]);
  const path = geoPath(projection);
  const graticule = useMemo(() => geoGraticule10(), []);
  const inView = (row: Observation) => {
    if (!positioned(row) || (view === "globe" && !frontHemisphere(center, row))) return false;
    const p = projection([row.longitude!, row.latitude!]);
    return p !== null && p[0] >= 5 && p[0] <= 795 && p[1] >= 5 && p[1] <= 435;
  };
  const visibleCount = positionedTracks.filter(track => inView(track.latestPosition!)).length;
  const simulated = ordered.some(row => row.source_kind === "simulated");
  const missing = tracks.filter(track => !positioned(track.latest)).length;

  const focus = () => {
    if (selected?.latestPosition) {
      setCenter([selected.latestPosition.longitude!, selected.latestPosition.latitude!]);
      setZoom(3.5);
    }
  };

  return (
    <section className="hud-panel airspace-panel" aria-label="Aircraft globe and recording playback">
      <div className="panel-heading">
        <div><span className="eyebrow">OBSERVED AIRCRAFT POSITIONS</span><h2>Aircraft globe &amp; playback</h2></div>
        <span className="airspace-mode">{recording && !recording.saved ? "CSV PREVIEW" : simulated ? "SIMULATED / RECORDED" : "RECORDED OBSERVATIONS"}</span>
      </div>
      <p className="airspace-context">{recording ? recording.name : "Stored observations (up to 1,000 loaded rows)"}</p>
      {recording && !recording.saved && <p className="airspace-notice">Preview only. Return to CSV Import and import valid rows before running detection.</p>}
      <div className="airspace-toolbar">
        <div className="airspace-view-switch" aria-label="Airspace projection">
          <button className={view === "globe" ? "active" : ""} aria-pressed={view === "globe"} onClick={() => { setView("globe"); setZoom(1); }}>3D Globe</button>
          <button className={view === "map" ? "active" : ""} aria-pressed={view === "map"} onClick={() => { setView("map"); setZoom(1); }}>World map</button>
        </div>
        <button className="action-button" aria-label="Zoom in" onClick={() => setZoom(value => Math.min(6, value * 1.4))}>+</button>
        <button className="action-button" aria-label="Zoom out" onClick={() => setZoom(value => Math.max(.7, value / 1.4))}>−</button>
        <button className="action-button" onClick={focus} disabled={!selected?.latestPosition}>Focus aircraft</button>
        <button className="action-button" onClick={() => { setCenter(recordingCenter(ordered)); setZoom(1); }}>Reset view</button>
      </div>
      <div className="airspace-layers">
        <label><input type="checkbox" checked={trails} onChange={event => setTrails(event.target.checked)} />Recorded trails</label>
        <label><input type="checkbox" checked={grid} onChange={event => setGrid(event.target.checked)} />Coordinate grid</label>
        <span><i style={{ background: "#67dfd5" }} />No finding</span>
        <span><i style={{ background: "#8fb9ff" }} />Quality / low</span>
        <span><i style={{ background: "#ffd27d" }} />Medium</span>
        <span><i style={{ background: "#ff7d86" }} />High</span>
      </div>
      <div className="airspace-canvas">
        <svg ref={svgRef} viewBox="0 0 800 440" aria-label="Aircraft positions on an interactive globe" role="group"
          onPointerDown={event => {
            if (event.button !== 0 || (event.target as Element).closest('[data-aircraft]')) return;
            drag.current = { x: event.clientX, y: event.clientY, center };
            svgRef.current?.setPointerCapture?.(event.pointerId);
          }}
          onPointerMove={event => {
            const previous = drag.current;
            if (!previous) return;
            const width = svgRef.current?.getBoundingClientRect().width || 800;
            const factor = 180 / width / zoom;
            setCenter([((previous.center[0] - (event.clientX - previous.x) * factor + 540) % 360) - 180,
              Math.max(-85, Math.min(85, previous.center[1] + (event.clientY - previous.y) * factor))]);
          }}
          onPointerUp={event => { drag.current = null; if(svgRef.current?.hasPointerCapture?.(event.pointerId)) svgRef.current.releasePointerCapture(event.pointerId); }}
          onPointerCancel={() => { drag.current = null; }}>
          <title>Recorded aircraft locations. Drag to rotate; select a marker to inspect an aircraft.</title>
          <defs>
            <radialGradient id={`ocean-${id}`} cx="35%" cy="25%"><stop offset="0" stopColor="#4e5054" /><stop offset=".8" stopColor="#2b2d31" /><stop offset="1" stopColor="#1a1c20" /></radialGradient>
            <clipPath id={`viewport-${id}`}><rect x="0" y="0" width="800" height="440" /></clipPath>
          </defs>
          <g clipPath={`url(#viewport-${id})`}>
            <path d={path({ type: "Sphere" }) ?? ""} fill={`url(#ocean-${id})`} stroke="#96989c" strokeWidth="1.2" />
            {land && <path data-testid="globe-land" d={path(land) ?? ""} fill="#4c4e52" stroke="#a0a2a6" strokeWidth=".5" />}
            {grid && <path d={path(graticule) ?? ""} fill="none" stroke="#adafb3" strokeWidth=".45" opacity=".27" />}
            {trails && positionedTracks.map(track => {
              const points = track.points.filter(positioned).map(row => [row.longitude!, row.latitude!]);
              if (points.length < 2) return null;
              return <path key={track.icao24} data-testid={`trail-${track.icao24}`} d={path({ type: "LineString", coordinates: points }) ?? ""} fill="none" stroke={severityColor(severity[track.icao24])} strokeWidth={track.icao24 === selected?.icao24 ? 2 : 1} opacity=".7" />;
            })}
            {[...positionedTracks].sort((a, b) => Number(a.icao24 === selected?.icao24) - Number(b.icao24 === selected?.icao24)).map(track => {
              const last = track.latestPosition!;
              if (!inView(last)) return null;
              const [x, y] = projection([last.longitude!, last.latitude!])!;
              const active = track.icao24 === selected?.icao24;
              const color = severityColor(severity[track.icao24]);
              return <g key={track.icao24} data-aircraft={track.icao24} data-testid={`globe-aircraft-${track.icao24}`} data-latitude={last.latitude} data-longitude={last.longitude} data-severity={severity[track.icao24] ?? "none"}
                transform={`translate(${x},${y})`} className="airspace-aircraft" role="button" tabIndex={0} aria-pressed={active}
                aria-label={`Select ${track.callsign ?? track.icao24}`}
                onClick={() => onSelect(track.icao24)}
                onKeyDown={event => { if(event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(track.icao24); } }}>
                <title>{track.callsign ?? track.icao24} · last position {last.latitude!.toFixed(4)}, {last.longitude!.toFixed(4)} · {formatTs(last.observed_at)}</title>
                <circle r="12" fill="transparent" />
                {active && <circle r="10" fill="none" stroke="#fafcff" strokeWidth="1" />}
                <circle r="6" fill={color} fillOpacity=".14" stroke={color} strokeWidth="1" strokeDasharray={positioned(track.latest) ? undefined : "2 2"} />
                <path d="M0,-6 L4,5 L0,3 L-4,5 Z" fill={color} transform={`rotate(${(last.heading_deg ?? 0)})`} />
                {active && <text x="15" y="-10" fill="#fafcff" fontSize="11" paintOrder="stroke" stroke="#1c1e22" strokeWidth="3">{track.callsign ?? track.icao24}</text>}
              </g>;
            })}
          </g>
          {!ordered.length && <text x="400" y="220" textAnchor="middle" fill="#f2f4f8" fontSize="16">Import a CSV to plot aircraft</text>}
        </svg>
        <div className="airspace-map-readout"><span>{visibleCount} / {positionedTracks.length} positioned aircraft in view</span><span>Drag to rotate · + / − to zoom</span></div>
      </div>
      {landFailed && <p className="airspace-notice">Land outline unavailable. Coordinate grid and aircraft positions are still shown.</p>}
      <div className="airspace-clock">
        <button className="primary-button" disabled={!ordered.length || start === end} onClick={() => { if (!playing && time >= end) setTime(start); setPlaying(value => !value); }}>{playing ? "Pause" : time >= end && end > start ? "Play recording" : "Play"}</button>
        <button className="action-button" disabled={!ordered.length} onClick={() => { setPlaying(false); setTime(end); }}>Latest</button>
        <label>Speed<select aria-label="Globe playback speed" value={speed} onChange={event => setSpeed(Number(event.target.value))}>{[1,10,60,300].map(value => <option value={value} key={value}>{value}×</option>)}</select></label>
        <time>{ordered.length ? formatTs(time) : "No recording loaded"}</time>
        <input type="range" aria-label="Globe playback timeline" min={start} max={end > start ? end : start + 1} step="0.1" value={time} disabled={start === end} onChange={event => { setPlaying(false); setTime(Number(event.target.value)); }} />
      </div>
      <div className="airspace-counts"><span>{revealed.length} / {ordered.length} reports</span><span>{findings.length} stored findings up to this time</span><span>{missing} aircraft currently lack a position</span></div>
      <p className="airspace-note">Recorded playback, not a live feed. Markers use reported positions only. Trails connect observations; they do not confirm a flown route or GNSS interference. Missing reports keep the last known marker.</p>
      <label className="airspace-aircraft-select">Aircraft
        <select aria-label="Select globe aircraft" value={selected?.icao24 ?? ""} disabled={!filtered.length} onChange={event => {
          onSelect(event.target.value);
          const point = filtered.find(track => track.icao24 === event.target.value)?.latestPosition;
          if (point) setCenter([point.longitude!, point.latitude!]);
        }}>
          {!filtered.length && <option value="">No matching aircraft</option>}
          {filtered.map(track => <option value={track.icao24} key={track.icao24}>{track.callsign ?? track.icao24} · {track.icao24.toUpperCase()}{severity[track.icao24] ? ` · ${severity[track.icao24]}` : ""}</option>)}
        </select>
      </label>
      <div className="airspace-selection" aria-live="polite">
        {selected ? <><strong>{selected.callsign ?? selected.icao24.toUpperCase()}</strong><span>{selected.icao24.toUpperCase()} · {selected.sourceKind}</span><span>{selected.latestPosition ? `Last position: ${selected.latestPosition.latitude!.toFixed(4)}°, ${selected.latestPosition.longitude!.toFixed(4)}°` : "Position unavailable"}</span><span>Report: {formatTs(selected.latest.observed_at)}</span><span>Altitude: {selected.latest.baro_altitude_m === null ? "—" : `${Math.round(selected.latest.baro_altitude_m)} m`} · Speed: {selected.latest.velocity_ms === null ? "—" : `${selected.latest.velocity_ms.toFixed(1)} m/s`}</span><span>{severity[selected.icao24] ? `${severity[selected.icao24]}-severity finding` : "No stored finding at this time"}</span></> : <span>No aircraft match this filter at the current playback time.</span>}
      </div>
      <div className="airspace-foot-actions">
        <button className="action-button" onClick={onOpenImport}>Open CSV Import</button>
        {recording && <button className="action-button" onClick={onUseStored}>Use stored feed</button>}
        <button className="primary-button" onClick={onOpenInvestigation} disabled={Boolean(recording && !recording.saved)}>Open investigation lab</button>
      </div>
    </section>
  );
}
