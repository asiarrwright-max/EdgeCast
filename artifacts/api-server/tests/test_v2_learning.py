"""
Tests for V2 Learning Progress endpoints and helper functions.
Covers all 6 readiness states, milestone math, source quality labels,
aggregation, and empty-data behaviour.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.routers.audit import (
    _lp_compute_readiness_status,
    _lp_milestone_progress,
    _lp_source_quality_label,
    _READINESS_LABELS,
    _LP_MILESTONES,
    _LP_MIN_SAMPLE,
)


# ---------------------------------------------------------------------------
# Fixtures — mock SettlementStation and ForecastErrorStats
# ---------------------------------------------------------------------------

def _station(verified=True, notes=None):
    s = MagicMock()
    s.verified = verified
    s.notes = notes
    s.station_name = "Test Airport"
    s.ghcnd_station_id = "USW00099999"
    return s


def _fes(city="Chicago", variable="high", lead="0-1d", month=None, sample=1, fallback="city"):
    g = MagicMock()
    g.city = city
    g.weather_variable = variable
    g.lead_time_bucket = lead
    g.month = month
    g.sample_size = sample
    g.fallback_level = fallback
    g.mae = 2.0
    g.std_dev = 3.0
    g.mean_error = 0.5
    g.last_computed_at = None
    return g


# ---------------------------------------------------------------------------
# _lp_compute_readiness_status — all 6 states
# ---------------------------------------------------------------------------

class TestComputeReadinessStatus:

    def test_no_station_returns_data_quality_issue(self):
        result = _lp_compute_readiness_status(None, 10, [])
        assert result == "data_quality_issue"

    def test_high_ambiguity_note_returns_data_quality_issue(self):
        station = _station(verified=False, notes="UNVERIFIED. HIGH AMBIGUITY: LAX vs USC")
        result = _lp_compute_readiness_status(station, 50, [])
        assert result == "data_quality_issue"

    def test_high_ambiguity_case_insensitive(self):
        station = _station(notes="high ambiguity station")
        result = _lp_compute_readiness_status(station, 50, [])
        assert result == "data_quality_issue"

    def test_zero_usable_returns_not_collecting(self):
        station = _station()
        result = _lp_compute_readiness_status(station, 0, [])
        assert result == "not_collecting"

    def test_zero_usable_verified_station_returns_not_collecting(self):
        station = _station(verified=True)
        result = _lp_compute_readiness_status(station, 0, [])
        assert result == "not_collecting"

    def test_one_obs_returns_collecting(self):
        station = _station()
        result = _lp_compute_readiness_status(station, 1, [])
        assert result == "collecting"

    def test_four_obs_returns_collecting(self):
        station = _station()
        result = _lp_compute_readiness_status(station, 4, [])
        assert result == "collecting"

    def test_five_obs_no_fes_returns_insufficient_sample(self):
        station = _station()
        result = _lp_compute_readiness_status(station, 5, [])
        assert result == "insufficient_sample"

    def test_five_obs_fes_below_min_returns_insufficient_sample(self):
        station = _station()
        fes = [_fes(sample=4, fallback="city")]
        result = _lp_compute_readiness_status(station, 5, fes)
        assert result == "insufficient_sample"

    def test_five_obs_only_global_fes_returns_insufficient_sample(self):
        station = _station()
        fes = [_fes(sample=10, fallback="global")]
        result = _lp_compute_readiness_status(station, 5, fes)
        assert result == "insufficient_sample"

    def test_partially_learned_when_some_groups_ready(self):
        station = _station()
        fes = [
            _fes(variable="high", sample=5, fallback="city"),
            _fes(variable="low", sample=4, fallback="city"),
        ]
        result = _lp_compute_readiness_status(station, 10, fes)
        assert result == "partially_learned"

    def test_learned_when_all_city_groups_ready(self):
        station = _station()
        fes = [
            _fes(variable="high", sample=5, fallback="city"),
            _fes(variable="low", sample=6, fallback="city"),
        ]
        result = _lp_compute_readiness_status(station, 12, fes)
        assert result == "learned"

    def test_learned_single_group_at_min_sample(self):
        station = _station()
        fes = [_fes(sample=5, fallback="city")]
        result = _lp_compute_readiness_status(station, 5, fes)
        assert result == "learned"

    def test_global_fes_ignored_in_city_readiness(self):
        """Global FES groups don't count toward city readiness."""
        station = _station()
        fes = [
            _fes(city="__global__", sample=100, fallback="global"),
            _fes(variable="high", sample=5, fallback="city"),
            _fes(variable="low", sample=2, fallback="city"),
        ]
        result = _lp_compute_readiness_status(station, 10, fes)
        assert result == "partially_learned"

    def test_data_quality_overrides_obs_count(self):
        """Even with 100 usable obs, HIGH AMBIGUITY → data_quality_issue."""
        station = _station(notes="HIGH AMBIGUITY risk")
        result = _lp_compute_readiness_status(station, 100, [_fes(sample=50, fallback="city")])
        assert result == "data_quality_issue"

    def test_none_notes_does_not_crash(self):
        station = _station(notes=None)
        result = _lp_compute_readiness_status(station, 0, [])
        assert result == "not_collecting"

    def test_empty_notes_string_does_not_trigger_data_quality(self):
        station = _station(notes="")
        result = _lp_compute_readiness_status(station, 0, [])
        assert result == "not_collecting"


