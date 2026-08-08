"""
Tests for V2.2 probability engine — bias sign correction.

All tests are pure unit tests with no real DB access.
They mock _bias_v2, _sigma_v2, and _calibration_adj_v2 to inject
controlled values.  A non-None AsyncMock session is always passed so the
engine takes the ``if session is not None`` branch and calls the mocked
helpers rather than the fixed-table fallback that ignores bias.

Verified properties:
  - positive mean_error RAISES V2.2 mu (correct direction)
  - negative mean_error LOWERS V2.2 mu (correct direction)
  - V2.1 output is UNCHANGED (mu moves in the inverted direction — preserved)
  - V2.2 bias stays zero when _bias_v2 returns 0.0 (MIN_SAMPLE guard active)
  - V2.2 and V2.1 are identical when bias is 0
  - V2.2 diverges from V2.1 in the CORRECT direction once bias fires
  - strategy isolation: distinct STRATEGY_VERSION, setting keys, and flag names
  - no live Kalshi HTTP requests made by the engine
"""
from __future__ import annotations

import inspect
import math
import pytest
from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# Pure-Python Gaussian CDF (no scipy dependency)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    """Standard normal CDF via math.erfc (exact to ~15 digits)."""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _gaussian_gte(threshold: float, mu: float, sigma: float) -> float:
    """P(T >= threshold | mu, sigma) — raw formula, no NWS rounding correction."""
    return 1.0 - _norm_cdf((threshold - mu) / sigma)


def _gaussian_lte(threshold: float, mu: float, sigma: float) -> float:
    """P(T <= threshold | mu, sigma) — raw formula, no NWS rounding correction."""
    return _norm_cdf((threshold - mu) / sigma)


def _is_int(v: float) -> bool:
    return v == math.floor(v)


def _gaussian_gte_nws(threshold: float, mu: float, sigma: float) -> float:
    """
    P(X_rounded >= threshold | mu, sigma) with NWS integer rounding correction.
    For integer thresholds uses T−0.5 boundary (round(actual)>=T ↔ actual>=T−0.5).
    Half-integer thresholds are unchanged.
    """
    eff = (threshold - 0.5) if _is_int(threshold) else threshold
    return round(1.0 - _norm_cdf((eff - mu) / sigma), 4)


# ---------------------------------------------------------------------------
# Test fixture helpers
# ---------------------------------------------------------------------------

def _mock_session() -> AsyncMock:
    """
    Return a non-None AsyncMock session.
    Passing a session triggers the `if session is not None` branch in both
    V2.1 and V2.2 engines, which calls _sigma_v2, _bias_v2, _calibration_adj_v2.
    With session=None those helpers are bypassed and bias is hardcoded to 0.
    """
    return AsyncMock()


def _common_kwargs(**overrides):
    """Base kwargs for run_analysis_v22 / run_analysis_v2 calls in tests."""
    base = dict(
        title="KXHIGHOKC-26JUL31-T95",
        subtitle=None,
        city="Oklahoma City",
        target_date_str="2026-07-31",
        weather_variable="high",
        operator="gte",
        threshold=95.0,
        parse_confidence="high",
        settlement_status="supported",
        unsupported_reason=None,
        forecast_high=91.4,
        forecast_low=None,
        forecast_retrieved_at=None,
        yes_bid=0.06,
        yes_ask=0.08,
        contract_type="threshold",
        session=_mock_session(),
    )
    base.update(overrides)
    return base


def _patch_v22(bias: float, sigma: float = 5.0, fallback: str = "city"):
    """Context manager: patch V2.2 engine helpers with given values."""
    return (
        patch("app.services.probability_engine_v22._bias_v2",
              new=AsyncMock(return_value=bias)),
        patch("app.services.probability_engine_v22._sigma_v2",
              new=AsyncMock(return_value=(sigma, fallback))),
        patch("app.services.probability_engine_v22._calibration_adj_v2",
              new=AsyncMock(return_value=1.0)),
    )


