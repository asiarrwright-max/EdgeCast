"""
READ-ONLY audit check runner.
==============================
Runs the three deferred DB verification checks identified in the final audit
and stores results in audit_check_results.  Does NOT modify any trading,
calibration, or historical data.

Checks:
  A. db_calibration_contents      — calibration_adjustments table contents
  B. db_date_alignment            — target_settlement_date UTC vs local-date
  C. db_coord_alignment           — WeatherLocation vs settlement-station coords
"""
from __future__ import annotations

import logging
import math
import zoneinfo
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditCheckResult, WeatherLocation
from app.services.settlement_stations import SETTLEMENT_STATIONS
from app.services.kalshi import SERIES_TO_CITY

logger = logging.getLogger(__name__)

FORWARD_TEST_START = datetime(2026, 8, 4, 22, 21, 44, tzinfo=timezone.utc)
COORD_MISMATCH_THRESHOLD_MILES = 1.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _series_city_coords() -> dict[str, tuple[float, float]]:
    """Return city → (lat, lon) from SERIES_TO_CITY, first entry per city."""
    result: dict[str, tuple[float, float]] = {}
    for entry in SERIES_TO_CITY.values():
        if entry is not None:
            city, lat, lon = entry
            if city not in result:
                result[city] = (lat, lon)
    return result


def _parse_settlement_date(raw: str) -> tuple[str, bool]:
    """
    Return (date_str_as_used_by_verifier, has_time_component).
    date_str = raw[:10] — exactly what forecast_verifier.py does.
    has_time_component = True when raw contains a 'T' separator.
    """
    date_str = raw[:10] if raw else ""
    has_time = "T" in raw or "t" in raw
    return date_str, has_time


def _utc_to_local_date(utc_str: str, tz_name: str) -> str | None:
    """Convert a UTC ISO string to a local date string using tz_name."""
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(zoneinfo.ZoneInfo(tz_name))
        return local.date().isoformat()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Check A — calibration_adjustments contents
# ---------------------------------------------------------------------------

async def _run_calibration_check(db: AsyncSession) -> AuditCheckResult:
    rows = (await db.execute(
        text(
            "SELECT strategy_version, bucket_lo, bucket_hi, predicted_rate, "
            "actual_yes_rate, adjustment_factor, sample_size, computed_at "
            "FROM calibration_adjustments ORDER BY strategy_version, bucket_lo"
        )
    )).fetchall()

    total_rows = len(rows)
    v20_rows = [r for r in rows if r.strategy_version == "v2.0"]
    active_rows = [
        r for r in v20_rows
        if (r.sample_size or 0) >= 30 and r.adjustment_factor is not None
    ]

    row_detail: list[dict[str, Any]] = [
        {
            "strategy_version": r.strategy_version,
            "bucket_lo": r.bucket_lo,
            "bucket_hi": r.bucket_hi,
            "predicted_rate": r.predicted_rate,
            "actual_yes_rate": r.actual_yes_rate,
            "adjustment_factor": r.adjustment_factor,
            "sample_size": r.sample_size,
            "computed_at": r.computed_at.isoformat() if r.computed_at else None,
        }
        for r in rows
    ]

    calibration_active = bool(active_rows)
    detail_lines: list[str] = [
        f"Total rows in calibration_adjustments: {total_rows}",
        f"Rows with strategy_version='v2.0': {len(v20_rows)}",
        f"Active rows (sample_size≥30, factor not null): {len(active_rows)}",
    ]
    if active_rows:
        detail_lines.append("\nActive row details:")
        for r in active_rows:
            detail_lines.append(
                f"  bucket [{r.bucket_lo:.2f}, {r.bucket_hi:.2f}): "
                f"factor={r.adjustment_factor:.4f}, "
                f"sample_size={r.sample_size}, "
                f"computed_at={r.computed_at}"
            )
    detail_lines.append(
        f"\nV2.2 calibration is currently "
        f"{'ACTIVE — calib_adj ≠ 1.0 for matched buckets' if calibration_active else 'BYPASSED — calib_adj = 1.0 (no qualifying rows)'}."
    )
    if total_rows == 0:
        detail_lines.append(
            "No rows exist. Safe to proceed with v2.3 — calibration will be "
            "bypassed by design until clean forward-test data accumulates."
        )
    elif not calibration_active:
        detail_lines.append(
            f"{total_rows} row(s) exist but none meet sample_size≥30. "
            "Calibration is effectively bypassed. Safe to proceed."
        )
    else:
        detail_lines.append(
            "These factors were derived before the rounding correction and must "
            "be cleared before deploying the corrected formula. Starting the "
            "corrected model as strategy_version='v2.3' bypasses them automatically "
            "because no v2.3 rows exist — but the v2.0 rows should still be "
            "documented and cleared for hygiene."
        )

    if total_rows == 0:
        status, action_required = "CLEARED", False
        summary = (
            "No calibration rows exist. V2.2 calibration is bypassed (calib_adj=1.0). "
            "Safe to proceed — v2.3 will also have no matching rows."
        )
    elif not calibration_active:
        status, action_required = "CLEARED", False
        summary = (
            f"{total_rows} row(s) present but none active (sample_size<30). "
            "V2.2 calibration is currently bypassed (calib_adj=1.0)."
        )
    else:
        status, action_required = "FIX_REQUIRED", True
        summary = (
            f"{len(active_rows)} active calibration row(s) detected for "
            "strategy_version='v2.0'. These factors pre-date the rounding correction "
            "and should be cleared before any refit."
        )

    return AuditCheckResult(
        check_key="db_calibration_contents",
        check_name="Calibration Adjustments Contents",
        category="calibration",
        status=status,
        severity="HIGH",
        summary=summary,
        details="\n".join(detail_lines),
        action_required=action_required,
        checked_at=datetime.now(timezone.utc),
        source="audit_checks_service",
        metadata_json={
            "total_rows": total_rows,
            "v20_rows": len(v20_rows),
            "active_rows": len(active_rows),
            "calibration_active": calibration_active,
            "row_detail": row_detail,
        },
    )


