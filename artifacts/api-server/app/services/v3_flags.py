"""
V3 Feature Flag Helpers
========================
Thin wrappers around AppSetting for reading and initializing V3 feature flags.

All four V3 flags default to ``false`` and must be explicitly enabled.
This module is the single source of truth for flag names and defaults.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting
from app.models_v3 import V3_FLAG_DEFAULTS

logger = logging.getLogger(__name__)


async def ensure_v3_feature_flags(session: AsyncSession) -> None:
    """
    Insert all four V3 feature flags with value ``false`` if they do not already
    exist.  Called once on startup.  Idempotent — existing flags are untouched.
    """
    from sqlalchemy import select
    for key, default_value in V3_FLAG_DEFAULTS.items():
        existing = await session.execute(
            select(AppSetting).where(AppSetting.key == key)
        )
        row = existing.scalar_one_or_none()
        if row is None:
            session.add(AppSetting(key=key, value=default_value))
            logger.info("[V3 Flags] Created flag %s = %s", key, default_value)
    await session.flush()


async def get_v3_flag(session: AsyncSession, flag_key: str) -> bool:
    """Return True if a V3 feature flag is set to 'true'."""
    from sqlalchemy import select
    result = await session.execute(
        select(AppSetting).where(AppSetting.key == flag_key)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return False
    return (row.value or "").lower() in ("true", "1", "yes")