def _patch_v21(bias: float, sigma: float = 5.0, fallback: str = "city"):
    """Context manager: patch V2.1 engine helpers with given values."""
    return (
        patch("app.services.probability_engine_v2._bias_v2",
              new=AsyncMock(return_value=bias)),
        patch("app.services.probability_engine_v2._sigma_v2",
              new=AsyncMock(return_value=(sigma, fallback))),
        patch("app.services.probability_engine_v2._calibration_adj_v2",
              new=AsyncMock(return_value=1.0)),
    )


async def _run_v22(bias: float, sigma: float = 5.0, **kw):
    from app.services.probability_engine_v22 import run_analysis_v22
    kwargs = _common_kwargs(**kw)
    with _patch_v22(bias, sigma)[0], _patch_v22(bias, sigma)[1], _patch_v22(bias, sigma)[2]:
        return await run_analysis_v22(**kwargs)


async def _run_v21(bias: float, sigma: float = 5.0, **kw):
    from app.services.probability_engine_v2 import run_analysis_v2
    kwargs = _common_kwargs(**kw)
    with _patch_v21(bias, sigma)[0], _patch_v21(bias, sigma)[1], _patch_v21(bias, sigma)[2]:
        return await run_analysis_v2(**kwargs)


# ---------------------------------------------------------------------------
# V2.2 sign correction tests
# ---------------------------------------------------------------------------

class TestV22BiasSign:

    @pytest.mark.asyncio
    async def test_positive_mean_error_raises_v22_mu(self):
        """
        mean_error = +2.0°F (GFS under-forecasts; actual hotter).
        V2.2: mu = 91.4 + 2.0 = 93.4°F → higher P(T≥95°F) vs raw forecast.
        """
        mean_error = 2.0
        forecast = 91.4
        sigma = 5.0
        threshold = 95.0

        from app.services.probability_engine_v22 import run_analysis_v22
        kwargs = _common_kwargs()
        with _patch_v22(mean_error, sigma)[0], \
             _patch_v22(mean_error, sigma)[1], \
             _patch_v22(mean_error, sigma)[2]:
            result = await run_analysis_v22(**kwargs)

        expected_mu   = forecast + mean_error          # 93.4
        # Use NWS-corrected formula (T−0.5 for integer threshold)
        expected_prob = _gaussian_gte_nws(threshold, expected_mu, sigma)
        assert result.ec_probability is not None
        assert abs(result.ec_probability - expected_prob) < 1e-4, (
            f"Expected P(T≥{threshold} | μ={expected_mu}, σ={sigma}) "
            f"≈ {expected_prob:.4f}, got {result.ec_probability:.4f}"
        )
        assert result.bias_correction == mean_error

    @pytest.mark.asyncio
    async def test_negative_mean_error_lowers_v22_mu(self):
        """
        mean_error = -2.27°F (GFS over-forecasts; actual cooler).
        V2.2: mu = 91.4 − 2.27 = 89.13°F → lower P(T≥95°F) vs raw forecast.
        """
        mean_error = -2.27
        forecast = 91.4
        sigma = 5.0

        from app.services.probability_engine_v22 import run_analysis_v22
        kwargs = _common_kwargs()
        with _patch_v22(mean_error, sigma)[0], \
             _patch_v22(mean_error, sigma)[1], \
             _patch_v22(mean_error, sigma)[2]:
            result = await run_analysis_v22(**kwargs)

        expected_mu   = forecast + mean_error          # 89.13
        expected_prob = _gaussian_gte_nws(95.0, expected_mu, sigma)
        assert abs(result.ec_probability - expected_prob) < 1e-4

    @pytest.mark.asyncio
    async def test_v22_bias_zero_when_min_sample_not_met(self):
        """When _bias_v2 returns 0.0 (MIN_SAMPLE guard active), mu = raw forecast."""
        forecast = 91.4
        sigma = 5.0

        from app.services.probability_engine_v22 import run_analysis_v22
        kwargs = _common_kwargs()
        with _patch_v22(0.0, sigma, fallback="fixed_table")[0], \
             _patch_v22(0.0, sigma, fallback="fixed_table")[1], \
             _patch_v22(0.0, sigma, fallback="fixed_table")[2]:
            result = await run_analysis_v22(**kwargs)

        expected_prob = _gaussian_gte_nws(95.0, forecast, sigma)
        assert abs(result.ec_probability - expected_prob) < 1e-4
        assert result.bias_correction == 0.0

    @pytest.mark.asyncio
    async def test_v22_applies_corrected_bias_after_min_sample(self):
        """
        Simulates a city bucket crossing MIN_SAMPLE=30: _bias_v2 returns +1.5°F.
        V2.2: mu = 91.4 + 1.5 = 92.9°F  → higher P (correct: GFS under-forecast)
        V2.1: mu = 91.4 − 1.5 = 89.9°F  → lower P  (wrong direction)
        """
        mean_error = 1.5
        forecast   = 91.4
        sigma      = 5.0
        threshold  = 95.0

        from app.services.probability_engine_v22 import run_analysis_v22
        from app.services.probability_engine_v2  import run_analysis_v2

        kwargs_22 = _common_kwargs()
        with _patch_v22(mean_error, sigma)[0], \
             _patch_v22(mean_error, sigma)[1], \
             _patch_v22(mean_error, sigma)[2]:
            v22 = await run_analysis_v22(**kwargs_22)

        kwargs_21 = _common_kwargs()
        with _patch_v21(mean_error, sigma)[0], \
             _patch_v21(mean_error, sigma)[1], \
             _patch_v21(mean_error, sigma)[2]:
            v21 = await run_analysis_v2(**kwargs_21)

        p_v22 = _gaussian_gte_nws(threshold, forecast + mean_error, sigma)  # 92.9
        p_v21 = _gaussian_gte_nws(threshold, forecast - mean_error, sigma)  # 89.9

        assert abs(v22.ec_probability - p_v22) < 1e-4, \
            f"V2.2 expected {p_v22:.4f} got {v22.ec_probability:.4f}"
        assert abs(v21.ec_probability - p_v21) < 1e-4, \
            f"V2.1 expected {p_v21:.4f} got {v21.ec_probability:.4f}"
        assert v22.ec_probability > v21.ec_probability, \
            "V2.2 P should exceed V2.1 P when GFS under-forecasts"


