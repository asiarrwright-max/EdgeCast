"""
Settlement contract parser for Kalshi weather markets.

Supported market types:
    - High temperature >= X°F  (title ">X°" or subtitle "X° or above")
    - High temperature <= X°F  (title "<X°" or subtitle "X° or below")
    - Low  temperature >= X°F
    - Low  temperature <= X°F

All other structures (range bets, precipitation, wind, etc.) return
status='unsupported' with an explanation.

Title conventions observed on Kalshi:
    ">94°"  + subtitle "95° or above"  → resolves YES if temp >= 95°F
    "<87°"  + subtitle "86° or below"  → resolves YES if temp <= 86°F
    "93-94°"+ subtitle "93° to 94°"    → range bet, unsupported

When a subtitle is present and matches a known pattern the parse_confidence
is "high". When the parser falls back to the title the confidence is "medium".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Subtitle patterns (highest confidence)
_SUB_GTE = re.compile(r"^(\d+(?:\.\d+)?)\s*[°℉]?\s*or\s+above$", re.IGNORECASE)
_SUB_LTE = re.compile(r"^(\d+(?:\.\d+)?)\s*[°℉]?\s*or\s+below$", re.IGNORECASE)
_SUB_RANGE = re.compile(
    r"^(\d+(?:\.\d+)?)\s*[°℉]?\s*to\s*(\d+(?:\.\d+)?)\s*[°℉]?$", re.IGNORECASE
)

# Title patterns (medium confidence)
_TITLE_GT = re.compile(r">(\d+(?:\.\d+)?)\s*[°℉]", re.IGNORECASE)
_TITLE_LT = re.compile(r"<(\d+(?:\.\d+)?)\s*[°℉]", re.IGNORECASE)
_TITLE_RANGE = re.compile(
    r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*[°℉]", re.IGNORECASE
)

# Variable detection
_HIGH = re.compile(r"\b(?:high|maximum|max)\b", re.IGNORECASE)
_LOW = re.compile(r"\b(?:low|minimum|min)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Contract dataclass
# ---------------------------------------------------------------------------

@dataclass
class SettlementContract:
    """
    Structured representation of what a market is betting on.

    status           : 'supported' | 'unsupported' | 'no_data'
    variable         : 'high' | 'low' | None
    operator         : 'gte' | 'lte' | None
    threshold        : float temperature in °F, or None
    unit             : always 'F' for Kalshi US weather markets
    parse_confidence : 'high' (subtitle match) | 'medium' (title fallback) | 'low'
    unsupported_reason: human-readable explanation when status != 'supported'
    """

    status: str
    variable: str | None
    operator: str | None
    threshold: float | None
    unit: str
    parse_confidence: str
    unsupported_reason: str | None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_settlement(title: str, subtitle: str | None) -> SettlementContract:
    """
    Parse a Kalshi market title/subtitle into a SettlementContract.

    Precedence:
        1. Subtitle: 'X° or above' / 'X° or below' / 'X° to Y°'
        2. Title:    '>X°' / '<X°' / 'X-Y°'
        3. Unsupported — explain why
    """
    if not title:
        return SettlementContract(
            status="no_data",
            variable=None,
            operator=None,
            threshold=None,
            unit="F",
            parse_confidence="low",
            unsupported_reason="Market title is empty",
        )

    variable = _detect_variable(title)

    # ---- Subtitle path (high confidence) -----------------------------------
    if subtitle:
        s = subtitle.strip()

        m = _SUB_GTE.match(s)
        if m:
            return SettlementContract(
                status="supported",
                variable=variable,
                operator="gte",
                threshold=float(m.group(1)),
                unit="F",
                parse_confidence="high",
                unsupported_reason=None,
            )

        m = _SUB_LTE.match(s)
        if m:
            return SettlementContract(
                status="supported",
                variable=variable,
                operator="lte",
                threshold=float(m.group(1)),
                unit="F",
                parse_confidence="high",
                unsupported_reason=None,
            )

        m = _SUB_RANGE.match(s)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            return SettlementContract(
                status="unsupported",
                variable=variable,
                operator=None,
                threshold=None,
                unit="F",
                parse_confidence="high",
                unsupported_reason=(
                    f"Range market ({lo:.0f}°–{hi:.0f}°F): "
                    "probability for range bets is not yet supported"
                ),
            )

    # ---- Title path (medium confidence) ------------------------------------
    # Range check first — it's the most common and must not be mistaken for ≥/≤.
    m = _TITLE_RANGE.search(title)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return SettlementContract(
            status="unsupported",
            variable=variable,
            operator=None,
            threshold=None,
            unit="F",
            parse_confidence="medium",
            unsupported_reason=(
                f"Range market ({lo:.0f}°–{hi:.0f}°F): "
                "probability for range bets is not yet supported"
            ),
        )

    m = _TITLE_GT.search(title)
    if m:
        # Kalshi ">X°" resolves YES if temp > X, which for a continuous
        # Gaussian model is equivalent to temp >= X (P(T=X) = 0).
        return SettlementContract(
            status="supported",
            variable=variable,
            operator="gte",
            threshold=float(m.group(1)),
            unit="F",
            parse_confidence="medium",
            unsupported_reason=None,
        )

    m = _TITLE_LT.search(title)
    if m:
        return SettlementContract(
            status="supported",
            variable=variable,
            operator="lte",
            threshold=float(m.group(1)),
            unit="F",
            parse_confidence="medium",
            unsupported_reason=None,
        )

    # ---- Nothing matched ---------------------------------------------------
    if variable is None:
        reason = "Could not determine temperature variable (high/low) or comparison operator"
    else:
        reason = "Could not extract comparison operator and threshold from title or subtitle"

    return SettlementContract(
        status="unsupported",
        variable=variable,
        operator=None,
        threshold=None,
        unit="F",
        parse_confidence="low",
        unsupported_reason=reason,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_variable(title: str) -> str | None:
    """Return 'high', 'low', or None based on keywords in the title."""
    is_high = bool(_HIGH.search(title))
    is_low = bool(_LOW.search(title))

    if is_high and not is_low:
        return "high"
    if is_low and not is_high:
        return "low"
    if is_high and is_low:
        # Both present (rare). Use position of first match.
        high_pos = min(
            (title.lower().find(w) for w in ("high", "max", "maximum") if w in title.lower()),
            default=999,
        )
        low_pos = min(
            (title.lower().find(w) for w in ("low", "min", "minimum") if w in title.lower()),
            default=999,
        )
        return "high" if high_pos < low_pos else "low"
    return None
