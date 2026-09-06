"""
Settled-V3 Accuracy Lab (read-only)
===================================

Exports a frozen settled-V3 snapshot and runs an offline candidate bakeoff.
This script is strictly read-only and does not modify forecasting, eligibility,
settlement, or production behavior.

Usage:
    cd artifacts/api-server
    PYTHONPATH=. python scripts/run_v3_settled_bakeoff.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.models_v3 import V3PaperTrade  # noqa: E402
from app.services.v3_accuracy_lab import build_settled_v3_accuracy_lab_report  # noqa: E402


async def main() -> None:
    settings = get_settings()
    db_url, connect_args = settings.get_async_db_url()
    engine = create_async_engine(db_url, connect_args=connect_args, echo=False)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    async with SessionLocal() as session:
        result = await session.execute(
            select(V3PaperTrade).where(
                V3PaperTrade.status == "SETTLED",
                V3PaperTrade.strategy_version == "v3.0",
            )
        )
        rows = list(result.scalars().all())

    report = build_settled_v3_accuracy_lab_report(
        rows,
        as_of=datetime.now(timezone.utc),
    )

    out_dir = Path(__file__).parent.parent / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "settled_v3_accuracy_lab_bakeoff.json"
    out_file.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")

    checkpoint = {
        "frozen_population": report["frozen_population"],
        "partition_protocol": {
            "development_event_count": report["partition_protocol"]["development_event_count"],
            "validation_event_count": report["partition_protocol"]["validation_event_count"],
            "holdout_event_count": report["partition_protocol"]["holdout_event_count"],
        },
        "baseline_reproduction": report["baseline_reproduction"],
        "first_breakdown": report["error_breakdowns"]["by_threshold_vs_range"][:3],
        "top_candidate": report["recommendation"]["top_candidate"],
        "recommendation": report["recommendation"]["decision"],
    }
    print(json.dumps(checkpoint, indent=2, default=str))
    print(f"\nReport written to: {out_file}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
