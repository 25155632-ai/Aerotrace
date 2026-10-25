import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Detection, Observation } from "../lib/api";
import AirspaceView from "./AirspaceView";

const first: Observation = { id: 1, icao24: "abcdef", callsign: "SIM01", observed_at: 1700000000,
  latitude: 19, longitude: 72, baro_altitude_m: 10000, geo_altitude_m: 10100, velocity_ms: 250,
  heading_deg: 90, source: "csv-import", source_kind: "simulated", received_at: 1700000000, quality_note: null };
const last: Observation = { ...first, id: 2, observed_at: first.observed_at + 10, longitude: 73 };
const detection: Detection = { id: 1, icao24: "abcdef", rule: "teleport", severity: "high", kind: "anomaly",
  detected_at: last.observed_at, latitude: 19, longitude: 73, evidence: {}, supporting_observation_ids: [1,2], source_kind: "simulated", source_kinds: ["simulated"] };
const base = { selectedId: null, onSelect: vi.fn(), onOpenInvestigation: vi.fn(), onOpenImport: vi.fn(), onUseStored: vi.fn(), query: "", signalsOnly: false, recording: null };
beforeEach(() => vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ type: "FeatureCollection", features: [] }) }))));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("data-driven globe", () => {
  it("uses recorded coordinates and hides future findings while seeking", async () => {
    render(<AirspaceView {...base} observations={[first,last]} detections={[detection]} />);
    await screen.findByTestId("globe-land");
    expect(screen.getByTestId("globe-aircraft-abcdef")).toHaveAttribute("data-longitude", "73");
    expect(screen.getByTestId("globe-aircraft-abcdef")).toHaveAttribute("data-severity", "high");
    fireEvent.change(screen.getByRole("slider"), { target: { value: String(first.observed_at) } });
    expect(screen.getByTestId("globe-aircraft-abcdef")).toHaveAttribute("data-longitude", "72");
    expect(screen.getByTestId("globe-aircraft-abcdef")).toHaveAttribute("data-severity", "none");
    expect(screen.queryByTestId("trail-abcdef")).not.toBeInTheDocument();
  });
  it("moves markers at the selected playback speed and stops at the end", async () => {
    render(<AirspaceView {...base} observations={[first,last]} detections={[detection]} />);
    await screen.findByTestId("globe-land");
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Play recording" }));
    expect(screen.getByTestId("globe-aircraft-abcdef")).toHaveAttribute("data-longitude", "72");
    act(() => { vi.advanceTimersByTime(1200); });
    expect(screen.getByTestId("globe-aircraft-abcdef")).toHaveAttribute("data-longitude", "73");
    expect(screen.getByRole("button", { name: "Play recording" })).toBeInTheDocument();
  });
  it("keeps last-known coordinates when a report loses its position", async () => {
    const missing = { ...last, latitude: null, longitude: null, quality_note: "missing_coordinates" };
    render(<AirspaceView {...base} observations={[first,missing]} detections={[]} />);
    await screen.findByTestId("globe-land");
    expect(screen.getByTestId("globe-aircraft-abcdef")).toHaveAttribute("data-longitude", "72");
    expect(screen.getByText("1 aircraft currently lack a position")).toBeInTheDocument();
  });
  it("does not draw future aircraft and allows map selection and a snapshot", async () => {
    const future = { ...last, icao24: "123456", callsign: "SIM02" };
    render(<AirspaceView {...base} observations={[first,future]} detections={[]} />);
    await screen.findByTestId("globe-land");
    fireEvent.change(screen.getByRole("slider"), { target: { value: String(first.observed_at) } });
    expect(screen.queryByTestId("globe-aircraft-123456")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "World map" }));
    fireEvent.click(screen.getByTestId("globe-aircraft-abcdef"));
    expect(base.onSelect).toHaveBeenCalledWith("abcdef");
  });
  it("shows an honest empty state and disables replay when no observations exist", async () => {
    render(<AirspaceView {...base} observations={[]} detections={[]} />);
    await screen.findByTestId("globe-land");
    expect(screen.getByText("Import a CSV to plot aircraft")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play" })).toBeDisabled();
  });
});