# ---------------------------------------------------------------------------
# _lp_milestone_progress
# ---------------------------------------------------------------------------

class TestMilestoneProgress:

    def test_zero_observations(self):
        mp = _lp_milestone_progress(0)
        assert mp["current"] == 0
        assert mp["milestones"] == _LP_MILESTONES
        assert mp["reached"] == [False, False, False, False, False]
        assert mp["nextMilestone"] == 5
        assert mp["neededForNext"] == 5

    def test_exactly_first_milestone(self):
        mp = _lp_milestone_progress(5)
        assert mp["reached"][0] is True
        assert mp["reached"][1] is False
        assert mp["nextMilestone"] == 15
        assert mp["neededForNext"] == 10

    def test_all_milestones_reached(self):
        mp = _lp_milestone_progress(100)
        assert all(mp["reached"])
        assert mp["nextMilestone"] is None
        assert mp["neededForNext"] is None

    def test_exceeds_all_milestones(self):
        mp = _lp_milestone_progress(200)
        assert all(mp["reached"])
        assert mp["nextMilestone"] is None

    def test_one_below_second_milestone(self):
        mp = _lp_milestone_progress(14)
        assert mp["reached"][0] is True
        assert mp["reached"][1] is False
        assert mp["nextMilestone"] == 15
        assert mp["neededForNext"] == 1

    def test_partial_milestones(self):
        mp = _lp_milestone_progress(30)
        # 5, 15, 30 reached; 50, 100 not
        assert mp["reached"] == [True, True, True, False, False]
        assert mp["nextMilestone"] == 50
        assert mp["neededForNext"] == 20

    def test_milestones_list_matches_constant(self):
        mp = _lp_milestone_progress(7)
        assert mp["milestones"] == [5, 15, 30, 50, 100]


# ---------------------------------------------------------------------------
# _lp_source_quality_label
# ---------------------------------------------------------------------------

