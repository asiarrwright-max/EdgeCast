"""
tests/test_forward_evidence_reconciliation.py
Tests for forward-evidence completeness and reconciliation diagnostics
(app/routers/forward_evidence_reconciliation.py).

Coverage
--------
• Population separation: OFFICIAL / RESEARCH_ONLY / LEGACY / UNCLASSIFIED
  rows are placed into their own buckets and never pooled.
• NULL eligibility_status maps to UNCLASSIFIED.
• Unknown/unexpected eligibility_status values map to UNCLASSIFIED.
• Lifecycle counts are correct.
• Settlement coverage percentage is computed correctly.
• Missing entry-price count uses only stored side_market_price.
• Stale/missing quote counts use only stored quote fields.
• Integrity exception count uses only stored quality_flags.
• Date-range helpers return None gracefully when fields are absent.
• Funnel narrative emits correct plain-language messages.
• Strategy breakdown groups by strategy_version.
• Safety invariants: _build_population_block and the pure helpers
  never set trading_state_modified or real_money_execution_enabled.
• Fail-closed: empty trade list returns safe zero/None values.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.routers.forward_evidence_reconciliation import (
    _POP_LEGACY,
    _POP_OFFICIAL,
    _POP_RESEARCH,
    _POP_UNCLASSIFIED,
    _build_population_block,
    _date_range,
    _funnel_narrative,
    _integrity_exception_count,
    _lifecycle_counts,
    _missing_entry_price_count,
    _population_key,
    _stale_or_missing_quote_count,
    _strategy_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


_UNSET = object()  # Sentinel distinguishing "not provided" from explicit None


def _make_trade(
    *,
    eligibility_status: str | None = "OFFICIAL",
    status: str = "SETTLED",
    strategy_version: str = "v2.3",
    side_market_price: float | None = 0.55,
    quote_timestamp: object = _UNSET,   # None = explicitly missing; _UNSET = use _now()
    quote_age_seconds: float | None = 60.0,
    quality_flags: list | None = None,
    target_settlement_date: str | None = "2026-01-15",
    created_at: datetime | None = None,
) -> MagicMock:
    t = MagicMock()
    t.eligibility_status = eligibility_status
    t.status = status
    t.strategy_version = strategy_version
    t.side_market_price = side_market_price
    t.quote_timestamp = _now() if quote_timestamp is _UNSET else quote_timestamp
    t.quote_age_seconds = quote_age_seconds
    t.quality_flags = quality_flags or []
    t.target_settlement_date = target_settlement_date
    t.created_at = created_at or _now()
    return t


# ---------------------------------------------------------------------------
# _population_key
# ---------------------------------------------------------------------------

class TestPopulationKey:
    def test_official(self):
        assert _population_key("OFFICIAL") == _POP_OFFICIAL

    def test_research_only(self):
        assert _population_key("RESEARCH_ONLY") == _POP_RESEARCH

    def test_legacy(self):
        assert _population_key("LEGACY") == _POP_LEGACY

    def test_null_maps_to_unclassified(self):
        assert _population_key(None) == _POP_UNCLASSIFIED

    def test_unknown_value_maps_to_unclassified(self):
        assert _population_key("SOME_NEW_STATUS") == _POP_UNCLASSIFIED

    def test_empty_string_maps_to_unclassified(self):
        assert _population_key("") == _POP_UNCLASSIFIED

    def test_lowercase_is_accepted(self):
        # Should normalise to uppercase
        assert _population_key("official") == _POP_OFFICIAL

    def test_whitespace_stripped(self):
        assert _population_key("  OFFICIAL  ") == _POP_OFFICIAL


# ---------------------------------------------------------------------------
# Population separation — populations must never be pooled
# ---------------------------------------------------------------------------

class TestPopulationSeparation:
    """
    Verify that trades with different eligibility_status values end up in
    strictly separate population buckets when the caller partitions them
    (as the endpoint does).
    """

    def _partition(self, trades):
        from collections import defaultdict
        buckets = defaultdict(list)
        for t in trades:
            buckets[_population_key(t.eligibility_status)].append(t)
        return dict(buckets)

    def test_four_populations_are_separate(self):
        trades = [
            _make_trade(eligibility_status="OFFICIAL"),
            _make_trade(eligibility_status="RESEARCH_ONLY"),
            _make_trade(eligibility_status="LEGACY"),
            _make_trade(eligibility_status=None),
        ]
        buckets = self._partition(trades)
        assert len(buckets[_POP_OFFICIAL]) == 1
        assert len(buckets[_POP_RESEARCH]) == 1
        assert len(buckets[_POP_LEGACY]) == 1
        assert len(buckets[_POP_UNCLASSIFIED]) == 1

    def test_official_block_excludes_research_trades(self):
        official = _make_trade(eligibility_status="OFFICIAL")
        research = _make_trade(eligibility_status="RESEARCH_ONLY")
        buckets = self._partition([official, research])
        assert research not in buckets.get(_POP_OFFICIAL, [])
        assert official not in buckets.get(_POP_RESEARCH, [])

    def test_all_official_in_official_bucket(self):
        trades = [_make_trade(eligibility_status="OFFICIAL") for _ in range(5)]
        buckets = self._partition(trades)
        assert len(buckets[_POP_OFFICIAL]) == 5
        assert buckets.get(_POP_RESEARCH, []) == []
        assert buckets.get(_POP_LEGACY, []) == []
        assert buckets.get(_POP_UNCLASSIFIED, []) == []


# ---------------------------------------------------------------------------
# _lifecycle_counts
# ---------------------------------------------------------------------------

class TestLifecycleCounts:
    def test_settled_counted(self):
        trades = [_make_trade(status="SETTLED"), _make_trade(status="SETTLED")]
        counts = _lifecycle_counts(trades)
        assert counts["SETTLED"] == 2

    def test_open_counted(self):
        trades = [_make_trade(status="OPEN")]
        counts = _lifecycle_counts(trades)
        assert counts["OPEN"] == 1

    def test_void_counted(self):
        trades = [_make_trade(status="VOID")]
        counts = _lifecycle_counts(trades)
        assert counts["VOID"] == 1

    def test_pending_counted(self):
        trades = [_make_trade(status="PENDING_SETTLEMENT")]
        counts = _lifecycle_counts(trades)
        assert counts["PENDING_SETTLEMENT"] == 1

    def test_unknown_status_lands_in_other(self):
        trades = [_make_trade(status="MYSTERY")]
        counts = _lifecycle_counts(trades)
        assert counts["OTHER"] == 1

    def test_empty_returns_zeros(self):
        counts = _lifecycle_counts([])
        assert all(v == 0 for v in counts.values())

    def test_mixed_statuses(self):
        trades = [
            _make_trade(status="SETTLED"),
            _make_trade(status="OPEN"),
            _make_trade(status="OPEN"),
            _make_trade(status="VOID"),
        ]
        counts = _lifecycle_counts(trades)
        assert counts["SETTLED"] == 1
        assert counts["OPEN"] == 2
        assert counts["VOID"] == 1


# ---------------------------------------------------------------------------
# _missing_entry_price_count
# ---------------------------------------------------------------------------

class TestMissingEntryPriceCount:
    def test_none_price_counted(self):
        trades = [_make_trade(side_market_price=None)]
        assert _missing_entry_price_count(trades) == 1

    def test_present_price_not_counted(self):
        trades = [_make_trade(side_market_price=0.55)]
        assert _missing_entry_price_count(trades) == 0

    def test_mixed(self):
        trades = [
            _make_trade(side_market_price=None),
            _make_trade(side_market_price=0.60),
            _make_trade(side_market_price=None),
        ]
        assert _missing_entry_price_count(trades) == 2

    def test_empty_returns_zero(self):
        assert _missing_entry_price_count([]) == 0


# ---------------------------------------------------------------------------
# _stale_or_missing_quote_count
# ---------------------------------------------------------------------------

class TestStaleOrMissingQuoteCount:
    def test_missing_quote_when_both_timestamp_and_price_null(self):
        t = _make_trade(quote_timestamp=None, side_market_price=None)
        result = _stale_or_missing_quote_count([t])
        assert result["missing_quote"] == 1

    def test_not_missing_when_price_present(self):
        # quote_timestamp None but side_market_price present → not missing
        t = _make_trade(quote_timestamp=None, side_market_price=0.55)
        result = _stale_or_missing_quote_count([t])
        assert result["missing_quote"] == 0

    def test_stale_when_age_exceeds_300(self):
        t = _make_trade(quote_age_seconds=301.0)
        result = _stale_or_missing_quote_count([t])
        assert result["stale_quote"] == 1

    def test_not_stale_when_age_exactly_300(self):
        t = _make_trade(quote_age_seconds=300.0)
        result = _stale_or_missing_quote_count([t])
        assert result["stale_quote"] == 0

    def test_not_stale_when_age_null(self):
        t = _make_trade(quote_age_seconds=None)
        result = _stale_or_missing_quote_count([t])
        assert result["stale_quote"] == 0

    def test_empty_returns_zeros(self):
        result = _stale_or_missing_quote_count([])
        assert result == {"missing_quote": 0, "stale_quote": 0}


# ---------------------------------------------------------------------------
# _integrity_exception_count
# ---------------------------------------------------------------------------

class TestIntegrityExceptionCount:
    def test_non_empty_flags_counted(self):
        t = _make_trade(quality_flags=["STALE_QUOTE"])
        assert _integrity_exception_count([t]) == 1

    def test_empty_flags_not_counted(self):
        t = _make_trade(quality_flags=[])
        assert _integrity_exception_count([t]) == 0

    def test_none_flags_not_counted(self):
        t = _make_trade(quality_flags=None)
        assert _integrity_exception_count([t]) == 0

    def test_multiple_flags_still_one_exception(self):
        t = _make_trade(quality_flags=["FLAG_A", "FLAG_B"])
        assert _integrity_exception_count([t]) == 1

    def test_mixed_population(self):
        trades = [
            _make_trade(quality_flags=["FLAG_A"]),
            _make_trade(quality_flags=[]),
            _make_trade(quality_flags=["FLAG_B"]),
        ]
        assert _integrity_exception_count(trades) == 2

    def test_empty_returns_zero(self):
        assert _integrity_exception_count([]) == 0


# ---------------------------------------------------------------------------
# _date_range
# ---------------------------------------------------------------------------

class TestDateRange:
    def test_returns_none_when_no_created_at(self):
        t = MagicMock()
        t.created_at = None
        t.target_settlement_date = None
        result = _date_range([t])
        assert result["oldest_entry"] is None
        assert result["newest_entry"] is None

    def test_returns_none_when_no_target_settlement_date(self):
        t = _make_trade(target_settlement_date=None)
        result = _date_range([t])
        assert result["oldest_target_settlement_date"] is None
        assert result["newest_target_settlement_date"] is None

    def test_empty_list_returns_all_none(self):
        result = _date_range([])
        assert result == {
            "oldest_entry": None,
            "newest_entry": None,
            "oldest_target_settlement_date": None,
            "newest_target_settlement_date": None,
        }

    def test_settlement_date_range_computed(self):
        t1 = _make_trade(target_settlement_date="2026-01-01")
        t2 = _make_trade(target_settlement_date="2026-03-15")
        t3 = _make_trade(target_settlement_date="2026-02-10")
        result = _date_range([t1, t2, t3])
        assert result["oldest_target_settlement_date"] == "2026-01-01"
        assert result["newest_target_settlement_date"] == "2026-03-15"


# ---------------------------------------------------------------------------
# _funnel_narrative
# ---------------------------------------------------------------------------

class TestFunnelNarrative:
    def test_no_trades_gives_single_message(self):
        notes = _funnel_narrative(0, {s: 0 for s in ["SETTLED", "OPEN", "PENDING_SETTLEMENT", "VOID"]}, 0, 0)
        assert len(notes) == 1
        assert "No paper trades" in notes[0]

    def test_missing_entry_price_mentioned(self):
        lifecycle = {"SETTLED": 5, "OPEN": 2, "PENDING_SETTLEMENT": 0, "VOID": 0}
        notes = _funnel_narrative(7, lifecycle, missing_entry_price=3, integrity_exceptions=0)
        assert any("missing" in n.lower() and "entry price" in n.lower() for n in notes)

    def test_integrity_exceptions_mentioned(self):
        lifecycle = {"SETTLED": 5, "OPEN": 2, "PENDING_SETTLEMENT": 0, "VOID": 0}
        notes = _funnel_narrative(7, lifecycle, missing_entry_price=0, integrity_exceptions=2)
        assert any("quality_flags" in n or "integrity" in n.lower() for n in notes)

    def test_zero_settled_warns(self):
        lifecycle = {"SETTLED": 0, "OPEN": 3, "PENDING_SETTLEMENT": 0, "VOID": 0}
        notes = _funnel_narrative(3, lifecycle, missing_entry_price=0, integrity_exceptions=0)
        assert any("no settled" in n.lower() for n in notes)

    def test_no_mention_when_no_issues(self):
        lifecycle = {"SETTLED": 10, "OPEN": 0, "PENDING_SETTLEMENT": 0, "VOID": 0}
        notes = _funnel_narrative(10, lifecycle, missing_entry_price=0, integrity_exceptions=0)
        # Should have a summary line; no missing-price or integrity warnings
        assert not any("missing" in n.lower() and "entry price" in n.lower() for n in notes)
        assert not any("quality_flags" in n for n in notes)


# ---------------------------------------------------------------------------
# _build_population_block — fail-closed handling of empty / missing data
# ---------------------------------------------------------------------------

class TestBuildPopulationBlock:
    def test_empty_population_returns_safe_defaults(self):
        block = _build_population_block(_POP_OFFICIAL, [])
        assert block["total_paper_trades"] == 0
        assert block["settled_count"] == 0
        assert block["settlement_coverage_pct"] is None
        assert block["missing_entry_price_count"] == 0
        assert block["integrity_exception_count"] == 0
        assert block["strategy_breakdown"] == []

    def test_population_label_preserved(self):
        block = _build_population_block(_POP_RESEARCH, [])
        assert block["population"] == _POP_RESEARCH

    def test_settlement_coverage_pct_computed(self):
        trades = [
            _make_trade(status="SETTLED"),
            _make_trade(status="SETTLED"),
            _make_trade(status="OPEN"),
            _make_trade(status="OPEN"),
        ]
        block = _build_population_block(_POP_OFFICIAL, trades)
        assert block["settlement_coverage_pct"] == 50.0

    def test_trading_state_not_modified(self):
        # The block itself should not set trading_state_modified;
        # that invariant lives on the endpoint response, not sub-blocks.
        block = _build_population_block(_POP_OFFICIAL, [])
        assert "trading_state_modified" not in block

    def test_strategy_breakdown_groups_correctly(self):
        trades = [
            _make_trade(strategy_version="v2.3"),
            _make_trade(strategy_version="v2.3"),
            _make_trade(strategy_version="v3.0"),
        ]
        block = _build_population_block(_POP_OFFICIAL, trades)
        versions = {row["strategy_version"] for row in block["strategy_breakdown"]}
        assert versions == {"v2.3", "v3.0"}

    def test_strategy_counts_correct(self):
        trades = [
            _make_trade(strategy_version="v2.3", status="SETTLED"),
            _make_trade(strategy_version="v2.3", status="OPEN"),
            _make_trade(strategy_version="v2.3", status="SETTLED"),
        ]
        block = _build_population_block(_POP_OFFICIAL, trades)
        row = block["strategy_breakdown"][0]
        assert row["total"] == 3
        assert row["settled_count"] == 2


# ---------------------------------------------------------------------------
# Safety invariants — no mutation, no trading-state changes
# ---------------------------------------------------------------------------

class TestSafetyInvariants:
    """
    The pure helpers must not produce any value that implies
    trading_state_modified=True or real_money_execution_enabled=True.
    """

    def test_lifecycle_counts_does_not_set_trading_flag(self):
        # Verify the helper returns a plain dict without trading flags
        result = _lifecycle_counts([_make_trade()])
        assert "trading_state_modified" not in result
        assert "real_money_execution_enabled" not in result

    def test_build_population_block_no_trading_flags(self):
        block = _build_population_block(_POP_OFFICIAL, [_make_trade()])
        assert "trading_state_modified" not in block
        assert "real_money_execution_enabled" not in block

    def test_missing_entry_price_is_read_only(self):
        # Calling the helper should not mutate the trade object
        t = _make_trade(side_market_price=None)
        original_price = t.side_market_price
        _missing_entry_price_count([t])
        assert t.side_market_price == original_price

    def test_integrity_exception_count_is_read_only(self):
        t = _make_trade(quality_flags=["FLAG"])
        original_flags = list(t.quality_flags)
        _integrity_exception_count([t])
        assert list(t.quality_flags) == original_flags
