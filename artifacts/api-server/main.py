import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.scheduler import start_scheduler, shutdown_scheduler
from app.routers import health, auth, dashboard, markets, weather, jobs, errors, analysis

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
