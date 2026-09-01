"""Regression tests for the frozen prospective V3.1 shadow cohort."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.v31_shadow_validation import (
    CANDIDATE_VERSION,
    build_shadow_payload,
    build_shadow_report,
)


def _trade(**overrides):
    values = {
        "id": 101,
        "market_ticker": "KXHIGHCHI-26SEP02-B80",
        "city": "Chicago",
        "weather_variable": "high",
        "contract_type": "range",
        "target_settlement_date": "2026-09-02",
        "collection_batch_id": "batch-1",
        "decision_timestamp": datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
        "direction": "NO",
        "ec_yes_probability": 0.20,
        "ec_side_probability": 0.80,
        "market_yes_probability": 0.75,
        "side_market_price": 0.25,
        "lead_time_days": 1,
        "eligibility_status": "RESEARCH_ONLY",
        "eligibility_reason": "missing_or_stale_executable_quote",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _observation(obs_id: int, evidence: str, event: str, *, v3: float, market: float):
    return SimpleNamespace(
        id=obs_id,
        v3_paper_trade_id=obs_id + 100,
        market_ticker=f"TICKER-{obs_id}",
        event_key=event,
        evidence_class=evidence,
        v3_side_probability=v3,
        market_side_probability=market,
        blended_side_probability=(v3 + market) / 2,
    )


def _settled(status: str, outcome: str | None):
    return SimpleNamespace(status=status, outcome=outcome)


def test_payload_freezes_exact_pr49_50_50_candidate_without_mutation():
    trade = _trade()
    before = dict(vars(trade))

    payload = build_shadow_payload(trade, station_id="USW00014819")

    assert payload is not None
    assert payload["candidate_version"] == CANDIDATE_VERSION
    assert payload["v3_weight"] == 0.5
    assert payload["market_weight"] == 0.5
    assert payload["v3_side_probability"] == 0.80
    assert payload["market_side_probability"] == 0.25
    assert payload["blended_side_probability"] == pytest.approx(0.525)
    assert payload["model_market_disagreement"] == pytest.approx(0.55)
    assert payload["event_key"] == "Chicago|2026-09-02|high"
    assert payload["station_id"] == "USW00014819"
    assert payload["evidence_class"] == "RESEARCH_ONLY"
    assert vars(trade) == before


def test_payload_rejects_incomplete_probability_input_instead_of_guessing():
    assert build_shadow_payload(
        _trade(side_market_price=None), station_id="USW00014819"
    ) is None
    assert build_shadow_payload(
        _trade(ec_side_probability=None), station_id="USW00014819"
    ) is None


def test_report_keeps_evidence_classes_separate_and_counts_events_once():
    rows = [
        (_observation(1, "OFFICIAL", "Chicago|2026-09-02|high", v3=0.8, market=0.6),
         _settled("SETTLED", "WIN")),
        (_observation(2, "RESEARCH_ONLY", "Denver|2026-09-03|high", v3=0.8, market=0.2),
         _settled("SETTLED", "LOSS")),
        (_observation(3, "RESEARCH_ONLY", "Denver|2026-09-03|high", v3=0.6, market=0.4),
         _settled("SETTLED", "WIN")),
        (_observation(4, "unexpected", "Austin|2026-09-04|low", v3=0.7, market=0.3),
         _settled("OPEN", None)),
    ]

    report = build_shadow_report(rows)

    official = report["populations"]["OFFICIAL"]
    research = report["populations"]["RESEARCH_ONLY"]
    unclassified = report["populations"]["UNCLASSIFIED"]
    assert official["observations"] == 1
    assert official["settled"] == 1
    assert research["observations"] == 2
    assert research["settled"] == 2
    assert research["metrics"]["v3"]["n"] == 2
    assert research["metrics"]["v3"]["event_n"] == 1
    assert research["metrics"]["v3"]["wins"] == 1
    assert research["metrics"]["v3"]["losses"] == 1
    assert research["metrics"]["v3"]["brier"] == pytest.approx(0.4)
    assert research["metrics"]["frozen_50_50_blend"]["brier"] == pytest.approx(0.25)
    assert research["metrics"]["kalshi"]["brier"] == pytest.approx(0.2)
    assert unclassified["observations"] == 1
    assert unclassified["settled"] == 0
    assert unclassified["metrics"]["v3"]["n"] == 0


def test_minimum_milestone_uses_event_n_not_correlated_contract_n():
    rows = []
    for index in range(100):
        # 100 contracts but only 20 independent events.
        event = f"City-{index % 20}|2026-09-{index % 20 + 1:02d}|high"
        rows.append((
            _observation(index, "RESEARCH_ONLY", event, v3=0.6, market=0.4),
            _settled("SETTLED", "WIN" if index % 2 else "LOSS"),
        ))
    report = build_shadow_report(rows)
    milestones = report["populations"]["RESEARCH_ONLY"]["milestones"]
    assert milestones["current_event_n"] == 20
    assert milestones["too_small_for_comparative_conclusion"] is True
    assert milestones["next"]["event_n"] == 25
    assert milestones["next"]["remaining_events"] == 5


@pytest.mark.asyncio
async def test_shadow_persistence_is_fail_closed_when_database_unavailable(monkeypatch):
    from app import database
    from app.services.v31_shadow_validation import persist_shadow_payload

    monkeypatch.setattr(database, "AsyncSessionLocal", None)
    assert await persist_shadow_payload({"v3_paper_trade_id": 1}) is False
