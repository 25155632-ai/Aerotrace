import { geoDistance } from "d3-geo";
import type { Detection, Observation } from "./api";

export function positioned(row: Observation): boolean {
  return row.latitude !== null && row.longitude !== null &&
    Number.isFinite(row.latitude) && Number.isFinite(row.longitude);
}

/** A spherical mean keeps recordings at +/-180 degrees near the dateline. */
export function recordingCenter(rows: Observation[]): [number, number] {
  let x = 0, y = 0, z = 0, count = 0;
  for (const row of rows) {
    if (!positioned(row)) continue;
    const lat = row.latitude! * Math.PI / 180;
    const lon = row.longitude! * Math.PI / 180;
    x += Math.cos(lat) * Math.cos(lon);
    y += Math.cos(lat) * Math.sin(lon);
    z += Math.sin(lat);
    count++;
  }
  if (!count) return [0, 20];
  if (Math.hypot(x, y, z) < 1e-8) {
    const first = rows.find(positioned)!;
    return [first.longitude!, first.latitude!];
  }
  return [Math.atan2(y, x) * 180 / Math.PI, Math.atan2(z, Math.hypot(x, y)) * 180 / Math.PI];
}

export function frontHemisphere(center: [number, number], row: Observation): boolean {
  return positioned(row) && geoDistance(center, [row.longitude!, row.latitude!]) < Math.PI / 2 - 0.001;
}

/** Stored findings only. No alarm is inferred from a drawn line or future point. */
export function findingsAt(detections: Detection[], rows: Observation[], time: number): Detection[] {
  const keys = new Set(rows.filter(row => row.observed_at <= time).map(row => `${row.icao24}:${row.observed_at}`));
  return detections.filter(detection => detection.detected_at <= time &&
    keys.has(`${detection.icao24}:${detection.detected_at}`));
}

export function severityColor(severity?: string): string {
  return severity === "high" ? "#ff7d86" : severity === "medium" ? "#ffd27d" : severity === "low" ? "#8fb9ff" : "#67dfd5";
}
