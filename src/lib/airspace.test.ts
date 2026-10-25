import { describe, expect, it } from "vitest";
import type { Detection, Observation } from "./api";
import { findingsAt, frontHemisphere, recordingCenter } from "./airspace";

const row = (lon: number | null, lat = 0, time = 10) => ({ icao24: "abcdef", observed_at: time, latitude: lat, longitude: lon }) as Observation;
describe("airspace geography and evidence", () => {
  it("keeps dateline crossings centred on the Pacific", () => {
    const center = recordingCenter([row(179), row(-179)]);
    expect(Math.abs(center[0])).toBeCloseTo(180);
    expect(center[1]).toBeCloseTo(0);
  });
  it("hides the far side of the sphere, including points projected onto the near side", () => {
    expect(frontHemisphere([0, 0], row(0))).toBe(true);
    expect(frontHemisphere([0, 0], row(180))).toBe(false);
    expect(frontHemisphere([0, 0], row(null))).toBe(false);
  });
  it("matches findings to recording timestamps and never reveals future evidence", () => {
    const detections = [
      { icao24: "abcdef", detected_at: 10 },
      { icao24: "abcdef", detected_at: 20 },
      { icao24: "ffffff", detected_at: 10 },
    ] as Detection[];
    expect(findingsAt(detections, [row(0)], 9)).toEqual([]);
    expect(findingsAt(detections, [row(0)], 10)).toEqual([detections[0]]);
  });
});
