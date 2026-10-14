# OrbitDesk

An AI-powered space mission control dashboard for exploring satellites and their orbits.

OrbitDesk lets you browse real satellite catalog data from CelesTrak, inspect orbital
elements, view calculated positions and ground tracks on an interactive Leaflet map, and
ask an optional AI assistant questions that are grounded in the same backend data.

## Features

- **Satellite dashboard** – searchable, filterable list of satellites grouped by category
  (space stations, Starlink, GPS, weather, etc.) with loading, empty, and error states.
- **Map visualization** – 2D Leaflet map (loaded from the unpkg CDN) showing the selected
  satellite's calculated sub-satellite position and one-orbit ground track, clearly
  labeled as *calculated from TLE* (not a live observation), with the calculation time,
  TLE epoch, and cache freshness timestamps.
- **Python backend** – FastAPI app with modular separation of concerns:
  `backend/app/celestrak.py` (fetching + caching), `backend/app/tle.py` (TLE parsing +
  demo data), `backend/app/orbit.py` (Skyfield SGP4 calculations), `backend/app/db.py`
  (SQLite persistence), `backend/app/ai.py` (assistant), `backend/app/routers/` (API).
- **AI assistant (optional)** – chat interface that calls backend tools (satellite search,
  details, position, ground track) before answering. Answers are grounded in retrieved
  data; missing data is reported instead of guessed. Works with any OpenAI-compatible API.
- **Persistence** – SQLite database (created automatically) caches satellite TLEs and
  records when each group was last updated.

## Current limitations

- Positions are computed from cached TLEs with SGP4 — they are predictions, not live
  telemetry. The UI labels them accordingly.
- The AI assistant requires an external OpenAI-compatible API key (`OPENAI_API_KEY`).
  Without it, the rest of the app works and the chat panel shows setup instructions.
- Persistence is SQLite only; there is no PostgreSQL migration tooling included (the
  design isolates all DB access in `backend/app/db.py` to make such a swap easy).
- No Docker support is included.
- CelesTrak usage policy: automatic refreshes are throttled by
  `ORBITDESK_CACHE_TTL_SECONDS` (default 900 s) and manual refreshes to one per minute.
  Per CelesTrak's terms, TLE data is intended for civilian space enthusiasts, not for
  operational use.

## Prerequisites

- Python 3.11 or newer (the test suite was run with Python 3.12)
- Internet access for live CelesTrak data and first-run Skyfield leap-second download
  (the app falls back to bundled demo data if CelesTrak is unreachable)

## Installation

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Dependencies (pinned by range in `requirements.txt`): FastAPI, Uvicorn, Skyfield,
httpx, python-dotenv, pytest.

## Configuration

All configuration is optional and read from environment variables. Copy the example
file and edit as needed (`.env` at the repo root is loaded automatically and is
git-ignored — never commit secrets):

```bash
cp .env.example .env
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | *(empty)* | Enables the AI assistant. Server-side only; never sent to the browser. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible chat-completions endpoint. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model used for chat. |
| `ORBITDESK_DB_PATH` | `<repo>/data/orbitdesk.db` | SQLite file (created automatically). |
| `ORBITDESK_DATA_DIR` | `<repo>/data/skyfield` | Where Skyfield stores leap-second/ephemeris files. |
| `ORBITDESK_CACHE_TTL_SECONDS` | `900` | Minimum age (seconds) before a cached group is auto-refetched from CelesTrak. |
| `ORBITDESK_FETCH_TIMEOUT_SECONDS` | `15` | HTTP timeout for CelesTrak requests. |
| `ORBITDESK_FORCE_DEMO_MODE` | `false` | When true, never touch the network; load clearly-labeled bundled demo TLEs. |
| `ORBITDESK_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |

## Running the app

The frontend is served by the backend itself (FastAPI mounts `frontend/static/` at
`/static` and returns `frontend/index.html` at `/`), so a single command starts
everything:

```bash
uvicorn backend.app.main:app --reload
```

Then open **http://127.0.0.1:8000/** in a browser. Interactive API docs are at
**http://127.0.0.1:8000/docs**.

(Alternatively, from the `backend/` directory: `uvicorn app.main:app --reload`.)

On startup the SQLite schema is initialized automatically. Pick a satellite group in the
UI to fetch its catalog from CelesTrak (or fall back to demo data).

## API endpoints

Base path: `/api`. All responses are JSON.

| Method & path | Description |
| --- | --- |
| `GET /api/health` | Status, demo mode, AI enabled flag, cache TTL. |
| `GET /api/satellites/groups` | Cached group summary + whitelisted CelesTrak groups. |
| `POST /api/satellites/refresh/{group}?force=true` | Fetch/refresh a group's TLE cache. Without `force`, refreshes only when the cache is older than the TTL; `force=true` triggers a manual refetch, rate-limited to one per minute (→ 429). Unknown group → 404. In demo mode (`ORBITDESK_FORCE_DEMO_MODE`) no network request is made and the 1-minute limiter does not apply. |
| `GET /api/satellites?group=&q=&limit=&offset=` | List cached satellites. `q` matches name or NORAD ID fragment; `limit` 1–5000 (default 200); `offset` ≥ 0. Includes `last_updated` and `data_source`. |
| `GET /api/satellites/{norad_id}` | One satellite with orbital elements parsed from its TLE. Non-numeric ID → 422; unknown ID → 404. |
| `GET /api/satellites/{norad_id}/position?time=` | SGP4-calculated lat/lon, altitude, velocity, etc. `time` is ISO-8601 UTC (defaults to now); invalid time → 422. Response includes `kind: "calculated_from_tle"` and a note that it is not a live observation. |
| `GET /api/satellites/{norad_id}/ground-track?time=&points=` | Ground-track samples (~one orbit centered on `time`). `points` 2–360 (default 72). |
| `GET /api/assistant/status` | `{enabled, model}` for the AI assistant. |
| `POST /api/chat` | Body `{"question": "...", "history": [{"role":"user"\|"assistant","content":"..."}]}`. Question 1–1000 chars, non-blank; history max 20 messages, roles validated. Returns `{answer, tool_calls, grounded, disabled}`. |

