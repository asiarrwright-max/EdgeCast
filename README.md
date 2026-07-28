# EdgeCast

**Phase 1** — Live monitoring of Kalshi weather prediction markets with Open-Meteo forecast data.

EdgeCast discovers active weather markets on [Kalshi](https://kalshi.com), retrieves matching weather forecasts from [Open-Meteo](https://open-meteo.com), stores everything in PostgreSQL, and presents a mobile-responsive admin dashboard. No trades are placed, no Kalshi credentials are required.

---

## Architecture

```
artifacts/api-server/   Python 3.11 FastAPI backend
artifacts/edgecast/     React 18 + Vite frontend
lib/api-spec/           OpenAPI 3.1 spec (source of truth)
lib/api-client-react/   Generated React Query hooks (from spec)
lib/api-zod/            Generated Zod schemas (from spec)
```

The proxy routes `/api/*` → Python backend, `/` → React frontend.

---

## Stack

| Layer | Technology |
|-------|-----------|
| API | Python 3.11 · FastAPI · Uvicorn |
| Database | PostgreSQL · SQLAlchemy (async) · asyncpg |
| Auth | JWT (python-jose) · single admin user |
| Scheduler | asyncio background task (3-hour interval) |
| External data | Kalshi public REST API · Open-Meteo free API |
| Frontend | React 18 · Vite · TypeScript · TanStack Query |
| Tests | pytest · pytest-asyncio |

---

## Database Tables

| Table | Purpose |
|-------|---------|
| `kalshi_events` | Raw Kalshi event records |
| `kalshi_markets` | Active weather markets with prices |
| `weather_locations` | City → coordinates mapping |
| `weather_forecasts` | Open-Meteo daily snapshots per city |
| `job_runs` | Background data-collection run history |
| `app_errors` | Parsing and data-source errors |
| `app_settings` | Key/value app configuration |

Tables are created automatically on first startup via `SQLAlchemy create_all`.

---

## Setup

### 1. Required secrets

Set these in **Replit Secrets** (or a `.env` file for local dev):

| Secret | Description | Default |
|--------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | *(provided by Replit)* |
| `ADMIN_PASSWORD` | Admin login password | `changeme` |
| `SECRET_KEY` | JWT signing secret (≥ 32 chars) | *(insecure default)* |
| `ADMIN_USERNAME` | Admin username | `admin` |

> **Important:** Change `ADMIN_PASSWORD` and `SECRET_KEY` before deploying.

### 2. Run locally (development)

```bash
# Install Python packages (one-time)
pip install -r artifacts/api-server/requirements.txt

# Start backend
cd artifacts/api-server
uvicorn main:app --host 0.0.0.0 --port 8080 --reload

# Start frontend (separate terminal)
pnpm --filter @workspace/edgecast run dev
```

### 3. Run tests

```bash
cd artifacts/api-server
pytest -v
```

### 4. Regenerate API types (after spec changes)

```bash
pnpm --filter @workspace/api-spec run codegen
```

---

## Configuration

All settings live in `artifacts/api-server/app/config.py` and are read from environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | required | PostgreSQL URL |
| `ADMIN_USERNAME` | `admin` | Admin login |
| `ADMIN_PASSWORD` | `changeme` | Admin password |
| `SECRET_KEY` | *(insecure)* | JWT secret |
| `KALSHI_BASE_URL` | `https://api.elections.kalshi.com/trade-api/v2` | Kalshi API base |
| `OPENMETEO_BASE_URL` | `https://api.open-meteo.com/v1` | Open-Meteo base |

---

## Dashboard Features (Phase 1)

- **Market overview** — number of active weather markets, titles, tickers, cities, target dates
- **Live prices** — YES/NO bid/ask from Kalshi (read-only)
- **Weather matching** — whether Open-Meteo forecast data was found for each market's city
- **Data health screen** — live status of Kalshi and Open-Meteo APIs
- **Job history** — record of every data-collection run with timestamps, counts, and errors
- **Error log** — parsing and fetch errors surfaced in the UI
- **"Run data collection now"** button — triggers an immediate fetch and refresh
- **Automatic collection** — every 3 hours, overlap-safe

---

## API Endpoints

All endpoints are under `/api`. Authentication uses `Authorization: Bearer <token>`.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/healthz` | None | Server health |
| `GET` | `/api/health/services` | ✓ | External API status |
| `POST` | `/api/auth/login` | None | Get JWT token |
| `GET` | `/api/dashboard` | ✓ | Dashboard summary |
| `GET` | `/api/markets` | ✓ | Active weather markets |
| `GET` | `/api/markets/{ticker}` | ✓ | Single market detail |
| `GET` | `/api/weather/forecasts` | ✓ | Stored forecasts |
| `GET` | `/api/jobs` | ✓ | Job run history |
| `POST` | `/api/jobs/collect` | ✓ | Trigger manual collection |
| `GET` | `/api/errors` | ✓ | Recent app errors |

Interactive docs: `/api/docs` (Swagger UI) and `/api/redoc`.

---

## Phase 1 Limitations

- **No real-time price streaming** — prices update only when data collection runs.
- **No probability calculations** — raw YES/NO prices only; analysis is Phase 2.
- **No settlement station mapping** — city matching uses a static lookup table; some markets may not match.
- **City detection is heuristic** — Kalshi ticker formats can vary; unknown cities won't have weather data.
- **Single admin user** — multi-user auth is out of scope for Phase 1.
- **Kalshi public API only** — no account-specific data (order book depth, personal positions).

---

## Deployment

1. Set all required secrets in Replit Secrets.
2. Click **Publish** in Replit.
3. The production server runs `uvicorn main:app` from `artifacts/api-server/`.
4. The frontend is built as a static bundle by Vite.

> **Do not begin Phase 2** (probability calculations, paper trading) until Phase 1 has been reviewed and confirmed working in production.
