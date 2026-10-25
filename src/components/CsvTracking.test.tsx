import { act, fireEvent, render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Observation } from "../lib/api";
import CsvTracking from "./CsvTracking";

const point = (time: number, latitude: number): Observation => ({
  id: -time, icao24: "abcdef", callsign: "TEST1", observed_at: time,
  latitude, longitude: 72, baro_altitude_m: 10000, geo_altitude_m: null,
  velocity_ms: 250, heading_deg: 90, source: "csv-import", source_kind: "real",
  received_at: time, quality_note: null,
});
afterEach(() => { cleanup(); vi.useRealTimers(); });
describe("CSV playback controls", () => {
  it("seeks to recorded positions and resets without claiming live data", () => {
    render(<CsvTracking observations={[point(1700000000, 19), point(1700000010, 20)]} />);
    expect(screen.getByText(/Position: 19.00000/)).toBeInTheDocument();
    fireEvent.change(screen.getByRole("slider"), { target: { value: "1700000010" } });
    expect(screen.getByText(/Position: 20.00000/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(screen.getByText(/Position: 19.00000/)).toBeInTheDocument();
    expect(screen.getByText(/not a live flight feed/)).toBeInTheDocument();
  });
  it("advances the recording at the selected speed and stops at the end", () => {
    vi.useFakeTimers();
    render(<CsvTracking observations={[point(1700000000, 19), point(1700000010, 20)]} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    act(() => { vi.advanceTimersByTime(1100); });
    expect(screen.getByText(/Position: 20.00000/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Replay" })).toBeInTheDocument();
  });
  it("disables playback for a single-timestamp snapshot", () => {
    render(<CsvTracking observations={[point(1700000000, 19)]} />);
    expect(screen.getByRole("button", { name: "Play" })).toBeDisabled();
    expect(screen.getByText(/Single-position snapshot/)).toBeInTheDocument();
  });
});
