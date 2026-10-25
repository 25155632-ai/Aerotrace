import type { Observation } from "./api";

/** Keep recorded timestamps: never interpolate or extrapolate aircraft positions. */
export function observationsAt(rows: Observation[], time: number): Observation[] {
  return rows.filter((row) => row.observed_at <= time);
}

/** Fit the entire recording so the viewport remains stable during playback.
 * Remove the largest empty longitude arc to handle dateline crossings. */
export function recordingProjection(rows: Observation[]) {
  const positioned = rows.filter((row) => row.latitude !== null && row.longitude !== null);
  if (!positioned.length) return null;
  const longitudes = positioned.map((row) => ((row.longitude! % 360) + 360) % 360).sort((a, b) => a - b);
  let largest = -1;
  let origin = longitudes[0]!;
  for (let i = 0; i < longitudes.length; i++) {
    const next = longitudes[(i + 1) % longitudes.length]!;
    const gap = (i === longitudes.length - 1 ? next + 360 : next) - longitudes[i]!;
    if (gap > largest) { largest = gap; origin = next; }
  }
  const unwrap = (lon: number) => (((lon % 360) + 360) % 360 - origin + 360) % 360;
  let minLat = 90, maxLat = -90, maxLon = 0;
  for (const row of positioned) {
    minLat = Math.min(minLat, row.latitude!);
    maxLat = Math.max(maxLat, row.latitude!);
    maxLon = Math.max(maxLon, unwrap(row.longitude!));
  }
  const lonSpan = Math.max(0.1, maxLon);
  const latSpan = Math.max(0.1, maxLat - minLat);
  return (latitude: number, longitude: number) => ({
    x: 50 + ((unwrap(longitude) - maxLon / 2) / lonSpan) * 80,
    y: 30 - ((latitude - (minLat + maxLat) / 2) / latSpan) * 44,
  });
}