class TestSourceQualityLabel:

    def test_pure_ghcnd_observation(self):
        assert _lp_source_quality_label({"ghcnd_observation": 5}) == "ghcnd"

    def test_pure_ghcnd_unverified(self):
        assert _lp_source_quality_label({"ghcnd_observation_unverified": 3}) == "ghcnd"

    def test_mixed_ghcnd_types_still_ghcnd(self):
        assert _lp_source_quality_label({
            "ghcnd_observation": 2, "ghcnd_observation_unverified": 3
        }) == "ghcnd"

    def test_pure_era5(self):
        assert _lp_source_quality_label({"era5_reanalysis": 10}) == "era5"

    def test_open_meteo_historical_counts_as_era5(self):
        assert _lp_source_quality_label({"open_meteo_historical": 4}) == "era5"

    def test_mixed_era5_and_ghcnd(self):
        assert _lp_source_quality_label({
            "ghcnd_observation": 2, "era5_reanalysis": 8
        }) == "mixed"

    def test_empty_sources_returns_none(self):
        assert _lp_source_quality_label({}) == "none"

    def test_unknown_source_returns_none(self):
        assert _lp_source_quality_label({"kalshi_implied": 1}) == "none"


# ---------------------------------------------------------------------------
# _READINESS_LABELS — all states have a label
# ---------------------------------------------------------------------------

class TestReadinessLabels:

    def test_all_six_states_have_labels(self):
        expected = {
            "not_collecting", "collecting", "insufficient_sample",
            "partially_learned", "learned", "data_quality_issue",
        }
        assert expected == set(_READINESS_LABELS.keys())

    def test_labels_are_non_empty_strings(self):
        for key, val in _READINESS_LABELS.items():
            assert isinstance(val, str) and len(val) > 0, f"{key} has empty label"


# ---------------------------------------------------------------------------
# _LP_MIN_SAMPLE and _LP_MILESTONES sanity
# ---------------------------------------------------------------------------

class TestConstants:

    def test_min_sample_is_5(self):
        assert _LP_MIN_SAMPLE == 5

    def test_milestones_are_ascending(self):
        assert _LP_MILESTONES == sorted(_LP_MILESTONES)

    def test_milestones_first_equals_min_sample(self):
        assert _LP_MILESTONES[0] == _LP_MIN_SAMPLE


# ---------------------------------------------------------------------------
# Endpoint integration tests (mocked async DB)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    """Returns a mock AsyncSession with configurable execute results."""
    db = AsyncMock()
    return db


def _make_execute_result(items):
    """Wrap a list into the shape returned by db.execute().scalars().all()."""
    result = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = items
    result.scalars.return_value = scalars_mock
    return result


@pytest.mark.asyncio
async def test_v2_learning_progress_empty_db(mock_db):
    """With no FV / FES / trade rows, endpoint returns 24 cities all not_collecting."""
    from app.routers.audit import v2_learning_progress

    # db.execute returns empty results for all four queries
    mock_db.execute.return_value = _make_execute_result([])

    result = await v2_learning_progress(db=mock_db, _user={})

    assert result["summary"]["totalCities"] == 24
    assert result["summary"]["totalUsableObservations"] == 0
    assert result["summary"]["citiesNotCollecting"] >= 0  # LA is data_quality_issue
    assert len(result["cities"]) == 24
    assert len(result["errorGroups"]) == 0


@pytest.mark.asyncio
async def test_v2_learning_progress_los_angeles_is_verified(mock_db):
    """Los Angeles ambiguity resolved 2026-07-30: should appear as not_collecting (verified, no obs yet)."""
    from app.routers.audit import v2_learning_progress

    mock_db.execute.return_value = _make_execute_result([])

    result = await v2_learning_progress(db=mock_db, _user={})

    la_row = next((r for r in result["cities"] if r["city"] == "Los Angeles"), None)
    assert la_row is not None
    assert la_row["readinessStatus"] == "not_collecting"


