# Aerotrace — CSV aircraft tracking upgrade

## Use the feature

1. Start both the FastAPI backend and Vite frontend. Restart the backend after replacing the source; preview now returns normalized observations.
2. Open **CSV Import**, choose your CSV and select its provenance.
3. Click **Preview & track CSV**. Validation errors show their original CSV line numbers. Preview never writes to the database.
4. The recording opens below the upload panel. Click **Play**, choose **1× / 10× / 60× / 300×**, or drag the timeline. Click an aircraft or search its callsign / ICAO24 to inspect its last report, altitude, speed and coordinates.
5. Click **Import valid rows** when you want to save the recording. This also opens the tracking view if you skipped preview.

For an immediate moving example, choose **Load bundled scenario**, then **Preview & track CSV**, then **Play** at 10× or 60×. Authentic bundled OpenSky snapshots have one position per aircraft, so they cannot show recorded motion.

## What tracking means here

CSV playback follows original UTC timestamps and reveals only observations at or before the playback clock. Markers move when another recorded position is available; the app does not predict coordinates between points. Trails connect recorded observations and are not verified flight paths. The adaptive coordinate plot fits the entire recording, including international date line crossings; it is not a geographic basemap or a navigational display.

A CSV is a fixed recording. Continuous current-flight tracking needs an active data feed and credentials/availability from that provider. Uploaded provenance is user asserted and remains `csv-import`; a real-labelled upload is never presented as a live provider response.

Movement requires multiple rows with the same six-character hexadecimal ICAO24, different timestamps, and latitude/longitude. Required columns: `icao24,observed_at,latitude,longitude`. Optional examples: `callsign,baro_altitude_m,velocity_ms,heading_deg`. Times support epoch seconds, epoch milliseconds or ISO 8601 with an explicit timezone. Missing positions remain listed without a fabricated marker.

Import limits remain 1,000,000 characters and 10,000 data rows. File selection checks size before reading. Invalid rows are skipped and reported, and preview uses the same backend normalization as import.

## Windows startup

Extract the ZIP and open PowerShell inside the extracted project folder.

Backend terminal:

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Second terminal, inside the project folder:

```powershell
npm ci
npm run dev
```

Open the local URL printed by Vite. The included Vite proxy sends `/api` to backend port 8000. Existing database files and environment credentials are intentionally excluded from this distributable source archive; retain your local database separately if needed.

## Project rename

The project is now Aerotrace. Page titles, social metadata, header/footer branding, backend API title, alert messages and export filenames use the new name. Existing `SPOOFSKY_*` environment keys, database filename and internal data-source identifiers remain compatible with earlier installations.

## Additional fixes

Corrected a broken TypeScript import in the track grouping module, strict type errors in request cancellation options and incident replay initialization, and exposed row-level CSV validation errors in the interface.

## Validation

- Backend suite: 130 tests passed.
- Frontend suite: 7 tests passed, including timestamp filtering, dateline fitting, playback, seeking and snapshots.
- TypeScript strict check and production build passed. Existing build chunk-size warnings remain.
- Full browser visual verification was unavailable because the browser binary download failed.
