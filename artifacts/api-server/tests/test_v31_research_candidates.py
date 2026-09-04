"""
Tests for V3.1 research candidate helpers (YELLOW prediction-quality sprint,
issue #44).

All functions under test are offline/read-only and must not modify any
production state, settlement results, or eligibility classifications.
"""
from __future__ import annotations

from app.routers.v3_analytics import (
    _v31_apply_candidate,
    _v31_candidate_evaluations,
    _v31_holdout_split,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _row(
    actual: float,
    model_prob: float | None,
    *,
    contract_type: str = "threshold",
    lead: str = "0-1d",
    market_prob: float | None = None,
    date: str = "2026-08-01",
    city: str = "Chicago",
) -> dict:
    return {
        "event_key": f"{city}|{date}|temperature_max",
        "actual": actual,
        "model_prob": model_prob,
        "market_prob": market_prob,
        "contract_type": contract_type,
        "lead_time_bucket": lead,
        "settlement_date": date,
    }


# ---------------------------------------------------------------------------
# _v31_apply_candidate
# ---------------------------------------------------------------------------

def test_global_shrinkage_moves_toward_half():
    p = _v31_apply_candidate(
        0.9,
        candidate="global_shrinkage",
        contract_type="threshold",
        lead_time_bucket="0-1d",
        market_prob=None,
    )
    assert p is not None
    assert 0.5 < p < 0.9


def test_global_shrinkage_symmetric():
    p_hi = _v31_apply_candidate(
        0.8,
        candidate="global_shrinkage",
        contract_type="threshold",
        lead_time_bucket="0-1d",
        market_prob=None,
    )
    p_lo = _v31_apply_candidate(
        0.2,
        candidate="global_shrinkage",
        contract_type="threshold",
        lead_time_bucket="0-1d",
        market_prob=None,
    )
    assert p_hi is not None and p_lo is not None
    assert round(p_hi + p_lo, 9) == 1.0


def test_threshold_shrinkage_shrinks_threshold_more_than_range():
    p = 0.85
    p_threshold = _v31_apply_candidate(
        p,
        candidate="threshold_shrinkage",
        contract_type="threshold",
        lead_time_bucket="0-1d",
        market_prob=None,
    )
    p_range = _v31_apply_candidate(
        p,
        candidate="threshold_shrinkage",
        contract_type="range",
        lead_time_bucket="0-1d",
        market_prob=None,
    )
    assert p_threshold is not None and p_range is not None
    assert p_threshold < p_range


def test_range_shrinkage_shrinks_range_less_than_threshold():
    p = 0.85
    p_range = _v31_apply_candidate(
        p,
        candidate="range_shrinkage",
        contract_type="range",
        lead_time_bucket="0-1d",
        market_prob=None,
    )
    p_threshold = _v31_apply_candidate(
        p,
        candidate="range_shrinkage",
        contract_type="threshold",
        lead_time_bucket="0-1d",
        market_prob=None,
    )
    assert p_range is not None and p_threshold is not None
    assert p_range > p_threshold


def test_lead_time_shrinkage_increases_with_lead():
    p = 0.85
    p_short = _v31_apply_candidate(
        p,
        candidate="lead_time_shrinkage",
        contract_type="threshold",
        lead_time_bucket="0-1d",
        market_prob=None,
    )
    p_long = _v31_apply_candidate(
        p,
        candidate="lead_time_shrinkage",
        contract_type="threshold",
        lead_time_bucket="8d+",
        market_prob=None,
    )
    assert p_short is not None and p_long is not None
    # Less shrinkage at short lead → higher output probability
    assert p_short > p_long


def test_market_blend_uses_market_prob_when_available():
    p_model = 0.9
    p_market = 0.6
    p_blended = _v31_apply_candidate(
        p_model,
        candidate="market_blend",
        contract_type="threshold",
        lead_time_bucket="0-1d",
        market_prob=p_market,
    )
    assert p_blended is not None
    assert p_market < p_blended < p_model


def test_market_blend_fallback_when_market_prob_missing():
    p_model = 0.85
    p_blended = _v31_apply_candidate(
        p_model,
        candidate="market_blend",
        contract_type="threshold",
        lead_time_bucket="0-1d",
        market_prob=None,
    )
    assert p_blended is not None
    assert 0.5 < p_blended < p_model


def test_apply_candidate_returns_none_for_none_input():
    for candidate in [
        "global_shrinkage",
        "threshold_shrinkage",
        "range_shrinkage",
        "lead_time_shrinkage",
        "market_blend",
    ]:
        result = _v31_apply_candidate(
            None,
            candidate=candidate,
            contract_type="threshold",
            lead_time_bucket="0-1d",
            market_prob=0.5,
        )
        assert result is None, f"Expected None for candidate={candidate}"


def test_apply_candidate_clamps_to_unit_interval():
    p = _v31_apply_candidate(
        1.0,
        candidate="global_shrinkage",
        contract_type="range",
        lead_time_bucket="0-1d",
        market_prob=None,
    )
    assert p is not None
    assert 0.0 <= p <= 1.0


# ---------------------------------------------------------------------------
# _v31_candidate_evaluations
# ---------------------------------------------------------------------------

def test_candidate_evaluations_returns_all_five_candidates():
    rows = [_row(1.0, 0.85), _row(0.0, 0.85), _row(1.0, 0.7)]
    results = _v31_candidate_evaluations(rows)
    names = {r["candidate"] for r in results}
    assert names == {
        "global_shrinkage",
        "threshold_shrinkage",
        "range_shrinkage",
        "lead_time_shrinkage",
        "market_blend",
    }


def test_candidate_evaluations_empty_rows_returns_empty_list():
    assert _v31_candidate_evaluations([]) == []


def test_candidate_evaluations_shrinkage_improves_brier_for_overconfident_model():
    # 50/50 win rate but model is always 95% — overconfident → shrinkage should help
    rows = [_row(1.0, 0.95), _row(0.0, 0.95), _row(0.0, 0.95), _row(1.0, 0.95)]
    results = _v31_candidate_evaluations(rows)
    shrink = next(r for r in results if r["candidate"] == "global_shrinkage")
    assert shrink["improves_over_v3_trade"] is True


def test_candidate_evaluations_includes_by_contract_type():
    rows = [
        _row(1.0, 0.85, contract_type="threshold"),
        _row(0.0, 0.85, contract_type="range"),
    ]
    results = _v31_candidate_evaluations(rows)
    for cand in results:
        ct_labels = {r["contract_type"] for r in cand["by_contract_type"]}
        assert "threshold" in ct_labels or "range" in ct_labels


def test_candidate_evaluations_v3_baseline_same_across_candidates():
    rows = [_row(1.0, 0.8, market_prob=0.6), _row(0.0, 0.8, market_prob=0.6)]
    results = _v31_candidate_evaluations(rows)
    baselines = {r["v3_baseline_brier_trade"] for r in results}
    assert len(baselines) == 1


def test_candidate_evaluations_includes_event_level_brier():
    rows = [
        _row(1.0, 0.85, date="2026-07-01", city="Chicago"),
        _row(0.0, 0.85, date="2026-07-02", city="Denver"),
    ]
    results = _v31_candidate_evaluations(rows)
    for cand in results:
        assert "candidate_brier_event" in cand
        assert "v3_baseline_brier_event" in cand


# ---------------------------------------------------------------------------
# _v31_holdout_split
# ---------------------------------------------------------------------------

def test_holdout_split_empty_rows():
    result = _v31_holdout_split([])
    assert result["dev_rows"] == 0
    assert result["holdout_rows"] == 0
    assert result["split_date"] is None
    assert result["development"] == []
    assert result["holdout"] == []


def test_holdout_split_chronological_partition():
    rows = (
        [_row(1.0, 0.8, date="2026-06-01")] * 3
        + [_row(0.0, 0.8, date="2026-07-01")] * 3
        + [_row(1.0, 0.7, date="2026-08-01")] * 3
    )
    result = _v31_holdout_split(rows, holdout_fraction=0.33)
    assert result["split_date"] is not None
    assert result["holdout_rows"] > 0
    assert result["dev_rows"] > 0
    for d in result["holdout_dates"]:
        assert d >= result["split_date"]
    for d in result["dev_dates"]:
        assert d < result["split_date"]


def test_holdout_split_rows_sum_to_total():
    rows = (
        [_row(1.0, 0.8, date="2026-06-01")] * 4
        + [_row(0.0, 0.75, date="2026-07-01")] * 4
    )
    result = _v31_holdout_split(rows, holdout_fraction=0.5)
    assert result["dev_rows"] + result["holdout_rows"] == len(rows)


def test_holdout_split_produces_candidate_evals_for_non_empty_partitions():
    rows = (
        [_row(1.0, 0.85, date="2026-06-01")] * 3
        + [_row(0.0, 0.85, date="2026-07-01")] * 3
        + [_row(1.0, 0.7, date="2026-08-01")] * 3
    )
    result = _v31_holdout_split(rows, holdout_fraction=0.33)
    if result["dev_rows"] > 0:
        assert len(result["development"]) > 0
    if result["holdout_rows"] > 0:
        assert len(result["holdout"]) > 0


def test_holdout_split_notes_references_no_peeking():
    rows = (
        [_row(1.0, 0.8, date="2026-07-01")] * 5
        + [_row(0.0, 0.8, date="2026-08-01")] * 5
    )
    result = _v31_holdout_split(rows)
    assert "peeking" in result["notes"].lower()


def test_holdout_split_single_date_puts_all_in_holdout():
    rows = [_row(1.0, 0.8, date="2026-08-01")] * 6
    result = _v31_holdout_split(rows, holdout_fraction=0.5)
    # Only one distinct date → rounds to 1 holdout date → all rows in holdout
    assert result["holdout_rows"] == 6
    assert result["dev_rows"] == 0
