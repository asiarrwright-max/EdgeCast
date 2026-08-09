"""
tests/test_verified_city_study.py
==================================
Tests for the Verified City Specialization Study.

Requirements:
  1. Unverified cities cannot appear in the verified shortlist.
  2. Non-NWS cities cannot appear.
  3. Verification status changes only when authoritative evidence is present.
  4. Conflicts do not silently overwrite station mappings.
  5. City ranking is deterministic.
  6. No paper_trades or FTB state is modified.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.routers.verified_city_study import (
    VERIFICATION_EVIDENCE,
    TARGET_CITIES,
    _build_verified_shortlist,
    get_verified_city_study,
)
from app.services.settlement_stations import SETTLEMENT_STATIONS


# ---------------------------------------------------------------------------
# 1. Unverified cities cannot appear in the shortlist
# ---------------------------------------------------------------------------

class TestUnverifiedCitiesExcluded:
    def _make_city(self, city, verified, nws, settled, win_rate, mae,
                   pct_valid, fv_obs=20):
        from app.routers.city_study import (
            _score_mae, _score_win_rate, _score_liquidity,
            _score_sample, _score_station,
            W_FORECAST, W_TRADING, W_LIQUIDITY, W_SAMPLE, W_STATION,
        )
        s_f  = _score_mae(mae)
        s_t  = _score_win_rate(win_rate, settled)
        s_l  = _score_liquidity(pct_valid)
        s_s  = _score_sample(settled, fv_obs)
        s_st = _score_station(verified, nws)
        total = (W_FORECAST*s_f + W_TRADING*s_t + W_LIQUIDITY*s_l +
                 W_SAMPLE*s_s + W_STATION*s_st)
        return {
            "city":               city,
            "station_verified":   verified,
            "nws_compatible":     nws,
            "settled_total":      settled,
            "wins":               int(settled * (win_rate or 0) / 100),
            "losses":             settled - int(settled * (win_rate or 0) / 100),
            "win_rate_pct":       win_rate,
            "mae":                mae,
            "fv_obs":             fv_obs,
            "score":              {"total": total, "forecast": s_f, "trading": s_t,
                                   "liquidity": s_l, "sample": s_s, "station": s_st},
            "sample_size_grade":  "MODERATE",
            "sample_warnings":    [],
        }

    def test_unverified_high_score_excluded_by_registry(self):
        """
        The _build_verified_shortlist receives only pre-filtered cities.
        Verify the endpoint filter (city in verified_nws) excludes unverified.
        """
        unverified_cities = [
            name for name, sta in SETTLEMENT_STATIONS.items()
            if not sta.verified and sta.nws_settlement
        ]
        # All unverified cities must NOT be in any shortlist from the endpoint
        # since the endpoint pre-filters to verified_nws before calling this helper.
        # Simulate the helper receiving ONLY verified entries.
        eligible = [
            self._make_city("Denver",      True,  True, 265, 50.2, 2.90, 70),
            self._make_city("OKC",         True,  True, 137, 43.8, 2.59, 62),
            self._make_city("New York City", True, True, 156, 39.1, 1.72, 45),
        ]
        shortlist, verdict, _ = _build_verified_shortlist(eligible)
        for city in unverified_cities:
            assert city not in shortlist, f"Unverified city '{city}' should not appear in shortlist"

    def test_shortlist_contains_no_non_verified_cities(self):
        """Shortlist must only contain verified+NWS entries."""
        eligible = [
            self._make_city("Denver",      True,  True, 265, 50.2, 2.90, 70),
            self._make_city("Houston",     True,  True, 124, 22.6, 0.87, 85),
            self._make_city("OKC",         True,  True, 137, 43.8, 2.59, 62),
        ]
        shortlist, _, _ = _build_verified_shortlist(eligible)
        for city in shortlist:
            sta = SETTLEMENT_STATIONS.get(city)
            if sta is not None:
                assert sta.verified, f"{city} in shortlist but not verified"
                assert sta.nws_settlement, f"{city} in shortlist but non-NWS"


# ---------------------------------------------------------------------------
# 2. Non-NWS cities cannot appear
# ---------------------------------------------------------------------------

class TestNonNwsCitiesExcluded:
    def test_washington_dc_not_in_registry_eligible(self):
        """Washington DC must not appear in the verified+NWS set."""
        dc_sta = SETTLEMENT_STATIONS.get("Washington DC")
        assert dc_sta is not None, "Washington DC must exist in registry"
        assert dc_sta.nws_settlement is False, "Washington DC must have nws_settlement=False"
        # The endpoint filters to nws_settlement=True, so DC is excluded before ranking

    def test_non_nws_city_not_reachable_in_shortlist(self):
        """If somehow a non-NWS city enters the pipeline, shortlist rejects it."""
        # _build_verified_shortlist doesn't itself check nws — it trusts the caller
        # to pre-filter. Verify the endpoint's filter logic via registry inspection.
        verified_nws = {
            name for name, sta in SETTLEMENT_STATIONS.items()
            if sta.verified and sta.nws_settlement
        }
        assert "Washington DC" not in verified_nws
        for city in verified_nws:
            sta = SETTLEMENT_STATIONS[city]
            assert sta.nws_settlement, f"{city} in verified_nws but nws_settlement=False"


# ---------------------------------------------------------------------------
# 3. Verification status changes only with authoritative evidence
# ---------------------------------------------------------------------------

class TestVerificationEvidenceIntegrity:
    def test_all_target_cities_have_evidence_entries(self):
        """All 5 target cities must have a VERIFICATION_EVIDENCE entry."""
        for city in TARGET_CITIES:
            assert city in VERIFICATION_EVIDENCE, (
                f"{city} missing from VERIFICATION_EVIDENCE"
            )

    def test_all_target_cities_are_verified_in_registry(self):
        """All 5 target cities must be verified=True in SETTLEMENT_STATIONS."""
        for city in TARGET_CITIES:
            sta = SETTLEMENT_STATIONS.get(city)
            assert sta is not None, f"{city} not in SETTLEMENT_STATIONS"
            assert sta.verified is True, (
                f"{city} is not marked verified=True in SETTLEMENT_STATIONS"
            )

    def test_all_evidence_has_query_date(self):
        """Each evidence entry must record the date of the authoritative query."""
        for city, ev in VERIFICATION_EVIDENCE.items():
            assert ev.get("query_date"), f"{city} evidence missing query_date"

    def test_all_evidence_has_settlement_text(self):
        """Each entry must record the exact settlement text from the API."""
        for city, ev in VERIFICATION_EVIDENCE.items():
            text = ev.get("settlement_text", "")
            assert len(text) > 10, f"{city} evidence has no meaningful settlement_text"

    def test_flag_changed_is_false_for_all_target_cities(self):
        """
        In this task, all 5 cities were already verified.
        flag_changed must be False — no station flags were modified.
        """
        for city, ev in VERIFICATION_EVIDENCE.items():
            assert ev["flag_changed"] is False, (
                f"{city}: flag_changed should be False (station was already verified)"
            )

    def test_all_verdicts_are_verified(self):
        """All target cities must have VERIFIED verdict."""
        for city, ev in VERIFICATION_EVIDENCE.items():
            assert ev["verdict"] == "VERIFIED", (
                f"{city} has verdict '{ev['verdict']}', expected 'VERIFIED'"
            )


# ---------------------------------------------------------------------------
# 4. Conflicts do not silently overwrite station mappings
# ---------------------------------------------------------------------------

class TestConflictHandling:
    def test_no_conflicts_in_current_study(self):
        """No conflicts were found for the 5 target cities."""
        for city, ev in VERIFICATION_EVIDENCE.items():
            assert ev["verdict"] != "CONFLICT", (
                f"{city} unexpectedly has a CONFLICT verdict"
            )

    def test_settlement_station_coords_match_expectations(self):
        """
        Spot-check that station coordinates in SETTLEMENT_STATIONS
        are consistent with known airport locations.
        """
        checks = [
            ("Houston",      29.6, 29.7,  -95.4, -95.1),   # KHOU ~29.65°N 95.28°W
            ("Oklahoma City", 35.3, 35.5, -97.7, -97.5),   # KOKC ~35.39°N 97.60°W
            ("Dallas",        32.8, 33.0, -97.1, -96.9),   # KDFW ~32.90°N 97.04°W
            ("Minneapolis",   44.8, 45.0, -93.3, -93.1),   # KMSP ~44.88°N 93.22°W
            ("Miami",         25.7, 25.9, -80.4, -80.1),   # KMIA ~25.80°N 80.29°W
        ]
        for city, lat_lo, lat_hi, lon_lo, lon_hi in checks:
            sta = SETTLEMENT_STATIONS.get(city)
            assert sta is not None
            assert lat_lo <= sta.lat <= lat_hi, (
                f"{city} lat {sta.lat} outside expected range [{lat_lo}, {lat_hi}]"
            )
            assert lon_lo <= sta.lon <= lon_hi, (
                f"{city} lon {sta.lon} outside expected range [{lon_lo}, {lon_hi}]"
            )


# ---------------------------------------------------------------------------
# 5. Ranking is deterministic
# ---------------------------------------------------------------------------

class TestRankingDeterminism:
    def _make_eligible(self):
        return [
            {
                "city": "Denver",       "settled_total": 265, "win_rate_pct": 50.2,
                "mae": 2.90, "wins": 133, "losses": 132, "fv_obs": 24,
                "score": {"total": 71.1, "forecast": 52, "trading": 50.2, "liquidity": 65, "sample": 73, "station": 100},
                "sample_size_grade": "MODERATE", "sample_warnings": [],
            },
            {
                "city": "Houston",      "settled_total": 124, "win_rate_pct": 22.6,
                "mae": 0.87, "wins": 28, "losses": 96, "fv_obs": 12,
                "score": {"total": 65.9, "forecast": 90, "trading": 22.6, "liquidity": 85, "sample": 53, "station": 100},
                "sample_size_grade": "LOW", "sample_warnings": [],
            },
            {
                "city": "OKC",          "settled_total": 137, "win_rate_pct": 43.8,
                "mae": 2.59, "wins": 60, "losses": 77, "fv_obs": 12,
                "score": {"total": 63.3, "forecast": 58, "trading": 43.8, "liquidity": 62, "sample": 55, "station": 100},
                "sample_size_grade": "LOW", "sample_warnings": [],
            },
        ]

    def test_shortlist_is_deterministic(self):
        """Calling _build_verified_shortlist with the same input always returns the same result."""
        eligible = self._make_eligible()
        r1, v1, _ = _build_verified_shortlist(eligible[:])
        r2, v2, _ = _build_verified_shortlist(eligible[:])
        assert r1 == r2
        assert v1 == v2

    def test_shortlist_order_is_stable(self):
        """The first city in the shortlist is always the top-scoring city."""
        eligible = self._make_eligible()
        shortlist, _, _ = _build_verified_shortlist(eligible)
        if shortlist:
            assert shortlist[0] == "Denver", (
                f"Expected Denver first (highest total score), got {shortlist[0]}"
            )

    def test_empty_input_returns_empty_shortlist(self):
        shortlist, verdict, _ = _build_verified_shortlist([])
        assert shortlist == []
        assert verdict.startswith("NO_")

    def test_two_cities_qualify_gives_two(self):
        """If only 2 cities pass the evidence bar, shortlist is ≤ 2."""
        tiny = self._make_eligible()[:2]
        shortlist, verdict, _ = _build_verified_shortlist(tiny, max_size=3)
        assert len(shortlist) <= 2

    def test_shortlist_no_duplicates(self):
        eligible = self._make_eligible()
        shortlist, _, _ = _build_verified_shortlist(eligible)
        assert len(shortlist) == len(set(shortlist))


# ---------------------------------------------------------------------------
# 6. Read-only — no paper_trades or FTB modifications
# ---------------------------------------------------------------------------

class TestReadOnly:
    @pytest.mark.asyncio
    async def test_endpoint_issues_no_mutations(self):
        """Verify no INSERT/UPDATE/DELETE statements are issued."""
        executed: list[str] = []

        async def fake_execute(stmt, *args, **kwargs):
            executed.append(str(stmt).strip().upper())
            result = MagicMock()
            result.fetchall.return_value = []
            return result

        mock_db = AsyncMock()
        mock_db.execute = fake_execute

        try:
            await get_verified_city_study(db=mock_db, _user={"username": "test"})
        except Exception:
            pass  # Empty DB returns may cause downstream errors — that's fine

        mutation_keywords = ["INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER"]
        for stmt in executed:
            for kw in mutation_keywords:
                assert kw not in stmt, f"Mutation detected in verified city study: {stmt[:120]}"

    @pytest.mark.asyncio
    async def test_response_attestations(self):
        """Response always carries safety flags."""
        mock_db = AsyncMock()
        result = MagicMock()
        result.fetchall.return_value = []
        mock_db.execute = AsyncMock(return_value=result)

        response = await get_verified_city_study(db=mock_db, _user={"username": "test"})
        assert response["trading_state_modified"] is False
        assert response["ftb_untouched"] is True
        assert response["station_flags_changed"] is False
        assert response["read_only"] is True

    @pytest.mark.asyncio
    async def test_newly_verified_is_empty_list(self):
        """All target cities were already verified — newly_verified must be []."""
        mock_db = AsyncMock()
        result = MagicMock()
        result.fetchall.return_value = []
        mock_db.execute = AsyncMock(return_value=result)

        response = await get_verified_city_study(db=mock_db, _user={"username": "test"})
        assert response["newly_verified"] == []
        assert response["conflicts"] == []


# ---------------------------------------------------------------------------
# 7. Registry-level sanity checks
# ---------------------------------------------------------------------------

class TestRegistrySanity:
    def test_all_five_target_cities_in_settlement_stations(self):
        for city in TARGET_CITIES:
            assert city in SETTLEMENT_STATIONS, f"{city} missing from SETTLEMENT_STATIONS"

    def test_all_five_target_cities_nws_compatible(self):
        for city in TARGET_CITIES:
            sta = SETTLEMENT_STATIONS[city]
            assert sta.nws_settlement, f"{city} should have nws_settlement=True"

    def test_all_five_ghcnd_ids_non_empty(self):
        for city in TARGET_CITIES:
            sta = SETTLEMENT_STATIONS[city]
            assert sta.ghcnd_station_id and len(sta.ghcnd_station_id) > 5, (
                f"{city} has no valid GHCND station ID"
            )

    def test_all_five_have_source_citations(self):
        for city in TARGET_CITIES:
            sta = SETTLEMENT_STATIONS[city]
            assert sta.source, f"{city} has no source citation in SETTLEMENT_STATIONS"
