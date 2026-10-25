/** Pure explorer logic: grouping, searching, freshness, formatting.
 *  Kept free of React and Leaflet so it is unit-testable in isolation,
 *  and so the map can never show anything the backend did not return:
 *  every track here is built from fetched observations only. */

import type { Observation } from "./api";

export interface Track {
  icao24: string;
  callsign: string | null;
  source: string;
  sourceKind: "real" | "simulated";
  /** All points, time-ordered (backend order preserved). */
  points: Observation[];
  /** Last point by time, whether or not it has coordinates. */
  latest: Observation;
  /** Last point with coordinates, for the map marker. Null when the
   *  aircraft has no recorded position at all. */
  latestPosition: Observation | null;
}

/** Group fetched observations into per-aircraft tracks. */
export function groupTracks(observations: Observation[]): Track[] {
  const byAircraft = new Map<string, Observation[]>();
  for (const observation of observations) {
    const points = byAircraft.get(observation.icao24) ?? [];
    points.push(observation);
    byAircraft.set(observation.icao24, points);
  }
  const tracks: Track[] = [];
  for (const [icao24, points] of byAircraft) {
    const sorted = [...points].sort((a, b) => a.observed_at - b.observed_at || a.id - b.id);
    const latest = sorted[sorted.length - 1]!;
    const withPosition = sorted.filter(
      (point) => point.latitude !== null && point.longitude !== null,
    );
    const callsign = [...sorted].reverse().find((point) => point.callsign)?.callsign ?? null;
    tracks.push({
      icao24,
      callsign,
      source: latest.source,
      sourceKind: latest.source_kind,
      points: sorted,
      latest,
      latestPosition: withPosition[withPosition.length - 1] ?? null,
    });
  }
  tracks.sort((a, b) => a.icao24.localeCompare(b.icao24));
  return tracks;
}

/** Client-side search over icao24 and callsign. Empty query matches all. */
export function filterTracksBySearch(tracks: Track[], query: string): Track[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return tracks;
  return tracks.filter(
    (track) =>
      track.icao24.toLowerCase().includes(needle) ||
      (track.callsign ?? "").toLowerCase().includes(needle),
  );
}

/** Format an epoch second as "YYYY-MM-DD HH:MM:SS UTC". */
export function formatTs(epochSeconds: number): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(new Date(epochSeconds * 1000));
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")}:${get("second")} UTC`;
}

/** Human age of a timestamp relative to now (both epoch seconds). */
export function formatAge(epochSeconds: number, nowSeconds: number): string {
  const age = nowSeconds - epochSeconds;
  if (age < 0) return "timestamp is in the future";
  if (age < 60) return `${Math.floor(age)} seconds ago`;
  if (age < 3600) return `${Math.floor(age / 60)} minutes ago`;
  if (age < 86400) return `${Math.floor(age / 3600)} hours ago`;
  return `${Math.floor(age / 86400)} days ago`;
}

export interface Freshness {
  label: string;
  detail: string;
  tone: "info" | "warning";
}

/**
 * Freshness for the *displayed* tracks. Simulated data is never
 * described as live, however recent its timestamps look; real data
 * older than an hour is flagged stale rather than shown as current.
 */
export function freshnessFor(tracks: Track[], nowSeconds: number): Freshness | null {
  if (tracks.length === 0) return null;
  const latest = Math.max(...tracks.map((track) => track.latest.observed_at));
  const latestText = formatTs(latest);
  const hasSimulated = tracks.some((track) => track.sourceKind === "simulated");
  const allSimulated = tracks.every((track) => track.sourceKind === "simulated");
  if (allSimulated) {
    return {
      label: "Demonstration data — not live",
      detail: `Latest observation ${latestText}. These positions come from a labelled demonstration dataset, not from current flights.`,
      tone: "info",
    };
  }
  if (hasSimulated) {
    return {
      label: "Mixed real and simulated data",
      detail: `Latest observation ${latestText}. Simulated observations in this view are not live.`,
      tone: "info",
    };
  }
  const age = nowSeconds - latest;
  if (age <= 3600) {
    return {
      label: "Recent",
      detail: `Latest observation ${latestText} (${formatAge(latest, nowSeconds)}).`,
      tone: "info",
    };
  }
  return {
    label: "Stale",
    detail: `Latest observation ${latestText} (${formatAge(latest, nowSeconds)}). Positions may no longer reflect current flights.`,
    tone: "warning",
  };
}

/** Parse a datetime-local input value to epoch seconds, or undefined. */
/** Inverse of datetimeLocalToEpoch, in the browser's local timezone
 * (datetime-local inputs are local, never UTC). Seconds are dropped:
 * the input has minute precision. */
export function epochToDatetimeLocal(epochSeconds: number): string {
  const date = new Date(epochSeconds * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

export function datetimeLocalToEpoch(value: string): number | undefined {
  if (!value) return undefined;
  const millis = new Date(value).getTime();
  return Number.isNaN(millis) ? undefined : Math.floor(millis / 1000);
}