# ---------------------------------------------------------------------------
# Check B — target_settlement_date UTC vs local-date alignment
# ---------------------------------------------------------------------------

async def _run_date_alignment_check(db: AsyncSession) -> AuditCheckResult:
    rows = (await db.execute(
        text(
            "SELECT market_ticker, city, target_settlement_date, "
            "settlement_timezone, eligibility_status "
            "FROM paper_trades "
            "WHERE eligibility_status = 'OFFICIAL' "
            "  AND created_at >= :fts "
            "ORDER BY created_at",
        ),
        {"fts": FORWARD_TEST_START},
    )).fetchall()

    total = len(rows)
    mismatches: list[dict[str, Any]] = []
    date_only_count = 0
    no_tz_count = 0
    parse_errors: list[str] = []

    for r in rows:
        raw = r.target_settlement_date or ""
        if not raw:
            parse_errors.append(f"{r.market_ticker}: empty target_settlement_date")
            continue

        date_str, has_time = _parse_settlement_date(raw)

        if not has_time:
            date_only_count += 1
            continue  # date-only strings have no UTC/local issue

        # Determine timezone: prefer stored field, fall back to SETTLEMENT_STATIONS
        tz_name: str | None = r.settlement_timezone
        if not tz_name and r.city and r.city in SETTLEMENT_STATIONS:
            tz_name = SETTLEMENT_STATIONS[r.city].timezone

        if not tz_name:
            no_tz_count += 1
            continue

        local_date = _utc_to_local_date(raw, tz_name)
        if local_date is None:
            parse_errors.append(f"{r.market_ticker}: failed to parse '{raw}'")
            continue

        if date_str != local_date:
            mismatches.append({
                "ticker": r.market_ticker,
                "city": r.city,
                "target_settlement_date": raw,
                "utc_date_used": date_str,
                "local_date": local_date,
                "timezone": tz_name,
                "off_by_days": (
                    (
                        datetime.fromisoformat(date_str) -
                        datetime.fromisoformat(local_date)
                    ).days
                ),
            })

    checkable = total - date_only_count - no_tz_count - len(parse_errors)
    mismatch_count = len(mismatches)

    detail_lines = [
        f"OFFICIAL forward-test trades examined: {total}",
        f"  Date-only strings (no time component — no UTC issue possible): {date_only_count}",
        f"  Checkable (full UTC timestamps with known timezone): {checkable}",
        f"  No timezone available (skipped): {no_tz_count}",
        f"  Parse errors: {len(parse_errors)}",
        f"  UTC date ≠ local station date (mismatches): {mismatch_count}",
    ]
    if mismatches:
        detail_lines.append("\nMismatched trades:")
        for m in mismatches:
            detail_lines.append(
                f"  {m['ticker']} ({m['city']}): "
                f"UTC date used = {m['utc_date_used']}, "
                f"correct local date = {m['local_date']} "
                f"(off by {m['off_by_days']} day(s), tz={m['timezone']})"
            )
        detail_lines.append(
            "\nImpact: ERA5/GHCND verification for these trades fetched "
            "temperatures from the wrong day. σ/bias statistics fed back "
            "into the probability engine are corrupted for these cities/dates."
        )
    elif date_only_count == total:
        detail_lines.append(
            "\ntarget_settlement_date is stored as a date-only string (no time component). "
            "The UTC/local mismatch cannot occur with this storage format. "
            "The suspected issue is NOT present."
        )
    else:
        detail_lines.append(
            "\nAll checkable trades use the correct local settlement date. "
            "No UTC/local mismatch detected."
        )

    if parse_errors:
        detail_lines.append(f"\nParse errors: {'; '.join(parse_errors)}")

    if mismatch_count > 0:
        status, action_required = "FIX_REQUIRED", True
        summary = (
            f"{mismatch_count} of {total} OFFICIAL trades used a UTC calendar date "
            f"that differed from the station-local settlement date. "
            f"ERA5 verification data for these trades is from the wrong day."
        )
    elif date_only_count == total:
        status, action_required = "CLEARED", False
        summary = (
            f"All {total} OFFICIAL trades store target_settlement_date as a date-only "
            f"string. The UTC/local mismatch issue does not apply to this storage format."
        )
    elif total == 0:
        status, action_required = "CLEARED", False
        summary = "No OFFICIAL forward-test trades found. Check will re-run as trades accumulate."
    else:
        status, action_required = "CLEARED", False
        summary = (
            f"Checked {checkable} of {total} OFFICIAL trades. "
            f"No UTC/local date mismatches detected."
        )

    return AuditCheckResult(
        check_key="db_date_alignment",
        check_name="Target Settlement Date Local-Date Alignment",
        category="era5_verification",
        status=status,
        severity="CRITICAL",
        summary=summary,
        details="\n".join(detail_lines),
        action_required=action_required,
        checked_at=datetime.now(timezone.utc),
        source="audit_checks_service",
        metadata_json={
            "total_official_trades": total,
            "date_only_count": date_only_count,
            "checkable_count": checkable,
            "no_tz_count": no_tz_count,
            "mismatch_count": mismatch_count,
            "parse_error_count": len(parse_errors),
            "mismatches": mismatches,
        },
    )


