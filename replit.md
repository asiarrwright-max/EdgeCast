# EdgeCast

EdgeCast is a private admin-only dashboard that monitors Kalshi weather prediction markets, fetches Open-Meteo forecasts, computes probability estimates, and simulates paper trades to evaluate whether identified edges hold up over time.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks + Zod schemas from OpenAPI spec (run after any spec change)
- `pnpm run typecheck` — full typecheck across all packages
- Required env: `DATABASE_URL` (Postgres), `SECRET_KEY`, `SESSION_SECRET`, `ADMIN_PASSWORD`

## Stack

- **Backend**: Python / FastAPI + SQLAlchemy async (asyncpg), PostgreSQL
- **Frontend**: React + Vite, Wouter routing, TanStack Query, Orval-generated hooks
- **Monorepo**: pnpm workspaces (Node.js / TypeScript for frontend, Python for backend)
- **API codegen**: Orval generates React Query hooks + Zod validators from `lib/api-spec/openapi.yaml`

## Where things live

```
artifacts/api-server/        FastAPI backend
  app/
    models.py                 All SQLAlchemy models
    database.py               DB init, migrations (_apply_migrations)
    services/
      analyzer.py             Orchestrates settlement parsing + probability engine
      collector.py            Kalshi fetch → forecast fetch → analysis → paper trading
      paper_trading.py        Paper trade eligibility, direction decision, position math
      settlement.py           Fetches Kalshi results and settles open paper trades
      probability_engine.py   Gaussian model (sigma table, confidence scoring)
      settlement_parser.py    Market title/subtitle → SettlementContract
      kalshi.py               Kalshi REST client
      openmeteo.py            Open-Meteo REST client
    routers/
      paper_trades.py         /paper-trades endpoints
      markets.py              /markets endpoints
      jobs.py                 /jobs + /jobs/collect
      dashboard.py            /dashboard
    scheduler.py              asyncio loops: collection (3h), settlement (3h offset 1.5h)
  tests/                      pytest test suite (220 tests)
artifacts/edgecast/           React frontend
  src/pages/
    paper-trading.tsx         Paper Trading list page (summary cards + trades table)
    paper-trade-detail.tsx    Paper Trade detail page
    market-detail.tsx         Market detail page
    dashboard.tsx             Dashboard page
lib/api-spec/openapi.yaml     Source of truth for all API schemas and endpoints
```

## Paper Trading

**What it is**: After every collection + analysis run, EdgeCast automatically reviews every supported market snapshot and hypothetically buys YES or NO based on the rules below. When Kalshi later settles the market, the record is updated with the real outcome and simulated P/L.

**No real trades are placed.** No Kalshi trading credentials are used.

### Eligibility rules

A market qualifies for a paper trade if ALL of the following are true:
1. `analysis_status == "supported"` (the settlement contract was parsed successfully)
2. `ec_probability` is available (forecast exists)
3. `market_probability` is available (Kalshi prices exist)
4. Confidence label ≥ `min_confidence` (default: **High**)
5. Market `status == "active"` (not yet closed)
6. No existing paper trade for `(market_ticker, strategy_version)` (duplicate prevention)

### Direction decision formulas

```
YES trade:  ec_yes_probability − YES_ask_price ≥ min_edge  (default min_edge = 10pp)
NO trade:   (1 − ec_yes_probability) − NO_ask_price ≥ min_edge
```

Both directions are evaluated. If both qualify, the higher-edge direction wins.

**Price selection**: YES ask preferred for YES trades; NO ask preferred for NO trades. Falls back to bid when ask is unavailable.

### Position math

```
quantity (contracts) = stake / purchase_price
```
Kalshi contracts pay **$1.00 per winning contract**.

### Settlement P/L

```
WIN:  gross_payout = quantity × $1.00
      profit_loss  = gross_payout − stake
      return_pct   = profit_loss / stake × 100

LOSS: gross_payout = $0.00
      profit_loss  = −stake
      return_pct   = −100%

VOID: gross_payout = stake  (refund)
      profit_loss  = $0
```

### Duplicate prevention

One paper trade per `(market_ticker, strategy_version)`. Later collection runs will not create a second trade for the same ticker + version combination.

### Strategy versioning

The `strategy_version` field allows future strategy changes to coexist with historical trades. Changing the version effectively "starts fresh" while preserving the old records.

### Settlement source

Settlement is determined **exclusively** from the authoritative Kalshi API (`GET /markets/{ticker}`). The `result` field is used when present; `status: canceled` maps to void. Forecasts are never used to infer settlement.

### Settlement scheduling

The settlement loop runs every 3 hours, offset 1.5 hours from the collection loop to avoid overlap. Each open trade is checked once per cycle; trades with no result yet remain OPEN until the next check.

### Known limitations

- No fees, slippage, partial fills, or liquidity modelled. Results likely overstate real-world performance.
- Fractional contract quantities are stored for accuracy but would not be possible in a real account.
- Sample sizes of 30+ settled trades are recommended before drawing conclusions from win-rate or ROI metrics.
- Changing `min_edge`, `min_confidence`, or `stake` settings does NOT retroactively alter existing trades.

### Admin settings (runtime-adjustable)

| Setting | Key | Default |
|---|---|---|
| Enabled | `paper_trading.enabled` | `true` |
| Min edge threshold | `paper_trading.min_edge_pct` | `10.0` pp |
| Min confidence | `paper_trading.min_confidence` | `High` |
| Stake per trade | `paper_trading.stake` | `$10.00` |
| Strategy version | `paper_trading.strategy_version` | `v1.0` |

Settings are stored in the `app_settings` key-value table and editable from the Paper Trading page → Settings panel.

### Manual test flow

1. Trigger a collection run from the Dashboard.
2. Navigate to Paper Trading — trades for eligible markets appear automatically.
3. Click any trade for the full decision rationale, entry prices, and edge breakdown.
4. Open a settled market on Kalshi; the settlement job runs every 3 hours and will update the trade automatically.

## Architecture decisions

- **`market_implied_probability()` returns `float | None`** — a previous attempt to return a tuple was reverted. The plain float represents the YES midpoint (or best available price).
- **`_apply_migrations` pattern** — all schema evolution is handled by idempotent `ALTER TABLE … ADD COLUMN IF NOT EXISTS` calls rather than a migration framework. New columns in `models.py` must be accompanied by a migration entry.
- **Auth**: `Depends(get_current_user)` on all non-health endpoints. Admin password is set via `ADMIN_PASSWORD` secret.
- **Kalshi prices are decimals (0.0–1.0)** — normalized in `parse_market()`. Display code multiplies by 100 for percentages.
- **Settings in `AppSetting` table** — no separate settings table. Key pattern: `paper_trading.*`.
- **Wouter routing**: `(.*)`  catch-all required for multi-segment routes like `/paper-trading/:id`. `/:rest*` only matches single-segment paths.

## Gotchas

- `DATABASE_URL` from Replit includes `sslmode=` which asyncpg rejects. `app/config.py` strips it and passes as `connect_args` instead.
- Vite requires `.tsx` extension for files containing JSX. After renaming `.ts` → `.tsx`, restart the workflow (Vite caches old resolution).
- Orval generates `zod.int()` for `type: integer` in OpenAPI spec — Zod v3 doesn't have `.int()`. Use `type: number` instead.
- Bare `type: object` schemas in the OpenAPI spec generate `z.looseObject()` in Orval for Zod v4 which doesn't exist in Zod v3. Add explicit `properties:` to any object schema to avoid this.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._
