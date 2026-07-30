"""
V3 Look-Ahead Validator
========================
Mandatory gate for every historical record before it enters V3HistoricalRecord.

The purpose of look-ahead validation is to ensure that the V3 model is trained
only on data that would have been genuinely available at the simulated
decision time.  Using data that was not available at decision time (e.g. a
forecast generated after the valid date, or a "nowcast" that incorporated
observations) would produce a model that appears better than it truly is and
invalidates any walk-forward calibration results.

Validation rules (all must pass for status = "ok")
----------------------------------------------------
1. MISSING_INIT_TIME
   ``forecast_init_time`` must be present.  Forecasts with no documented
   initialization time cannot be validated and are unconditionally rejected.

2. FUTURE_INIT_TIME
   ``forecast_init_time`` must be in the past relative to the retrieval
   time.  A forecast that was "initialized" after we retrieved it is
   logically impossible and indicates a data error.

3. REANALYSIS_NOT_ALLOWED
   Reanalysis products (ERA5, MERRA-2, etc.) are NOT genuine forecasts.
   They incorporate observational data and are constructed after the fact.
   Training on reanalysis as if it were a forecast would introduce severe
   look-ahead bias.  Records with ``is_reanalysis=True`` are rejected here.

4. LOOKAHEAD_VIOLATION
   The forecast initialization time must be BEFORE the valid time minus
   the nominal lead time, plus a tolerance.

   For a model run initialized at T_init forecasting T_valid:
       required: T_init <= T_valid - lead_time_hours + TOLERANCE_HOURS

   We use TOLERANCE_HOURS = 12 (half a day) to accommodate:
   - GFS runs initialized at 00Z, 06Z, 12Z, 18Z
   - Time-zone offset differences in daily aggregation
   - Minor rounding in derived init times (e.g. from Open-Meteo)

   A stricter tolerance of 0 h is available via
   ``validate_record(..., strict=True)`` for debugging.

5. VALID_TIME_INCONSISTENCY
   ``forecast_valid_time`` must be >= ``forecast_init_time``.
   A valid time before the init time is a data error.

6. MISSING_FORECAST_VALUE
   If ``forecast_tmax_raw`` is None, the record is flagged
   ``MISSING_FORECAST_VALUE`` but NOT rejected here — it is stored as
   quality_status = "pending_observation" so the audit view can show
   coverage gaps.  The ingestion orchestrator skips error computation
   for these rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from app.services.v3_providers.base import RawForecastRecord


# ── Constants ──────────────────────────────────────────────────────────────

TOLERANCE_HOURS = 12
# See docstring above for rationale.


# ── Rejection reasons (enum so code can compare without raw strings) ───────

class RejectionReason(str, Enum):
    MISSING_INIT_TIME          = "MISSING_INIT_TIME"
    FUTURE_INIT_TIME           = "FUTURE_INIT_TIME"
    REANALYSIS_NOT_ALLOWED     = "REANALYSIS_NOT_ALLOWED"
    LOOKAHEAD_VIOLATION        = "LOOKAHEAD_VIOLATION"
    VALID_TIME_INCONSISTENCY   = "VALID_TIME_INCONSISTENCY"
    MISSING_FORECAST_VALUE     = "MISSING_FORECAST_VALUE"


# ── Validation result ──────────────────────────────────────────────────────

@dataclass
class LookaheadResult:
    is_valid: bool
    # True = record passes all hard gates; may still have soft flags.
    rejection_reason: str | None = None
    # Populated when is_valid=False; one of RejectionReason values.
    flags: list[str] = field(default_factory=list)
    # Soft flags that do not cause rejection (e.g. MISSING_FORECAST_VALUE).
    notes: str | None = None
    # Human-readable explanation; shown in audit UI.


# ── Validator ─────────────────────────────────────────────────────────────

def validate_record(
    record: RawForecastRecord,
    *,
    strict: bool = False,
) -> LookaheadResult:
    """
    Run all look-ahead checks on one ``RawForecastRecord``.

    Parameters
    ----------
    record
        The raw forecast record to validate.
    strict
        If True, use 0-hour tolerance instead of TOLERANCE_HOURS.
        Useful for debugging; production ingestion uses strict=False.

    Returns
    -------
    LookaheadResult
        is_valid=True only when all hard rules pass.
    """
    flags: list[str] = []
    tolerance_h = 0 if strict else TOLERANCE_HOURS

    # ── Rule 1: MISSING_INIT_TIME ──────────────────────────────────────────
    if record.forecast_init_time is None:
        return LookaheadResult(
            is_valid=False,
            rejection_reason=RejectionReason.MISSING_INIT_TIME,
            flags=[RejectionReason.MISSING_INIT_TIME],
            notes=(
                f"[{record.city}] Provider '{record.provider}' did not return "
                f"a forecast initialization time for {record.target_date_local} "
                f"(lead={record.lead_time_hours}h).  "
                "Records without a confirmed init time cannot be look-ahead validated."
            ),
        )

    # Ensure timezone-aware comparisons
    init_time: datetime = _ensure_utc(record.forecast_init_time)
    valid_time: datetime | None = (
        _ensure_utc(record.forecast_valid_time)
        if record.forecast_valid_time is not None
        else None
    )
    retrieval_time: datetime = _ensure_utc(record.retrieval_timestamp)

    # ── Rule 2: FUTURE_INIT_TIME ──────────────────────────────────────────
    if init_time > retrieval_time:
        return LookaheadResult(
            is_valid=False,
            rejection_reason=RejectionReason.FUTURE_INIT_TIME,
            flags=[RejectionReason.FUTURE_INIT_TIME],
            notes=(
                f"[{record.city}] forecast_init_time ({init_time.isoformat()}) "
                f"is AFTER retrieval_timestamp ({retrieval_time.isoformat()}).  "
                "This is a data error."
            ),
        )

    # ── Rule 3: REANALYSIS_NOT_ALLOWED ────────────────────────────────────
    if record.is_reanalysis:
        return LookaheadResult(
            is_valid=False,
            rejection_reason=RejectionReason.REANALYSIS_NOT_ALLOWED,
            flags=[RejectionReason.REANALYSIS_NOT_ALLOWED],
            notes=(
                f"[{record.city}] Provider '{record.provider}' returned a "
                "reanalysis record (is_reanalysis=True).  Reanalysis is "
                "constructed after the fact and cannot be used for V3 training."
            ),
        )

    # ── Rule 4: LOOKAHEAD_VIOLATION ───────────────────────────────────────
    if valid_time is not None:
        # The forecast must have been initialized BEFORE the valid time,
        # offset back by lead_time, with tolerance.
        # Derived from:
        #   init_time <= valid_time - lead_time_hours + tolerance
        from datetime import timedelta
        max_allowed_init = valid_time - timedelta(hours=record.lead_time_hours - tolerance_h)
        if init_time > max_allowed_init:
            lag_hours = (init_time - max_allowed_init).total_seconds() / 3600
            return LookaheadResult(
                is_valid=False,
                rejection_reason=RejectionReason.LOOKAHEAD_VIOLATION,
                flags=[RejectionReason.LOOKAHEAD_VIOLATION],
                notes=(
                    f"[{record.city}] Look-ahead violation: "
                    f"forecast_init_time ({init_time.isoformat()}) is "
                    f"{lag_hours:.1f}h too recent for a {record.lead_time_hours}h lead "
                    f"to valid_time ({valid_time.isoformat()}).  "
                    f"Max allowed init: {max_allowed_init.isoformat()}."
                ),
            )

    # ── Rule 5: VALID_TIME_INCONSISTENCY ─────────────────────────────────
    if valid_time is not None and valid_time < init_time:
        return LookaheadResult(
            is_valid=False,
            rejection_reason=RejectionReason.VALID_TIME_INCONSISTENCY,
            flags=[RejectionReason.VALID_TIME_INCONSISTENCY],
            notes=(
                f"[{record.city}] forecast_valid_time ({valid_time.isoformat()}) "
                f"is before forecast_init_time ({init_time.isoformat()}).  "
                "Data error."
            ),
        )

    # ── Rule 6 (soft): MISSING_FORECAST_VALUE ────────────────────────────
    if record.forecast_tmax_raw is None:
        flags.append(RejectionReason.MISSING_FORECAST_VALUE)

    return LookaheadResult(
        is_valid=True,
        rejection_reason=None,
        flags=flags,
        notes=None,
    )


def _ensure_utc(dt: datetime) -> datetime:
    """Return dt as a UTC-aware datetime."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
