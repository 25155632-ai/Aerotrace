import { describe, expect, it } from "vitest";
import type { Observation } from "./api";
import { observationsAt, recordingProjection } from "./csv-tracking";

const point = (time: number, lat: number | null, lon: number | null) => ({ observed_at: time, latitude: lat, longitude: lon }) as Observation;

describe("CSV tracking", () => {
  it("reveals only observations at or before the playback clock", () => {
    const rows = [point(20, 2, 2), point(10, 1, 1), point(30, 3, 3)];
    expect(observationsAt(rows, 19)).toEqual([rows[1]]);
    expect(observationsAt(rows, 20)).toEqual([rows[0], rows[1]]);
    expect(observationsAt(rows, 0)).toEqual([]);
  });
  it("fits zero coordinates and a single-position snapshot", () => {
    const project = recordingProjection([point(10, 0, 0)])!;
    expect(project(0, 0)).toEqual({ x: 50, y: 30 });
    expect(recordingProjection([point(10, null, 0)])).toBeNull();
  });
  it("keeps dateline crossings on the short longitude arc", () => {
    const project = recordingProjection([point(10, 0, 179), point(20, 0, -179)])!;
    expect(project(0, 180).x).toBeCloseTo(50);
    expect(project(0, 179).x).toBeCloseTo(10);
    expect(project(0, -179).x).toBeCloseTo(90);
  });
});