# ---------------------------------------------------------------------------
# Check C — WeatherLocation vs settlement-station coordinates
# ---------------------------------------------------------------------------

async def _run_coord_alignment_check(db: AsyncSession) -> AuditCheckResult:
    from sqlalchemy import select
    wl_rows = (await db.execute(select(WeatherLocation))).scalars().all()

    series_coords = _series_city_coords()
    city_results: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []

    for wl in wl_rows:
        city = wl.city
        station = SETTLEMENT_STATIONS.get(city)
        series_coord = series_coords.get(city)

        # Distance: WeatherLocation vs settlement station
        wl_vs_station: float | None = None
        if station:
            wl_vs_station = _haversine_miles(
                wl.latitude, wl.longitude, station.lat, station.lon
            )

        # Distance: SERIES_TO_CITY vs settlement station (for cross-check)
        series_vs_station: float | None = None
        if station and series_coord:
            series_vs_station = _haversine_miles(
                series_coord[0], series_coord[1], station.lat, station.lon
            )

        # Are WeatherLocation and SERIES_TO_CITY aligned?
        wl_vs_series: float | None = None
        if series_coord:
            wl_vs_series = _haversine_miles(
                wl.latitude, wl.longitude, series_coord[0], series_coord[1]
            )

        entry: dict[str, Any] = {
            "city": city,
            "wl_lat": wl.latitude,
            "wl_lon": wl.longitude,
            "series_lat": series_coord[0] if series_coord else None,
            "series_lon": series_coord[1] if series_coord else None,
            "station_lat": station.lat if station else None,
            "station_lon": station.lon if station else None,
            "station_verified": station.verified if station else None,
            "wl_vs_station_miles": round(wl_vs_station, 2) if wl_vs_station is not None else None,
            "series_vs_station_miles": round(series_vs_station, 2) if series_vs_station is not None else None,
            "wl_vs_series_miles": round(wl_vs_series, 3) if wl_vs_series is not None else None,
        }
        city_results.append(entry)

        # Flag as mismatch if WeatherLocation differs materially from settlement station
        if wl_vs_station is not None and wl_vs_station > COORD_MISMATCH_THRESHOLD_MILES:
            entry["mismatch"] = True
            mismatches.append(entry)
        else:
            entry["mismatch"] = False

    mismatch_count = len(mismatches)
    detail_lines = [
        f"WeatherLocation rows examined: {len(wl_rows)}",
        f"Cities with settlement station data: {sum(1 for e in city_results if e['station_lat'] is not None)}",
        f"Cities with WeatherLocation coords > {COORD_MISMATCH_THRESHOLD_MILES} mile from settlement station: {mismatch_count}",
        "",
    ]
    for e in sorted(city_results, key=lambda x: (x.get("wl_vs_station_miles") or 0), reverse=True):
        if e["station_lat"] is not None:
            detail_lines.append(
                f"  {e['city']}: WL→station {e['wl_vs_station_miles']} mi, "
                f"SERIES→station {e['series_vs_station_miles']} mi, "
                f"WL≈SERIES {e['wl_vs_series_miles']} mi "
                f"(station verified={e['station_verified']}) "
                f"{'⚠ MISMATCH' if e.get('mismatch') else '✓'}"
            )

    if mismatch_count > 0:
        detail_lines.append(
            f"\n{mismatch_count} city/cities have WeatherLocation coordinates that differ "
            f"materially from the known settlement station. ERA5 verification and forecast "
            f"use different grid points for these cities. The WeatherLocation rows should "
            f"be updated to match SERIES_TO_CITY station coordinates, which should in turn "
            f"be corrected to match settlement station coordinates."
        )

    if mismatch_count == 0:
        status, action_required = "CLEARED", False
        summary = (
            f"All {len(wl_rows)} WeatherLocation rows are within "
            f"{COORD_MISMATCH_THRESHOLD_MILES} mile of their settlement station. "
            "No material coordinate mismatch between forecast and ERA5 verification."
        )
    else:
        status, action_required = "FIX_REQUIRED", True
        city_names = ", ".join(e["city"] for e in mismatches)
        summary = (
            f"{mismatch_count} city/cities have WeatherLocation coords > "
            f"{COORD_MISMATCH_THRESHOLD_MILES} mile from settlement station: {city_names}. "
            "Forecast and ERA5 verification may use different grid cells."
        )

    return AuditCheckResult(
        check_key="db_coord_alignment",
        check_name="WeatherLocation vs Settlement-Station Coordinates",
        category="coordinates",
        status=status,
        severity="MODERATE",
        summary=summary,
        details="\n".join(detail_lines),
        action_required=action_required,
        checked_at=datetime.now(timezone.utc),
        source="audit_checks_service",
        metadata_json={
            "cities_checked": len(wl_rows),
            "mismatch_count": mismatch_count,
            "threshold_miles": COORD_MISMATCH_THRESHOLD_MILES,
            "city_results": city_results,
        },
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_all_audit_checks(db: AsyncSession) -> list[AuditCheckResult]:
    """
    Run all three DB verification checks and persist results.
    READ-ONLY: does not modify any trading, calibration, or historical data.
    Returns the list of AuditCheckResult objects written.
    """
    results: list[AuditCheckResult] = []
    checks = [
        ("db_calibration_contents", _run_calibration_check),
        ("db_date_alignment", _run_date_alignment_check),
        ("db_coord_alignment", _run_coord_alignment_check),
    ]
    for key, fn in checks:
        try:
            result = await fn(db)
            db.add(result)
            results.append(result)
            logger.info("Audit check %s → %s", key, result.status)
        except Exception as exc:
            logger.error("Audit check %s failed: %s", key, exc, exc_info=True)
    return results