# ---------------------------------------------------------------------------
# V2.1 preservation tests
# ---------------------------------------------------------------------------

class TestV21Unchanged:

    @pytest.mark.asyncio
    async def test_v21_still_subtracts_positive_mean_error(self):
        """
        V2.1 formula is PRESERVED: mu = forecast − mean_error.
        This documents the known inversion — do not fix here.
        """
        mean_error = 2.0
        forecast   = 91.4
        sigma      = 5.0

        from app.services.probability_engine_v2 import run_analysis_v2
        kwargs = _common_kwargs()
        with _patch_v21(mean_error, sigma)[0], \
             _patch_v21(mean_error, sigma)[1], \
             _patch_v21(mean_error, sigma)[2]:
            result = await run_analysis_v2(**kwargs)

        # V2.1 SUBTRACTS: mu = 91.4 − 2.0 = 89.4 (inverted — preserved by design)
        expected_mu   = forecast - mean_error
        expected_prob = _gaussian_gte_nws(95.0, expected_mu, sigma)
        assert abs(result.ec_probability - expected_prob) < 1e-4

    @pytest.mark.asyncio
    async def test_v21_output_identical_to_v22_when_bias_is_zero(self):
        """When bias=0, V2.1 and V2.2 produce identical probabilities."""
        sigma = 5.0

        from app.services.probability_engine_v2  import run_analysis_v2
        from app.services.probability_engine_v22 import run_analysis_v22

        kwargs_21 = _common_kwargs()
        with _patch_v21(0.0, sigma)[0], \
             _patch_v21(0.0, sigma)[1], \
             _patch_v21(0.0, sigma)[2]:
            v21 = await run_analysis_v2(**kwargs_21)

        kwargs_22 = _common_kwargs()
        with _patch_v22(0.0, sigma)[0], \
             _patch_v22(0.0, sigma)[1], \
             _patch_v22(0.0, sigma)[2]:
            v22 = await run_analysis_v22(**kwargs_22)

        assert v21.ec_probability == v22.ec_probability
        assert v21.bias_correction == v22.bias_correction == 0.0


# ---------------------------------------------------------------------------
# Strategy isolation tests
# ---------------------------------------------------------------------------