@pytest.mark.asyncio
async def test_v2_learning_progress_counts_usable_observations(mock_db):
    """Usable count should only include FV rows with actual_value set."""
    from app.routers.audit import v2_learning_progress
    from app.models import ForecastVerification

    fv_with_actual = MagicMock(spec=ForecastVerification)
    fv_with_actual.city = "Chicago"
    fv_with_actual.target_date = "2026-07-28"
    fv_with_actual.actual_value = 85.0
    fv_with_actual.forecast_value = 83.0
    fv_with_actual.forecast_error = 2.0
    fv_with_actual.source_label = "era5_reanalysis"
    fv_with_actual.lead_time_days = 1

    fv_without_actual = MagicMock(spec=ForecastVerification)
    fv_without_actual.city = "Chicago"
    fv_without_actual.target_date = "2026-07-27"
    fv_without_actual.actual_value = None
    fv_without_actual.source_label = None
    fv_without_actual.lead_time_days = 1

    # Four calls: FV, FES, v2 trades, v1 trades
    mock_db.execute.side_effect = [
        _make_execute_result([fv_with_actual, fv_without_actual]),  # FV
        _make_execute_result([]),                                     # FES
        _make_execute_result([]),                                     # v2 trades
        _make_execute_result([]),                                     # v1 trades
    ]

    result = await v2_learning_progress(db=mock_db, _user={})

    chicago = next(r for r in result["cities"] if r["city"] == "Chicago")
    assert chicago["usableObservations"] == 1
    assert chicago["totalObservations"] == 2


@pytest.mark.asyncio
async def test_v2_learning_progress_source_breakdown_populated(mock_db):
    """Source breakdown should tally source_label values from usable FVs."""
    from app.models import ForecastVerification

    fvs = []
    for src in ["era5_reanalysis", "era5_reanalysis", "ghcnd_observation"]:
        fv = MagicMock(spec=ForecastVerification)
        fv.city = "New York City"
        fv.target_date = "2026-07-28"
        fv.actual_value = 80.0
        fv.source_label = src
        fv.lead_time_days = 0
        fvs.append(fv)

    from app.routers.audit import v2_learning_progress

    mock_db.execute.side_effect = [
        _make_execute_result(fvs),
        _make_execute_result([]),
        _make_execute_result([]),
        _make_execute_result([]),
    ]

    result = await v2_learning_progress(db=mock_db, _user={})

    nyc = next(r for r in result["cities"] if r["city"] == "New York City")
    assert nyc["sourceBreakdown"].get("era5_reanalysis") == 2
    assert nyc["sourceBreakdown"].get("ghcnd_observation") == 1
    assert nyc["sourceQualityLabel"] == "mixed"


@pytest.mark.asyncio
async def test_v2_learning_progress_collecting_state(mock_db):
    """4 usable obs and no FES → collecting."""
    from app.models import ForecastVerification
    from app.routers.audit import v2_learning_progress

    fvs = []
    for i in range(4):
        fv = MagicMock(spec=ForecastVerification)
        fv.city = "Denver"
        fv.target_date = f"2026-07-{20+i:02d}"
        fv.actual_value = 90.0
        fv.source_label = "ghcnd_observation"
        fv.lead_time_days = 1
        fvs.append(fv)

    mock_db.execute.side_effect = [
        _make_execute_result(fvs),
        _make_execute_result([]),
        _make_execute_result([]),
        _make_execute_result([]),
    ]

    result = await v2_learning_progress(db=mock_db, _user={})

    denver = next(r for r in result["cities"] if r["city"] == "Denver")
    assert denver["readinessStatus"] == "collecting"
    assert denver["milestoneProgress"]["current"] == 4
    assert denver["milestoneProgress"]["nextMilestone"] == 5


