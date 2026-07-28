"""
Settlement contract parser for Kalshi weather markets.

Supported market types:
    - High/low temperature >= X°F  (threshold contract)
    - High/low temperature <= X°F  (threshold contract)
    - High/low temperature in [lo, hi]°F  (range contract)
    - Hourly temperature above/below X°F at a specific time  (hourly_threshold)

Title conventions observed on Kalshi:
    ">94°"  + subtitle "95° or above"      → resolves YES if daily high >= 95°F
    "<87°"  + subtitle "86° or below"      → resolves YES if daily high <= 86°F
    "93-94°"+ subtitle "93° to 94°"        → range bet, YES if 93°F <= T <= 94°F
    "above 70.99° at 12am EDT"             → hourly bet, YES if temp > 70.99°F at midnight EDT

Contract types:
    threshold         : simple >= / <= against a daily high or low
    range             : P(lo ≤ T ≤ hi) via Gaussian CDF difference
    hourly_threshold  : >= / <= against a specific hour's temperature

When a subtitle is present and matches a known pattern the parse_confidence
is "high". When the parser falls back to the title the confidence is "medium".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

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

# Hourly market patterns
_HOURLY_INDICATOR = re.compile(r"\bat\s+\d{1,2}[ap]m\b", re.IGNORECASE)
_HOURLY_TIME = re.compile(r"\bat\s+(\d{1,2})(am|pm)\s+([A-Za-z]{2,4})\b", re.IGNORECASE)
_TITLE_ABOVE = re.compile(r"\babove\s+(\d+(?:\.\d+)?)\s*[°℉]", re.IGNORECASE)
_TITLE_BELOW = re.compile(r"\bbelow\s+(\d+(?:\.\d+)?)\s*[°℉]", re.IGNORECASE)

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
    variable         : 'high' | 'low' | 'hourly_temperature' | None
    operator         : 'gte' | 'lte' | None  (None for range contracts)
    threshold        : float temperature in °F, or None  (None for range contracts)
    unit             : always 'F' for Kalshi US weather markets
    parse_confidence : 'high' (subtitle match) | 'medium' (title fallback) | 'low'
    unsupported_reason: human-readable explanation when status != 'supported'

    Phase 2B fields (all optional, default None / 'threshold'):
    contract_type     : 'threshold' | 'range' | 'hourly_threshold'
    target_hour       : 0–23 (local time in target_timezone_str) for hourly contracts
    target_timezone_str: e.g. 'EDT', 'CDT' for hourly contracts
    lower_bound       : lower temperature bound for range contracts
    upper_bound       : upper temperature bound for range contracts
    """

    status: str
    variable: str | None
    operator: str | None
    threshold: float | None
    unit: str
    parse_confidence: str
    unsupported_reason: str | None

    # Phase 2B
    contract_type: str = "threshold"
    target_hour: int | None = None
    target_timezone_str: str | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_settlement(title: str, subtitle: str | None) -> SettlementContract:
    """
    Parse a Kalshi market title/subtitle into a SettlementContract.

    Precedence:
        0. Hourly market detection (takes priority — subtitles can be misleading)
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

    # ---- Hourly market detection (step 0) ----------------------------------
    # Must check BEFORE subtitle because hourly markets often have misleading
    # subtitles (e.g. "-1° or below" is a price-change floor, not a temp threshold).
    if _is_hourly_market(title):
        return _parse_hourly_contract(title)

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
                contract_type="threshold",
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
                contract_type="threshold",
            )

        m = _SUB_RANGE.match(s)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            return SettlementContract(
                status="supported",
                variable=variable,
                operator=None,
                threshold=None,
                unit="F",
                parse_confidence="high",
                unsupported_reason=None,
                contract_type="range",
                lower_bound=lo,
                upper_bound=hi,
            )

    # ---- Title path (medium confidence) ------------------------------------
    # Range check first — it's the most common and must not be mistaken for ≥/≤.
    m = _TITLE_RANGE.search(title)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return SettlementContract(
            status="supported",
            variable=variable,
            operator=None,
            threshold=None,
            unit="F",
            parse_confidence="medium",
            unsupported_reason=None,
            contract_type="range",
            lower_bound=lo,
            upper_bound=hi,
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
            contract_type="threshold",
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
            contract_type="threshold",
        )

    # ---- Nothing matched ---------------------------------------------------
    if variable is None:
        reason = "Could not identify temperature variable (high/low) or comparison operator in title or subtitle"
    else:
        reason = f"Could not extract comparison operator and threshold for {variable} temperature from title or subtitle"

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
# Hourly market parsing
# ---------------------------------------------------------------------------

def _is_hourly_market(title: str) -> bool:
    """Return True if the title specifies a particular hour (hourly contract)."""
    return bool(_HOURLY_INDICATOR.search(title))


def _parse_hour_12(hour: int, meridiem: str) -> int:
    """Convert 12-hour clock to 0–23."""
    h = int(hour)
    if meridiem.lower() == "am":
        return 0 if h == 12 else h
    else:  # pm
        return h if h == 12 else h + 12


def _parse_hourly_contract(title: str) -> SettlementContract:
    """
    Parse an hourly temperature contract from the title.
    Returns a SettlementContract with contract_type='hourly_threshold'.
    """
    # Detect operator and threshold
    m_above = _TITLE_ABOVE.search(title)
    m_below = _TITLE_BELOW.search(title)

    if m_above:
        operator = "gte"
        threshold = float(m_above.group(1))
        confidence = "high"
    elif m_below:
        operator = "lte"
        threshold = float(m_below.group(1))
        confidence = "high"
    else:
        return SettlementContract(
            status="unsupported",
            variable=None,
            operator=None,
            threshold=None,
            unit="F",
            parse_confidence="low",
            unsupported_reason=(
                "Hourly market detected (contains time specification) but could not "
                "extract operator ('above'/'below') or threshold temperature from title"
            ),
            contract_type="hourly_threshold",
        )

    # Detect time and timezone
    m_time = _HOURLY_TIME.search(title)
    if not m_time:
        return SettlementContract(
            status="unsupported",
            variable="hourly_temperature",
            operator=operator,
            threshold=threshold,
            unit="F",
            parse_confidence="medium",
            unsupported_reason=(
                "Hourly settlement time not found in title "
                "(expected format: 'at X am/pm TZ', e.g. 'at 12am EDT')"
            ),
            contract_type="hourly_threshold",
        )

    hour_12 = int(m_time.group(1))
    meridiem = m_time.group(2)
    tz_abbrev = m_time.group(3).upper()
    target_hour = _parse_hour_12(hour_12, meridiem)

    return SettlementContract(
        status="supported",
        variable="hourly_temperature",
        operator=operator,
        threshold=threshold,
        unit="F",
        parse_confidence=confidence,
        unsupported_reason=None,
        contract_type="hourly_threshold",
        target_hour=target_hour,
        target_timezone_str=tz_abbrev,
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