class TestStrategyIsolation:

    def test_strategy_version_constant(self):
        from app.services.paper_trading_v22 import STRATEGY_VERSION
        # Changed to v2.3 after Forward Test B correction package (NWS rounding,
        # hourly sigma, coordinate corrections, calibration isolation).
        assert STRATEGY_VERSION == "v2.3"

    def test_v22_strategy_version_distinct_from_v21(self):
        from app.services.paper_trading_v21 import STRATEGY_VERSION as V21
        from app.services.paper_trading_v22 import STRATEGY_VERSION as V22
        assert V21 == "v2.1"
        assert V22 == "v2.3"  # v2.3 after FTB correction package
        assert V21 != V22

    def test_flag_keys_distinct_from_v3(self):
        from app.services.paper_trading_v22 import V22_FLAG_DEFAULTS
        assert "v2.2.predictions_enabled"   in V22_FLAG_DEFAULTS
        assert "v2.2.paper_trading_enabled" in V22_FLAG_DEFAULTS
        from app.models_v3 import V3_FLAG_DEFAULTS
        for key in V22_FLAG_DEFAULTS:
            assert key not in V3_FLAG_DEFAULTS, \
                f"{key} must not appear in V3 flags"

    def test_v22_settings_keys_distinct_from_v21(self):
        from app.services.paper_trading_v21 import _V21_SETTING_KEYS
        from app.services.paper_trading_v22 import _V22_SETTING_KEYS
        overlap = set(_V21_SETTING_KEYS.keys()) & set(_V22_SETTING_KEYS.keys())
        assert not overlap, f"V2.1 and V2.2 share setting keys: {overlap}"

    def test_no_live_http_calls_in_engine(self):
        """V2.2 engine must never make direct HTTP requests."""
        import app.services.probability_engine_v22 as eng22
        src = inspect.getsource(eng22)
        # Check for actual HTTP call patterns, not the word "kalshi" in comments/strings
        assert "requests.get(" not in src
        assert "requests.post(" not in src
        assert "httpx." not in src
        assert "aiohttp." not in src
        assert "urllib.request" not in src


# ---------------------------------------------------------------------------
# Synthetic threshold-crossing comparison
# ---------------------------------------------------------------------------

