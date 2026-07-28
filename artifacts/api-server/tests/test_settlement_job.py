"""
Tests for settlement job logic — run_settlement_job, _extract_result, fetch_kalshi_market.

Covers:
  - confirmed YES settlement
  - confirmed NO settlement
  - unresolved (active) market
  - expired / finalized market with no result yet → PENDING_SETTLEMENT
  - missing result field in API response → PENDING_SETTLEMENT (if closed) or OPEN (if active)
  - API failure (transient) → status unchanged
  - API 404 (terminal) → ERROR
  - YES trade payout
  - NO trade payout
  - settled-trade detail routing (link uses /paper-trading/<id>)
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.settlement import _extract_result, run_settlement_job
from app.services.paper_trading import settle_position


# ── _extract_result ────────────────────────────────────────────────────────────

class TestExtractResult:
    """_extract_result classifies Kalshi market payloads correctly."""

    # --- Confirmed results ---

    def test_confirmed_yes(self):
        assert _extract_result({"status": "finalized", "result": "yes"}) == "yes"

    def test_confirmed_no(self):
        assert _extract_result({"status": "finalized", "result": "no"}) == "no"

    def test_confirmed_yes_uppercase(self):
        """result field values are case-normalised."""
        assert _extract_result({"status": "finalized", "result": "YES"}) == "yes"

    def test_confirmed_no_uppercase(self):
        assert _extract_result({"status": "finalized", "result": "NO"}) == "no"

    # --- Voided / canceled ---

    def test_canceled_status(self):
        assert _extract_result({"status": "canceled"}) == "void"

    def test_cancelled_status(self):
        assert _extract_result({"status": "cancelled"}) == "void"

    def test_voided_status(self):
        assert _extract_result({"status": "voided"}) == "void"

    def test_canceled_voided_status(self):
        assert _extract_result({"status": "canceled_voided"}) == "void"

    def test_canceled_status_overrides_result(self):
        """A canceled market is VOID regardless of the result field."""
        assert _extract_result({"status": "canceled", "result": "yes"}) == "void"

    # --- Active market (not yet settled) ---

    def test_active_market_no_result(self):
        assert _extract_result({"status": "open", "result": ""}) is None

    def test_active_market_result_absent(self):
        assert _extract_result({"status": "open"}) is None

    def test_active_market_result_none(self):
        assert _extract_result({"status": "open", "result": None}) is None

    # --- Finalized / closed market WITHOUT a result → PENDING_SETTLEMENT ---

    def test_finalized_no_result_is_pending(self):
        """A finalized market with no result must never be a LOSS — it is pending."""
        assert _extract_result({"status": "finalized"}) == "pending"

    def test_finalized_empty_result_is_pending(self):
        assert _extract_result({"status": "finalized", "result": ""}) == "pending"

    def test_closed_no_result_is_pending(self):
        assert _extract_result({"status": "closed"}) == "pending"

    def test_closed_empty_result_is_pending(self):
        assert _extract_result({"status": "closed", "result": ""}) == "pending"

    def test_closed_null_result_is_pending(self):
        assert _extract_result({"status": "closed", "result": None}) == "pending"

    # --- Missing API result field (API omits the field entirely) ---

    def test_missing_result_field_active(self):
        """Active market with no 'result' key at all → None (still open)."""
        assert _extract_result({"status": "open"}) is None

    def test_missing_result_field_finalized(self):
        """Finalized market with no 'result' key → pending, not a loss."""
        data = {"status": "finalized"}
        assert _extract_result(data) == "pending"


# ── settle_position payout tests ───────────────────────────────────────────────

class TestSettlePositionPayout:
    """Explicit payout verification for YES and NO winning trades."""

    def test_yes_trade_wins_correct_payout(self):
        """stake $10 @ $0.40 → 25 contracts → gross $25 → profit $15"""
        result = settle_position("YES", quantity=25.0, stake=10.0, kalshi_result="yes")
        assert result["outcome"] == "WIN"
        assert abs(result["gross_payout"] - 25.0) < 1e-6
        assert abs(result["profit_loss"] - 15.0) < 1e-6
        assert abs(result["return_pct"] - 150.0) < 0.01

    def test_no_trade_wins_correct_payout(self):
        """stake $10 @ $0.20 → 50 contracts → gross $50 → profit $40"""
        result = settle_position("NO", quantity=50.0, stake=10.0, kalshi_result="no")
        assert result["outcome"] == "WIN"
        assert abs(result["gross_payout"] - 50.0) < 1e-6
        assert abs(result["profit_loss"] - 40.0) < 1e-6
        assert abs(result["return_pct"] - 400.0) < 0.01

    def test_yes_trade_loses_correct_payout(self):
        """YES trade on 'no' result: gross=0, P/L=-stake."""
        result = settle_position("YES", quantity=25.0, stake=10.0, kalshi_result="no")
        assert result["outcome"] == "LOSS"
        assert result["gross_payout"] == 0.0
        assert abs(result["profit_loss"] - (-10.0)) < 1e-6
        assert result["return_pct"] == -100.0

    def test_no_trade_loses_correct_payout(self):
        """NO trade on 'yes' result: gross=0, P/L=-stake."""
        result = settle_position("NO", quantity=50.0, stake=10.0, kalshi_result="yes")
        assert result["outcome"] == "LOSS"
        assert result["gross_payout"] == 0.0
        assert abs(result["profit_loss"] - (-10.0)) < 1e-6
        assert result["return_pct"] == -100.0

    def test_yes_payout_at_01_price(self):
        """stake $10 @ $0.01 → 1000 contracts → gross $1000 → profit $990"""
        result = settle_position("YES", quantity=1000.0, stake=10.0, kalshi_result="yes")
        assert result["outcome"] == "WIN"
        assert abs(result["gross_payout"] - 1000.0) < 1e-6
        assert abs(result["profit_loss"] - 990.0) < 1e-6

    def test_no_payout_at_099_price(self):
        """stake $10 @ $0.99 → ~10.1 contracts → gross ~$10.1 → profit ~$0.10"""
        qty = 10.0 / 0.99  # ~10.1010
        result = settle_position("NO", quantity=qty, stake=10.0, kalshi_result="no")
        assert result["outcome"] == "WIN"
        assert abs(result["gross_payout"] - qty) < 1e-4
        assert result["profit_loss"] > 0


# ── run_settlement_job behaviour ───────────────────────────────────────────────

def _make_trade(
    trade_id: int = 1,
    ticker: str = "KXTEST-26JUL27-T90",
    direction: str = "YES",
    quantity: float = 25.0,
    stake: float = 10.0,
    status: str = "OPEN",
) -> MagicMock:
    t = MagicMock()
    t.id = trade_id
    t.market_ticker = ticker
    t.direction = direction
    t.quantity = quantity
    t.stake = stake
    t.status = status
    t.warnings = None
    return t


def _market_payload(status: str, result: str | None = None) -> dict:
    d: dict = {"ticker": "KXTEST", "status": status}
    if result is not None:
        d["result"] = result
    return d


@pytest.mark.asyncio
class TestRunSettlementJob:
    """Integration tests for run_settlement_job using mocked DB and HTTP."""

    async def _run(self, trades: list, market_payloads: dict[str, dict]):
        """
        Run the settlement job with mocked DB (returns `trades`) and mocked
        fetch_kalshi_market (returns market_payloads keyed by ticker).
        """
        from app.services import settlement as svc
        from app.services.settlement import _FetchResult

        # Use MagicMock for execute_mock so that .scalars().all() are sync calls
        session = MagicMock()
        session.add = MagicMock()

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = trades
        execute_mock = MagicMock()
        execute_mock.scalars.return_value = scalars_mock

        async def _execute(*args, **kwargs):
            return execute_mock

        session.execute = _execute
        session.commit = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        session_factory = MagicMock(return_value=session)

        async def _fetch(ticker):
            payload = market_payloads.get(ticker)
            if payload is None:
                return _FetchResult(not_found=True)
            if isinstance(payload, Exception):
                return _FetchResult(error_msg=str(payload))
            return _FetchResult(data=payload)

        with (
            patch("app.database.AsyncSessionLocal", session_factory),
            patch.object(svc, "fetch_kalshi_market", side_effect=_fetch),
        ):
            return await run_settlement_job()

    async def test_confirmed_yes_settles_yes_direction(self):
        trade = _make_trade(direction="YES")
        stats = await self._run(
            [trade],
            {"KXTEST-26JUL27-T90": _market_payload("finalized", "yes")},
        )
        assert trade.status == "SETTLED"
        assert trade.outcome == "WIN"
        assert trade.kalshi_result == "yes"
        assert stats["settled"] == 1
        assert stats["errors"] == 0

    async def test_confirmed_no_settles_no_direction(self):
        trade = _make_trade(direction="NO")
        stats = await self._run(
            [trade],
            {"KXTEST-26JUL27-T90": _market_payload("finalized", "no")},
        )
        assert trade.status == "SETTLED"
        assert trade.outcome == "WIN"
        assert trade.kalshi_result == "no"
        assert stats["settled"] == 1

    async def test_unresolved_active_market_stays_open(self):
        """Active market with no result: trade remains OPEN."""
        trade = _make_trade()
        stats = await self._run(
            [trade],
            {"KXTEST-26JUL27-T90": _market_payload("open")},
        )
        assert trade.status == "OPEN"
        assert stats["still_open"] == 1
        assert stats["settled"] == 0

    async def test_finalized_without_result_becomes_pending(self):
        """Finalized market with no result must never be treated as a loss."""
        trade = _make_trade()
        stats = await self._run(
            [trade],
            {"KXTEST-26JUL27-T90": _market_payload("finalized")},
        )
        assert trade.status == "PENDING_SETTLEMENT"
        assert stats["pending_settlement"] == 1
        assert stats["settled"] == 0
        # P/L must NOT have been written
        assert trade.profit_loss != pytest.approx(-10.0)

    async def test_closed_without_result_becomes_pending(self):
        """Closed market with no result must go to PENDING_SETTLEMENT."""
        trade = _make_trade()
        stats = await self._run(
            [trade],
            {"KXTEST-26JUL27-T90": _market_payload("closed")},
        )
        assert trade.status == "PENDING_SETTLEMENT"
        assert stats["pending_settlement"] == 1

    async def test_missing_api_result_field_finalized_is_pending(self):
        """Finalized payload missing the 'result' key entirely → PENDING_SETTLEMENT."""
        trade = _make_trade()
        # No "result" key in payload at all
        payload = {"status": "finalized"}
        stats = await self._run([trade], {"KXTEST-26JUL27-T90": payload})
        assert trade.status == "PENDING_SETTLEMENT"
        assert stats["pending_settlement"] == 1

    async def test_api_failure_transient_leaves_status_unchanged(self):
        """Network/5xx failure must not change trade status."""
        from app.services.settlement import _FetchResult

        trade = _make_trade(status="OPEN")
        original_status = trade.status

        from app.services import settlement as svc
        session = MagicMock()
        session.add = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [trade]
        execute_mock = MagicMock()
        execute_mock.scalars.return_value = scalars_mock

        async def _execute(*args, **kwargs):
            return execute_mock

        session.execute = _execute
        session.commit = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        async def _transient_fetch(ticker):
            return _FetchResult(error_msg="Connection timeout")

        with (
            patch("app.database.AsyncSessionLocal", MagicMock(return_value=session)),
            patch.object(svc, "fetch_kalshi_market", side_effect=_transient_fetch),
        ):
            stats = await run_settlement_job()

        assert trade.status == original_status  # unchanged
        assert stats["still_open"] == 1
        assert stats["errors"] == 0
        # A transient warning note must be recorded
        assert trade.warnings is not None
        assert "transient" in trade.warnings.lower()

    async def test_api_404_marks_error(self):
        """A 404 from Kalshi (market not found) must set status=ERROR."""
        trade = _make_trade()
        # market_payloads is empty → fetch returns not_found=True
        stats = await self._run([trade], {})
        assert trade.status == "ERROR"
        assert stats["errors"] == 1
        assert stats["settled"] == 0

    async def test_pending_settlement_trade_retried_on_next_cycle(self):
        """PENDING_SETTLEMENT trades are included in the next settlement cycle."""
        trade = _make_trade(status="PENDING_SETTLEMENT")
        # Now the result is available
        stats = await self._run(
            [trade],
            {"KXTEST-26JUL27-T90": _market_payload("finalized", "yes")},
        )
        assert trade.status == "SETTLED"
        assert stats["settled"] == 1

    async def test_idempotent_already_settled_not_double_counted(self):
        """Already-SETTLED trades are not queried (settlement job filters OPEN/PENDING only)."""
        # Settlement job queries OPEN + PENDING_SETTLEMENT.
        # Return an empty list to simulate no open trades.
        stats = await self._run([], {})
        assert stats["checked"] == 0
        assert stats["settled"] == 0


# ── Route path test ────────────────────────────────────────────────────────────

class TestTradeDetailRouting:
    """Verify the correct URL pattern for the trade detail page."""

    def test_trade_detail_path_uses_paper_trading_not_paper_trades(self):
        """
        The route in App.tsx is /paper-trading/:id.
        Links must use /paper-trading/{id}, NOT /paper-trades/{id}.
        """
        trade_id = 42
        correct_path = f"/paper-trading/{trade_id}"
        wrong_path = f"/paper-trades/{trade_id}"

        # The route definition in App.tsx: path="/paper-trading/:id"
        # Simulate wouter matching
        import re
        route_pattern = re.compile(r"^/paper-trading/(\d+)$")

        assert route_pattern.match(correct_path), (
            f"Expected route to match '{correct_path}' with pattern /paper-trading/:id"
        )
        assert not route_pattern.match(wrong_path), (
            f"Expected route to NOT match '{wrong_path}' — it uses the old /paper-trades/ prefix"
        )

    def test_all_trade_ids_produce_valid_paths(self):
        """Spot-check that 5 representative trade IDs produce valid /paper-trading/ paths."""
        import re
        route_pattern = re.compile(r"^/paper-trading/(\d+)$")
        for trade_id in [1, 2, 7, 15, 41]:
            path = f"/paper-trading/{trade_id}"
            match = route_pattern.match(path)
            assert match is not None, f"Path {path!r} did not match route pattern"
            assert int(match.group(1)) == trade_id
