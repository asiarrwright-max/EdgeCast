from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.routers.forward_evidence_reconciliation import (
    _CLASS_CONTEXT,
    _CLASS_DIRECT,
    _CLASS_INCOMPATIBLE,
    _METHOD_PROFILE,
    _cohort_metrics,
    _evidence_counts_and_pooling,
    _profile_for_strategy,
)


def _trade(
    *,
    strategy_version: str = "v3.0",
    eligibility_status: str | None = "OFFICIAL",
    status: str = "SETTLED",
    ec_yes_probability: float | None = 0.7,
    kalshi_result: str | None = "yes",
):
    t = MagicMock()
    t.strategy_version = strategy_version
    t.eligibility_status = eligibility_status
    t.status = status
    t.ec_yes_probability = ec_yes_probability
    t.kalshi_result = kalshi_result
    return t


def test_strategy_profiles_are_classified():
    assert _profile_for_strategy("v3.0")["classification"] == _CLASS_DIRECT
    assert _profile_for_strategy("v2.3")["classification"] == _CLASS_CONTEXT
    assert _profile_for_strategy("v2.0")["classification"] == _CLASS_INCOMPATIBLE
    assert _profile_for_strategy("unknown-strategy")["classification"] == _CLASS_INCOMPATIBLE


def test_cohort_metrics_returns_brier_and_calibration():
    trades = [
        _trade(ec_yes_probability=0.9, kalshi_result="yes"),
        _trade(ec_yes_probability=0.8, kalshi_result="no"),
        _trade(ec_yes_probability=0.2, kalshi_result="no"),
        _trade(ec_yes_probability=0.1, kalshi_result="yes"),
    ]
    metrics = _cohort_metrics(trades)
    assert metrics["settled_n"] == 4
    assert metrics["scored_n"] == 4
    assert metrics["brier"] is not None
    assert metrics["brier"]["mean"] >= 0
    assert len(metrics["calibration_table"]) >= 1


def test_pooling_guard_blocks_without_older_directly_comparable():
    settled = [
        _trade(strategy_version="v3.0", eligibility_status="OFFICIAL"),
        _trade(strategy_version="v2.3", eligibility_status="OFFICIAL"),
        _trade(strategy_version="v2.1", eligibility_status="RESEARCH_ONLY"),
    ]
    counts = _evidence_counts_and_pooling(settled)
    assert counts["current_clean_v3_forward_settled_n"] == 1
    assert counts["older_directly_comparable_settled_n"] == 0
    assert counts["older_context_only_or_excluded_settled_n"] == 2
    assert counts["pooling_permitted"] is False


def test_pooling_guard_allows_when_direct_older_cohort_exists():
    injected = {
        **_METHOD_PROFILE["v3.0"],
        "classification": _CLASS_DIRECT,
        "equivalent_to_v3": True,
    }
    with patch.dict(_METHOD_PROFILE, {"v3.1": injected}, clear=False):
        settled = [
            _trade(strategy_version="v3.0", eligibility_status="OFFICIAL"),
            _trade(strategy_version="v3.1", eligibility_status="OFFICIAL"),
        ]
        counts = _evidence_counts_and_pooling(settled)
        assert counts["current_clean_v3_forward_settled_n"] == 1
        assert counts["older_directly_comparable_settled_n"] == 1
        assert counts["pooling_permitted"] is True