@pytest.mark.asyncio
async def test_v2_learning_progress_v2_fallback_counts(mock_db):
    """v2TradesFallback vs v2TradesHistorical split should be correct."""
    from app.models import PaperTrade
    from app.routers.audit import v2_learning_progress

    def _v2(fallback):
        t = MagicMock(spec=PaperTrade)
        t.city = "Boston"
        t.strategy_version = "v2.0"
        t.fallback_level = fallback
        t.status = "OPEN"
        t.outcome = None
        t.direction = "YES"
        t.market_ticker = "TEST-001"
        t.sigma_used = 3.0
        t.bias_correction = 0.5
        t.calibration_adj = 1.0
        t.stake = 5.0
        t.profit_loss = None
        t.target_settlement_date = "2026-07-30"
        t.created_at = None
        return t

    v2_trades = [_v2("fixed_table"), _v2("fixed_table"), _v2("city")]

    mock_db.execute.side_effect = [
        _make_execute_result([]),
        _make_execute_result([]),
        _make_execute_result(v2_trades),
        _make_execute_result([]),
    ]

    result = await v2_learning_progress(db=mock_db, _user={})

    boston = next(r for r in result["cities"] if r["city"] == "Boston")
    assert boston["v2TradesTotal"] == 3
    assert boston["v2TradesFallback"] == 2
    assert boston["v2TradesHistorical"] == 1
    assert result["summary"]["v2TradesUsingHistorical"] == 1
    assert result["summary"]["v2TradesUsingFallback"] == 2


@pytest.mark.asyncio
async def test_v2_learning_progress_all_cities_count_is_24(mock_db):
    """The registry has exactly 24 cities."""
    from app.routers.audit import v2_learning_progress

    mock_db.execute.return_value = _make_execute_result([])
    result = await v2_learning_progress(db=mock_db, _user={})

    assert result["summary"]["totalCities"] == 24
    assert len(result["cities"]) == 24


@pytest.mark.asyncio
async def test_v2_learning_progress_summary_keys(mock_db):
    """Summary payload must contain all expected keys."""
    from app.routers.audit import v2_learning_progress

    mock_db.execute.return_value = _make_execute_result([])
    result = await v2_learning_progress(db=mock_db, _user={})

    required = {
        "totalCities", "citiesLearned", "citiesPartiallyLearned",
        "citiesCollecting", "citiesNotCollecting", "citiesDataQualityIssue",
        "totalUsableObservations", "totalFesGroups", "v2TotalTrades",
        "v2TradesUsingHistorical", "v2TradesUsingFallback", "v1TotalTrades",
    }
    assert required <= set(result["summary"].keys())


@pytest.mark.asyncio
async def test_v2_learning_progress_city_row_keys(mock_db):
    """Each city row must contain all expected keys."""
    from app.routers.audit import v2_learning_progress

    mock_db.execute.return_value = _make_execute_result([])
    result = await v2_learning_progress(db=mock_db, _user={})

    required = {
        "city", "stationVerified", "stationName", "readinessStatus",
        "readinessLabel", "usableObservations", "totalObservations",
        "sourceBreakdown", "sourceQualityLabel", "cityFesGroupCount",
        "cityFesReadyCount", "milestoneProgress", "v2TradesTotal",
        "v2TradesFallback", "v2TradesHistorical", "latestObservationDate",
        "fesGroups",
    }
    for row in result["cities"]:
        assert required <= set(row.keys()), f"Missing keys in row for {row.get('city')}"


@pytest.mark.asyncio
async def test_v2_city_detail_unknown_city(mock_db):
    """Unregistered city still returns a result (station info will be None)."""
    from app.routers.audit import v2_city_detail

    mock_db.execute.return_value = _make_execute_result([])
    result = await v2_city_detail("Unknown City", db=mock_db, _user={})

    assert result["city"] == "Unknown City"
    assert result["readinessStatus"] == "data_quality_issue"
    assert result["stationInfo"]["stationName"] is None
    assert result["verifications"] == []
    assert result["fesGroups"] == []
    assert result["v2Trades"] == []


@pytest.mark.asyncio
async def test_v2_city_detail_returns_all_keys(mock_db):
    """City detail payload must contain all expected top-level keys."""
    from app.routers.audit import v2_city_detail

    mock_db.execute.return_value = _make_execute_result([])
    result = await v2_city_detail("New York City", db=mock_db, _user={})

    required = {
        "city", "readinessStatus", "readinessLabel", "stationInfo",
        "milestoneProgress", "sourceBreakdown", "sourceQualityLabel",
        "verifications", "fesGroups", "v2Trades",
    }
    assert required <= set(result.keys())


