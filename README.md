> CSV tracking upgrade: see [CSV_TRACKING_GUIDE.md](CSV_TRACKING_GUIDE.md) for the new playback feature and Windows setup.

# Aerotrace

Build this app using the HTML files referenced below. You can hotlink the images referenced in the HTML. The attached images are screenshots of the desired screens. Here are public links to the html of the screens which you should read and use to build the app:

1. https://contribution.usercontent.google.com/download?c=CgthaWRhX2NvZGVmeBJ8Eh1hcHBfY29tcGFuaW9uX2dlbmVyYXRlZF9maWxlcxpbCiVodG1sXzAwMDY1ZTk5ZjEzYTdmNzUwNzA5MmM0YzA2MDQyZTJkEgsSBxC84Iq6oxYYAZIBJAoKcHJvamVjdF9pZBIWQhQxNzU5MTg2NjY0MjQyNDcwNjE4MQ&filename=&opi=89354086

This project was built with [Lovable](https://lovable.dev).

## Build with Lovable

Continue developing this project in the [Lovable editor](https://lovable.dev/projects/e96ef098-7848-4617-b443-46897658541c).

- **Ship faster**: describe what you want to build and Lovable handles the code.
- **Stay in sync**: every change made in Lovable is committed straight to this repository.
- **Full ownership**: this code is yours. Push to `main` on GitHub and your changes sync back into Lovable, ready for your next prompt.

## Development

Prefer working locally? You need Node.js and npm — [install with nvm](https://github.com/nvm-sh/nvm#installing-and-updating).

```sh
git clone <this-repository-url>
cd <repository-name>
npm i
npm run dev
```

The dashboard runs at `http://127.0.0.1:5173/`. The FastAPI service runs at
`http://127.0.0.1:8000/`; opening that address redirects to the dashboard while
keeping the JSON API available under `/api/*`.

Start the backend in a second terminal from `backend/`:

```sh
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

For a quick local walkthrough, open either `http://127.0.0.1:5173/` or
`http://127.0.0.1:8000/`, then use **Load Demo Data** followed by **Run
Detection** in the dashboard.

### Windows quick start and HTTP 502 troubleshooting

Run the backend and frontend in two separate terminals. The frontend's
`/api/*` requests are proxied to `127.0.0.1:8000`; if the backend terminal is
not running, Vite reports `HTTP 502` / `ECONNREFUSED` for every dashboard task.

**Terminal 1 — backend** (from `backend\`):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

If `python` is not recognized, install Python and reopen PowerShell. The
Windows `py` launcher is optional; it is not required by this project.

Check that it is ready before opening the dashboard:

```powershell
curl.exe http://127.0.0.1:8000/api/health
```

The expected response is:

```json
{"status":"ok","version":"0.1.0","database":"ready"}
```

**Terminal 2 — frontend** (from the project root):

```powershell
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

Then open `http://127.0.0.1:5173/`. Do not close the backend terminal while
using the dashboard. A 502 from `/api/ingest/opensky` can also mean the remote
OpenSky source is unavailable or rate-limited; that is different from the
local `ECONNREFUSED 127.0.0.1:8000` error and does not indicate that the CSV
importer is broken.

### Basic web workflow

1. Open **Overview** and confirm the source banner loads.
2. Use **Load Demo Data** for a deterministic local test.
3. Click **Run Detection** and review **Aircraft**, **Investigation Lab**, and
   **Alerts**.
4. Open **CSV Import**, choose an authentic sample, click **Preview CSV**, and
   then **Import valid rows**.
5. For live web data, open the live ingestion control, enter a bounding box,
   fetch the source, and verify the source status before interpreting results.
   Live observations are labelled real; demo data remains labelled simulated.

### Pitch walkthrough

- Use the **Live / Pitch** toggle in the top bar to switch between backend-backed
  values and a clearly labelled presentation snapshot (`PITCH PRESET`).
- Use **3D Globe** and **2D Map** in the trajectory panel. The 2D view is a
  self-contained tactical SVG map with clickable targets and layer toggles, so
  it does not depend on external map tiles during a pitch.
- **Investigation Lab** shows persisted candidate events and model readiness;
  **Alerts** shows persisted alert records; **CSV Import** supports file upload,
  validation preview, and commit.
- **Load bundled scenario** loads the synthetic replay CSV from
  `public/samples/coordinated-spoof-replay.csv`; it is labelled simulated and
  is safe to use for a deterministic demonstration.
