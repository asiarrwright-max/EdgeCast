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
from app.routers import v3_settlement_health  # V3 settlement observability — read-only
from app.routers import v22_analytics         # V2.2: isolated parallel challenger
from app.routers import strategy_comparison  # Unified cross-strategy comparison
from app.routers import bet_watch             # Bet Watch — read-only decision support
from app.routers import city_study            # City Specialization Study — read-only analytics
from app.routers import verified_city_study   # Verified City Specialization — read-only
from app.routers import autonomy             # Autonomy approvals/status page API
from app.routers import readiness            # Real-Money Readiness dashboard — read-only
from app.routers import forward_evidence_reconciliation  # Forward-evidence reconciliation diagnostics — read-only
from app.routers import v31_shadow_validation  # Frozen prospective V3.1 evidence — read-only

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
app.include_router(v3_analytics.router, prefix="/api")
app.include_router(v3_settlement_health.router, prefix="/api")
app.include_router(v22_analytics.router, prefix="/api")
app.include_router(strategy_comparison.router, prefix="/api")
app.include_router(bet_watch.router, prefix="/api")
app.include_router(city_study.router, prefix="/api")
app.include_router(verified_city_study.router, prefix="/api")
app.include_router(autonomy.router, prefix="/api")
app.include_router(readiness.router, prefix="/api")
app.include_router(forward_evidence_reconciliation.router, prefix="/api")
app.include_router(v31_shadow_validation.router, prefix="/api")