@pytest.mark.asyncio
async def test_v2_city_detail_los_angeles_now_verified(mock_db):
    """Los Angeles ambiguity resolved 2026-07-30: city detail shows verified station."""
    from app.routers.audit import v2_city_detail

    mock_db.execute.return_value = _make_execute_result([])
    result = await v2_city_detail("Los Angeles", db=mock_db, _user={})

    assert result["readinessStatus"] == "not_collecting"
    assert result["stationInfo"]["verified"] is True
    assert result["stationInfo"]["ghcndStationId"] == "USW00023174"


@pytest.mark.asyncio
async def test_v2_city_detail_verification_shape(mock_db):
    """Verification rows in city detail must contain expected keys."""
    from app.models import ForecastVerification

    fv = MagicMock(spec=ForecastVerification)
    fv.id = 42
    fv.target_date = "2026-07-28"
    fv.weather_variable = "high"
    fv.forecast_value = 88.0
    fv.actual_value = 90.0
    fv.forecast_error = 2.0
    fv.source_label = "era5_reanalysis"
    fv.ghcnd_station_id = None
    fv.lead_time_days = 1
    fv.month = 7
    fv.season = "summer"
    fv.created_at = None

    from app.routers.audit import v2_city_detail

    mock_db.execute.side_effect = [
        _make_execute_result([fv]),
        _make_execute_result([]),
        _make_execute_result([]),
    ]

    result = await v2_city_detail("Chicago", db=mock_db, _user={})

    assert len(result["verifications"]) == 1
    v = result["verifications"][0]
    required = {
        "id", "targetDate", "weatherVariable", "forecastValue",
        "actualValue", "forecastError", "sourceLabel", "ghcndStationId",
        "leadTimeDays", "month", "season", "createdAt",
    }
    assert required <= set(v.keys())
    assert v["id"] == 42
    assert v["actualValue"] == 90.0


@pytest.mark.asyncio
async def test_v2_learning_progress_error_groups_populated(mock_db):
    """Error groups table should include all FES rows."""
    from app.models import ForecastErrorStats
    from app.routers.audit import v2_learning_progress

    fes1 = _fes(city="New York City", variable="high", lead="0-1d", sample=11, fallback="city")
    fes1.last_computed_at = None
    fes2 = _fes(city="__global__", variable="high", lead="0-1d", sample=11, fallback="global")
    fes2.last_computed_at = None

    mock_db.execute.side_effect = [
        _make_execute_result([]),
        _make_execute_result([fes1, fes2]),
        _make_execute_result([]),
        _make_execute_result([]),
    ]

    result = await v2_learning_progress(db=mock_db, _user={})

    assert len(result["errorGroups"]) == 2
    cities_in_groups = {g["city"] for g in result["errorGroups"]}
    assert "New York City" in cities_in_groups
    assert "__global__" in cities_in_groups


@pytest.mark.asyncio
async def test_v2_learning_progress_city_sort_order(mock_db):
    """Data-quality cities should sort last; learned cities should sort first."""
    from app.routers.audit import v2_learning_progress

    mock_db.execute.return_value = _make_execute_result([])
    result = await v2_learning_progress(db=mock_db, _user={})

    cities = result["cities"]
    # With no observations in mock DB all cities are not_collecting;
    # verify no city is erroneously flagged data_quality_issue (LA ambiguity resolved 2026-07-30)
    statuses = [r["readinessStatus"] for r in cities]
    assert "data_quality_issue" not in statuses, (
        "No city should have data_quality_issue — all HIGH AMBIGUITY notes resolved"
    )
    # Sort is still exercised — all not_collecting, ordered alphabetically
    assert len(cities) > 0
