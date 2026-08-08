import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.scheduler import start_scheduler, shutdown_scheduler
from app.services.audit_checks import run_all_audit_checks
import app.database as _db_module
from app.routers import audit, health, auth, dashboard, markets, weather, jobs, errors, analysis, paper_trades, analytics, v21_analytics
from app.routers import v3_analytics  # V3: additive router import
from app.routers import v22_analytics         # V2.2: isolated parallel challenger
from app.routers import strategy_comparison  # Unified cross-strategy comparison

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("EdgeCast starting up…")
    await init_db()
    await start_scheduler()
    # Run read-only audit DB checks on startup so the Audit & Validation
    # tab shows current results immediately. Failures are logged but never
    # block startup or affect trading logic.
    try:
        async with _db_module.AsyncSessionLocal() as db:
            await run_all_audit_checks(db)
            await db.commit()
        logger.info("Startup audit checks complete.")
    except Exception as exc:
        logger.warning("Startup audit checks failed (non-fatal): %s", exc)
    yield
    logger.info("EdgeCast shutting down…")
    await shutdown_scheduler()


app = FastAPI(
    title="EdgeCast API",
    description="Kalshi weather market monitoring",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# All routes mount under /api so the proxy can route them
app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api/auth")
app.include_router(dashboard.router, prefix="/api")
app.include_router(markets.router, prefix="/api")
app.include_router(weather.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(errors.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(paper_trades.router, prefix="/api")
app.include_router(audit.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(v21_analytics.router, prefix="/api")
app.include_router(v3_analytics.router, prefix="/api")   # V3: additive router registration
app.include_router(v22_analytics.router, prefix="/api")         # V2.2: parallel challenger
app.include_router(strategy_comparison.router, prefix="/api")   # Unified comparison