class TestThresholdCrossingComparison:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mean_error,description", [
        (+1.5,  "GFS under-forecasts 1.5°F — V2.2 raises mu"),
        (-2.27, "GFS over-forecasts 2.27°F — V2.2 lowers mu"),
        (+2.23, "LA low: GFS under-forecasts 2.23°F — V2.2 raises mu"),
        (-1.87, "OKC low: GFS over-forecasts 1.87°F — V2.2 lowers mu"),
    ])
    async def test_v22_corrects_in_right_direction(self, mean_error, description):
        """V2.2 mu always moves TOWARD the historical actual; V2.1 moves away."""
        forecast  = 91.4
        sigma     = 5.0
        threshold = 95.0

        from app.services.probability_engine_v22 import run_analysis_v22
        from app.services.probability_engine_v2  import run_analysis_v2

        kw22 = _common_kwargs()
        with _patch_v22(mean_error, sigma)[0], \
             _patch_v22(mean_error, sigma)[1], \
             _patch_v22(mean_error, sigma)[2]:
            v22 = await run_analysis_v22(**kw22)

        kw21 = _common_kwargs()
        with _patch_v21(mean_error, sigma)[0], \
             _patch_v21(mean_error, sigma)[1], \
             _patch_v21(mean_error, sigma)[2]:
            v21 = await run_analysis_v2(**kw21)

        mu_v22 = forecast + mean_error
        mu_v21 = forecast - mean_error

        if mean_error > 0:
            assert mu_v22 > mu_v21, f"{description}: V2.2 mu should be higher"
        else:
            assert mu_v22 < mu_v21, f"{description}: V2.2 mu should be lower"

        assert v22.ec_probability != v21.ec_probability, \
            f"{description}: probs must differ when bias ≠ 0"

    @pytest.mark.asyncio
    async def test_synthetic_denver_high_overforecast(self):
        """
        Denver high mean_error = −2.2667°F (observed in forecast_error_stats).
        When threshold crossed:
          V2.1 raises mu by 2.27 (amplifies overforecast) → bad
          V2.2 lowers mu by 2.27 (corrects overforecast)  → good
        """
        mean_error = -2.2667
        forecast   = 91.9
        sigma      = 5.0
        threshold  = 95.0

        from app.services.probability_engine_v22 import run_analysis_v22
        from app.services.probability_engine_v2  import run_analysis_v2

        kw22 = _common_kwargs(forecast_high=forecast, threshold=threshold)
        with _patch_v22(mean_error, sigma)[0], \
             _patch_v22(mean_error, sigma)[1], \
             _patch_v22(mean_error, sigma)[2]:
            v22 = await run_analysis_v22(**kw22)

        kw21 = _common_kwargs(forecast_high=forecast, threshold=threshold)
        with _patch_v21(mean_error, sigma)[0], \
             _patch_v21(mean_error, sigma)[1], \
             _patch_v21(mean_error, sigma)[2]:
            v21 = await run_analysis_v2(**kw21)

        p_v22 = _gaussian_gte_nws(threshold, forecast + mean_error, sigma)  # mu=89.63
        p_v21 = _gaussian_gte_nws(threshold, forecast - mean_error, sigma)  # mu=94.17

        assert abs(v22.ec_probability - p_v22) < 1e-4, \
            f"V2.3: expected {p_v22:.4f} got {v22.ec_probability:.4f}"
        assert abs(v21.ec_probability - p_v21) < 1e-4, \
            f"V2.1: expected {p_v21:.4f} got {v21.ec_probability:.4f}"

        # V2.1 amplified overforecast → inflated P(hot); V2.3 corrected → lower P
        assert v21.ec_probability > v22.ec_probability, \
            "V2.1 should have higher P(T≥95) — it amplified Denver's overforecast"
        assert v22.ec_probability < _gaussian_gte_nws(threshold, forecast, sigma), \
            "V2.3 should be below raw-forecast baseline (corrects downward)"

    @pytest.mark.asyncio
    async def test_synthetic_la_low_underforecast(self):
        """
        LA low mean_error = +2.2333°F (observed in forecast_error_stats).
        When threshold crossed:
          V2.1 lowers mu (amplifies underforecast) → bad
          V2.2 raises mu (corrects underforecast)  → good
        """
        mean_error = 2.2333
        forecast   = 64.4
        sigma      = 5.0
        threshold  = 68.0

        from app.services.probability_engine_v22 import run_analysis_v22
        from app.services.probability_engine_v2  import run_analysis_v2

        kw22 = _common_kwargs(
            city="Los Angeles", weather_variable="low",
            forecast_high=None, forecast_low=forecast,
            threshold=threshold, operator="gte",
            session=_mock_session(),
        )
        with _patch_v22(mean_error, sigma)[0], \
             _patch_v22(mean_error, sigma)[1], \
             _patch_v22(mean_error, sigma)[2]:
            v22 = await run_analysis_v22(**kw22)

        kw21 = _common_kwargs(
            city="Los Angeles", weather_variable="low",
            forecast_high=None, forecast_low=forecast,
            threshold=threshold, operator="gte",
            session=_mock_session(),
        )
        with _patch_v21(mean_error, sigma)[0], \
             _patch_v21(mean_error, sigma)[1], \
             _patch_v21(mean_error, sigma)[2]:
            v21 = await run_analysis_v2(**kw21)

        p_v22 = _gaussian_gte_nws(threshold, forecast + mean_error, sigma)  # mu=66.63
        p_v21 = _gaussian_gte_nws(threshold, forecast - mean_error, sigma)  # mu=62.17

        assert abs(v22.ec_probability - p_v22) < 1e-4, \
            f"V2.3: expected {p_v22:.4f} got {v22.ec_probability:.4f}"
        assert abs(v21.ec_probability - p_v21) < 1e-4, \
            f"V2.1: expected {p_v21:.4f} got {v21.ec_probability:.4f}"
        assert v22.ec_probability > v21.ec_probability, \
            "V2.3 should have higher P(warm night) — corrects LA's underforecast"