Examples (with the server running):

```bash
curl http://127.0.0.1:8000/api/health

# Load the "stations" group from CelesTrak (or demo fallback).
# Omit ?force=true for normal use so the cache TTL is respected.
curl -X POST "http://127.0.0.1:8000/api/satellites/refresh/stations?force=true"

# Search cached satellites
curl "http://127.0.0.1:8000/api/satellites?q=ISS&group=stations"

# ISS details and calculated position
curl http://127.0.0.1:8000/api/satellites/25544
curl "http://127.0.0.1:8000/api/satellites/25544/position?time=2026-10-14T12:00:00Z"

# Ground track
curl "http://127.0.0.1:8000/api/satellites/25544/ground-track?points=120"

# Ask the assistant
curl -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"question": "Where is the ISS right now?"}'
```

## How data flows

1. **Fetching** – `POST /api/satellites/refresh/{group}` downloads a group's TLEs from
   CelesTrak (`celestrak.org/NORAD/elements/gp.php`) using a descriptive User-Agent.
   Only a whitelist of groups can be requested.
2. **Caching** – Parsed TLEs are stored in SQLite with the fetch timestamp and source
   (`celestrak` or `demo`). Automatic refreshes are skipped while the cache is younger
   than `ORBITDESK_CACHE_TTL_SECONDS`; manual forced refreshes are limited to one per
   minute across the app. Every list/detail response carries `data_fetched_at` /
   `last_updated` so the UI can show freshness.
3. **Demo mode** – If CelesTrak is unreachable and no cache exists, the app loads a
   bundled set of demo TLEs and marks the data source as `demo` with a visible notice.
   Setting `ORBITDESK_FORCE_DEMO_MODE=true` skips the network entirely. Demo data is
   never presented as live.
4. **Position calculation** – `orbit.py` uses Skyfield's SGP4 propagation on the cached
   TLE to compute the sub-satellite point (lat/lon), altitude, velocity, and ground
   tracks for a requested UTC time. Results are labeled `kind: "calculated_from_tle"`.
5. **Timestamps** – Each satellite record keeps the TLE line data plus the time it was
   fetched; position responses include `computed_at` and `tle_epoch`, and the UI displays
   the calculation time, TLE epoch, and data freshness alongside the "calculated from
   TLE" label.

## AI assistant behavior

- With `OPENAI_API_KEY` set, `/api/chat` runs a tool-calling loop: the model must call
  backend functions (search satellites, get details, calculate position/ground track,
  list groups) and answers are grounded in those results, including satellite names and
  data timestamps. The system prompt forbids inventing positions or satellite details.
- **Without a key**, the app remains fully functional. `/api/assistant/status` returns
  `{"enabled": false}` and `/api/chat` returns a helpful message explaining how to
  enable the assistant (`disabled: true` in the response). The chat panel shows the
  same setup instructions.
- The key lives only in the server environment; the browser never sees it.

## Running tests

From the repository root:

```bash
pytest backend/tests -v
```

The suite (21 tests) covers: TLE parser field offsets verified against Skyfield's own
parsing (inclination regression test included), API validation and error responses,
orbit calculation sanity checks, and AI-disabled behavior. External services (CelesTrak
HTTP, OpenAI) are mocked; tests do not require network access.

## Troubleshooting

- **`Address already in use` on port 8000** – another process holds the port; stop it or
  run `uvicorn backend.app.main:app --reload --port 8001`.
- **`ModuleNotFoundError: backend`** – run uvicorn from the repository root (or install
  the project layout accordingly); the import path `backend.app.main:app` requires the
  root on `sys.path`, which is the case when you launch from the root directory.
- **Dashboard loads but the list is empty** – pick a group and let it load; the first
  fetch populates the cache. Check `/api/health` and the notice banner for fetch errors.
- **"Could not reach CelesTrak" / demo notice** – network blocked or CelesTrak down. The
  app serves cached or demo data. Set `ORBITDESK_FORCE_DEMO_MODE=true` to explore
  offline, or restore connectivity and press Refresh.
- **HTTP 429 on refresh** – manual refreshes are rate-limited to one per minute; wait
  for the countdown shown in the error message.
- **Slow first position request** – Skyfield downloads a small leap-second file to
  `ORBITDESK_DATA_DIR` on first use; subsequent runs are local.
- **Chat says the assistant is disabled** – set `OPENAI_API_KEY` in `.env` and restart
  the server; verify with `curl http://127.0.0.1:8000/api/assistant/status`.
- **Verbose/no logs** – adjust `ORBITDESK_LOG_LEVEL` (e.g. `DEBUG`) and restart.
