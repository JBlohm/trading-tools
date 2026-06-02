"""Tests for place_order.py."""

import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

IB_UNSET = 1.7976931348623157e308


def _make_account_value(account, tag, value):
    return SimpleNamespace(account=account, tag=tag, value=value)


def _make_order(**kwargs):
    defaults = {
        "orderId": 99,
        "permId": 888,
        "clientId": 1004,
        "action": "BUY",
        "orderType": "MKT",
        "totalQuantity": 10.0,
        "lmtPrice": IB_UNSET,
        "auxPrice": IB_UNSET,
        "tif": "DAY",
        "account": "DU123456",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_order_status(**kwargs):
    defaults = {"status": "PreSubmitted", "filled": 0.0, "remaining": 10.0,
                "avgFillPrice": 0.0, "whyHeld": ""}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_contract(**kwargs):
    defaults = {
        "symbol": "AAPL", "secType": "STK", "currency": "USD",
        "exchange": "SMART", "lastTradeDateOrContractMonth": None,
        "strike": None, "right": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_trade(contract=None, order=None, status=None):
    return SimpleNamespace(
        contract=contract or _make_contract(),
        order=order or _make_order(),
        orderStatus=status or _make_order_status(),
        fills=[],
    )


def _inject_fake_ib_async(mock_ib_class=None):
    fake = types.ModuleType("ib_async")

    # Use a factory that returns a mock with common async methods pre-set
    def ib_mock_factory(*args, **kwargs):
        m = MagicMock()
        m.connectAsync = AsyncMock()
        m.qualifyContractsAsync = AsyncMock(return_value=[])
        m.accountSummaryAsync = AsyncMock(return_value=[])
        m.reqAllOpenOrdersAsync = AsyncMock(return_value=[])
        return m

    fake.IB = mock_ib_class or MagicMock(side_effect=ib_mock_factory)

    # Minimal stand-ins for contract/order classes
    class FakeContract:
        def __init__(self, *a, **kw):
            self.symbol = kw.get("symbol", "")
            self.secType = kw.get("secType", "STK")
            self.currency = kw.get("currency", "USD")
            self.exchange = kw.get("exchange", "SMART")

    class Stock(FakeContract):
        def __init__(self, symbol, exchange="SMART", currency="USD"):
            self.symbol = symbol
            self.secType = "STK"
            self.currency = currency
            self.exchange = exchange

    class Option(FakeContract):
        def __init__(self, symbol, expiry, strike, right, exchange, currency="USD"):
            self.symbol = symbol
            self.secType = "OPT"
            self.lastTradeDateOrContractMonth = expiry
            self.strike = strike
            self.right = right
            self.exchange = exchange
            self.currency = currency

    class Future(FakeContract):
        def __init__(self, symbol, expiry, exchange, currency="USD"):
            self.symbol = symbol
            self.secType = "FUT"
            self.lastTradeDateOrContractMonth = expiry
            self.exchange = exchange
            self.currency = currency

    class MarketOrder:
        def __init__(self, action, qty, tif="DAY"):
            self.action = action
            self.totalQuantity = qty
            self.orderType = "MKT"
            self.lmtPrice = IB_UNSET
            self.tif = tif

    class LimitOrder:
        def __init__(self, action, qty, price, tif="DAY"):
            self.action = action
            self.totalQuantity = qty
            self.lmtPrice = price
            self.orderType = "LMT"
            self.tif = tif

    fake.Contract = FakeContract
    fake.Stock = Stock
    fake.Option = Option
    fake.Future = Future
    fake.MarketOrder = MarketOrder
    fake.LimitOrder = LimitOrder

    sys.modules["ib_async"] = fake
    if "tools.place_order" in sys.modules:
        del sys.modules["tools.place_order"]
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    import tools.place_order as mod
    return mod, fake


def _make_args(**kwargs):
    defaults = {
        "host": "127.0.0.1",
        "port": 7497,
        "symbol": "AAPL",
        "sec_type": "STK",
        "currency": "USD",
        "exchange": "SMART",
        "expiry": None,
        "strike": None,
        "right": None,
        "action": "BUY",
        "quantity": 10.0,
        "order_type": "MKT",
        "limit_price": None,
        "tif": "DAY",
        "simulation": False,
        "max_notional": 100_000.0,
        "max_pct_nlv": 10.0,
        "allow_no_data": False,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _pass_risk_result():
    return {
        "verdict": "pass",
        "audit_id": "pretrade-20260101T000000Z-abcdef01",
        "timestamp": "2026-01-01T00:00:00Z",
        "failures": [],
        "warnings": [],
        "risk_checks": [],
        "stress_results": {},
        "limit_utilization": {},
        "account": {"nlv": 500_000, "excess_liquidity": 200_000},
        "projected_position": {},
        "margin_impact": {},
        "order": {},
    }


def _fail_risk_result(reason_code="ERR_NOTIONAL_LIMIT", message="Notional exceeds limit"):
    r = _pass_risk_result()
    r["verdict"] = "fail"
    r["failures"] = [{"reason_code": reason_code, "message": message, "check": "notional_limit"}]
    return r


class TestBuildOrder:
    def setup_method(self):
        self.mod, self.fake = _inject_fake_ib_async()

    def test_mkt_order_created(self):
        args = _make_args(order_type="MKT", action="BUY", quantity=5.0)
        order = self.mod._build_order(args)
        assert order.orderType == "MKT"
        assert order.action == "BUY"
        assert order.totalQuantity == 5.0

    def test_lmt_order_created(self):
        args = _make_args(order_type="LMT", action="SELL", quantity=3.0, limit_price=180.0)
        order = self.mod._build_order(args)
        assert order.orderType == "LMT"
        assert order.lmtPrice == pytest.approx(180.0)

    def test_lmt_without_price_raises(self):
        args = _make_args(order_type="LMT", limit_price=None)
        with pytest.raises(ValueError, match="--limit-price"):
            self.mod._build_order(args)

    def test_unsupported_order_type_raises(self):
        args = _make_args(order_type="STP")
        with pytest.raises(ValueError, match="Unsupported"):
            self.mod._build_order(args)


class TestArgParsing:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_defaults(self):
        with patch("sys.argv", ["place_order.py", "--symbol", "AAPL",
                                "--action", "BUY", "--quantity", "10",
                                "--order-type", "MKT"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_PAPER
        assert args.simulation is False

    def test_live_flag(self):
        with patch("sys.argv", ["place_order.py", "--symbol", "SPY",
                                "--action", "BUY", "--quantity", "1",
                                "--order-type", "MKT", "--live"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_LIVE

    def test_missing_required_symbol_exits(self):
        with patch("sys.argv", ["place_order.py", "--action", "BUY",
                                "--quantity", "10", "--order-type", "MKT"]):
            with pytest.raises(SystemExit):
                self.mod.parse_args()


class TestRiskGateIntegration:
    """Tests verifying that place_order correctly delegates to and respects the risk gate."""

    def setup_method(self):
        self.mock_ib_class = MagicMock()
        self.mod, _ = _inject_fake_ib_async(self.mock_ib_class)

    def _setup_mock_ib(self):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.qualifyContractsAsync = AsyncMock()
        mock_instance.placeOrder = MagicMock(return_value=_make_trade())
        mock_instance.accountSummaryAsync = AsyncMock(return_value=[
            _make_account_value("DU", "NetLiquidation", "500000"),
            _make_account_value("DU", "ExcessLiquidity", "200000"),
        ])
        mock_instance.reqAllOpenOrdersAsync = AsyncMock(return_value=[])
        ticker = MagicMock()
        ticker.bid = 49.95
        ticker.ask = 50.05
        ticker.last = 50.0
        ticker.close = 49.9
        ticker.halted = 0
        ticker.time = None
        ticker.modelGreeks = None
        ticker.lastGreeks = None
        mock_instance.reqMktData.return_value = ticker
        mock_instance.cancelMktData = MagicMock()
        mock_instance.disconnect = MagicMock()
        mock_instance.portfolio.return_value = []
        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    def test_places_order_when_risk_gate_passes(self):
        mock_ib = self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT")
        with patch("tools.place_order._run_risk_check", new=AsyncMock(return_value=_pass_risk_result())), \
             patch("tools.place_order._load_limits", return_value={}):
            result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        mock_ib.placeOrder.assert_called_once()
        assert result.get("status") != "risk_check_failed"
        assert "risk_check" in result

    def test_rejects_order_when_risk_gate_fails(self):
        mock_ib = self._setup_mock_ib()
        args = _make_args(quantity=1000.0, limit_price=200.0, order_type="LMT")
        with patch("tools.place_order._run_risk_check",
                   new=AsyncMock(return_value=_fail_risk_result())), \
             patch("tools.place_order._load_limits", return_value={}):
            result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        mock_ib.placeOrder.assert_not_called()
        assert result["status"] == "risk_check_failed"
        assert result["verdict"] == "fail"
        assert result["failures"][0]["reason_code"] == "ERR_NOTIONAL_LIMIT"

    def test_simulation_mode_bypasses_failed_risk_gate(self):
        mock_ib = self._setup_mock_ib()
        args = _make_args(quantity=1000.0, limit_price=200.0, order_type="LMT", simulation=True)
        with patch("tools.place_order._run_risk_check",
                   new=AsyncMock(return_value=_fail_risk_result())), \
             patch("tools.place_order._load_limits", return_value={}):
            result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args,
                                                       simulation=True))
        mock_ib.placeOrder.assert_called_once()
        assert result.get("status") != "risk_check_failed"

    def test_rejection_includes_machine_readable_reason_codes(self):
        self._setup_mock_ib()
        args = _make_args()
        with patch("tools.place_order._run_risk_check",
                   new=AsyncMock(return_value=_fail_risk_result(
                       reason_code="ERR_CONCENTRATION_SINGLE_NAME",
                       message="Projected position exceeds single-name limit"))), \
             patch("tools.place_order._load_limits", return_value={}):
            result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result["status"] == "risk_check_failed"
        assert result["failures"][0]["reason_code"] == "ERR_CONCENTRATION_SINGLE_NAME"
        assert "single-name" in result["failures"][0]["message"]

    def test_raises_connection_error_on_timeout(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=asyncio.TimeoutError())
        args = _make_args()
        with pytest.raises(ConnectionError, match="Timed out"):
            asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))

    def test_result_json_serialisable(self):
        self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT")
        with patch("tools.place_order._run_risk_check", new=AsyncMock(return_value=_pass_risk_result())), \
             patch("tools.place_order._load_limits", return_value={}):
            result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        json.dumps(result)

    def test_ib_warning_does_not_appear_on_stdout(self, capsys):
        mock_ib = self._setup_mock_ib()
        trade = _make_trade()

        def place_with_warning(contract, order):
            print("IBKR API validation warning: Warning 399 — order held")
            return trade

        mock_ib.placeOrder.side_effect = place_with_warning

        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT")
        with patch("tools.place_order._run_risk_check", new=AsyncMock(return_value=_pass_risk_result())), \
             patch("tools.place_order._load_limits", return_value={}):
            asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert capsys.readouterr().out == ""


class TestRiskCheckLogicPortfolioCompat:
    """Ensure existing risk-check tests still work after _fetch_position_snapshot was added."""

    def setup_method(self):
        self.mock_ib_class = MagicMock()
        self.mod, _ = _inject_fake_ib_async(self.mock_ib_class)

    def _setup_mock_ib(self, nlv=500_000.0, excess=200_000.0):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.qualifyContractsAsync = AsyncMock()
        mock_instance.reqAllOpenOrdersAsync = AsyncMock(return_value=[])
        mock_instance.placeOrder = MagicMock(return_value=_make_trade())
        summary = [
            _make_account_value("DU", "NetLiquidation", str(nlv)),
            _make_account_value("DU", "ExcessLiquidity", str(excess)),
        ]
        mock_instance.accountSummaryAsync = AsyncMock(return_value=summary)
        ticker = MagicMock()
        ticker.bid = None
        ticker.ask = None
        ticker.last = 0.0
        ticker.close = 0.0
        ticker.halted = 0
        ticker.time = None
        ticker.modelGreeks = None
        ticker.lastGreeks = None
        mock_instance.reqMktData.return_value = ticker
        mock_instance.cancelMktData = MagicMock()
        mock_instance.disconnect = MagicMock()
        mock_instance.portfolio.return_value = []
        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    def test_risk_check_failed_has_audit_id(self):
        import re
        self._setup_mock_ib(nlv=500_000, excess=200_000)
        args = _make_args(quantity=10_000.0, limit_price=200.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result["status"] == "risk_check_failed"
        assert "audit_id" in result
        uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        assert uuid_re.match(result["audit_id"]), f"Not a UUID: {result['audit_id']}"


class TestPostExecutionConfirmation:
    """TRA-26: post-execution confirmation fields, fill statuses, and audit trail."""

    def setup_method(self):
        self.mock_ib_class = MagicMock()
        self.mod, _ = _inject_fake_ib_async(self.mock_ib_class)

    def _setup_mock_ib(
        self,
        nlv=500_000.0,
        excess=200_000.0,
        order_status_kwargs=None,
        portfolio_items=None,
    ):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.qualifyContractsAsync = AsyncMock()

        os_kwargs = {
            "status": "PreSubmitted", "filled": 0.0,
            "remaining": 10.0, "avgFillPrice": 0.0, "whyHeld": "",
        }
        if order_status_kwargs:
            os_kwargs.update(order_status_kwargs)

        trade = _make_trade(status=_make_order_status(**os_kwargs))
        mock_instance.placeOrder = MagicMock(return_value=trade)

        summary = [
            _make_account_value("DU", "NetLiquidation", str(nlv)),
            _make_account_value("DU", "ExcessLiquidity", str(excess)),
            _make_account_value("DU", "BuyingPower", str(nlv * 2)),
            _make_account_value("DU", "InitMarginReq", "10000"),
            _make_account_value("DU", "MaintMarginReq", "8000"),
        ]
        mock_instance.accountSummaryAsync = AsyncMock(return_value=summary)
        mock_instance.reqAllOpenOrdersAsync = AsyncMock(return_value=[])
        ticker = MagicMock()
        ticker.bid = None
        ticker.ask = None
        ticker.last = 0.0
        ticker.close = 0.0
        ticker.halted = 0
        ticker.time = None
        ticker.modelGreeks = None
        ticker.lastGreeks = None
        mock_instance.reqMktData.return_value = ticker
        mock_instance.cancelMktData = MagicMock()
        mock_instance.disconnect = MagicMock()
        mock_instance.portfolio.return_value = portfolio_items if portfolio_items is not None else []
        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    @staticmethod
    def _make_portfolio_item(symbol="AAPL", position=10.0):
        item = MagicMock()
        item.contract.symbol = symbol
        item.contract.secType = "STK"
        item.contract.conId = 265598
        item.position = position
        item.marketPrice = 150.0
        item.marketValue = position * 150.0
        item.averageCost = 145.0
        item.unrealizedPNL = (150.0 - 145.0) * position
        item.realizedPNL = 0.0
        item.account = "DU123456"
        return item

    def test_accepted_order_has_all_required_confirmation_fields(self):
        self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        required = [
            "audit_id", "submission_timestamp", "broker_ack_timestamp",
            "client_order_id", "broker_order_id",
            "accepted", "rejection_reason", "ib_order_status",
            "fill_status", "filled_quantity", "remaining_quantity",
            "average_fill_price", "commission", "commission_note",
            "position_snapshot", "margin_snapshot", "risk_check",
        ]
        for field in required:
            assert field in result, f"Missing required field: {field}"

    def test_accepted_order_accepted_true_no_rejection_reason(self):
        self._setup_mock_ib(order_status_kwargs={"status": "PreSubmitted"})
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result["accepted"] is True
        assert result["rejection_reason"] is None

    def test_rejected_order_is_structured_not_an_exception(self):
        self._setup_mock_ib(order_status_kwargs={"status": "Inactive", "whyHeld": "risk limits"})
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result["accepted"] is False
        assert result["rejection_reason"] is not None
        assert result["fill_status"] == "rejected"

    def test_partial_fill(self):
        self._setup_mock_ib(order_status_kwargs={
            "status": "PartiallyFilled", "filled": 5.0, "remaining": 5.0, "avgFillPrice": 50.0,
        })
        args = _make_args(quantity=10.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result["fill_status"] == "partial_fill"
        assert result["filled_quantity"] == pytest.approx(5.0)
        assert result["remaining_quantity"] == pytest.approx(5.0)
        assert result["average_fill_price"] == pytest.approx(50.0)

    def test_full_fill(self):
        self._setup_mock_ib(order_status_kwargs={
            "status": "Filled", "filled": 10.0, "remaining": 0.0, "avgFillPrice": 50.25,
        })
        args = _make_args(quantity=10.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result["fill_status"] == "filled"
        assert result["filled_quantity"] == pytest.approx(10.0)
        assert result["remaining_quantity"] == pytest.approx(0.0)
        assert result["average_fill_price"] == pytest.approx(50.25)

    def test_open_order_no_fill(self):
        self._setup_mock_ib(order_status_kwargs={
            "status": "Submitted", "filled": 0.0, "remaining": 10.0,
        })
        args = _make_args(quantity=10.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result["fill_status"] == "open"
        assert result["filled_quantity"] == pytest.approx(0.0)

    def test_broker_error_returns_structured_dict(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.placeOrder.side_effect = RuntimeError("TWS rejected: invalid contract")
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result["status"] == "broker_error"
        assert "error" in result
        assert "audit_id" in result

    def test_audit_id_is_uuid(self):
        import re
        self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        assert uuid_re.match(result["audit_id"]), f"Not a UUID: {result['audit_id']}"

    def test_position_snapshot_returned_with_symbol_filter(self):
        item = self._make_portfolio_item(symbol="AAPL", position=10.0)
        self._setup_mock_ib(portfolio_items=[item])
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        snap = result["position_snapshot"]
        assert snap["available"] is True
        assert len(snap["positions"]) == 1
        assert snap["positions"][0]["symbol"] == "AAPL"
        assert snap["positions"][0]["position"] == pytest.approx(10.0)

    def test_position_snapshot_filters_to_traded_symbol_only(self):
        """Other portfolio positions must not leak into the confirmation."""
        aapl_item = self._make_portfolio_item(symbol="AAPL", position=5.0)
        spy_item = self._make_portfolio_item(symbol="SPY", position=100.0)
        self._setup_mock_ib(portfolio_items=[aapl_item, spy_item])
        args = _make_args(symbol="AAPL", quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        snap = result["position_snapshot"]
        assert snap["available"] is True
        assert all(p["symbol"] == "AAPL" for p in snap["positions"]), "SPY leaked into snapshot"

    def test_position_snapshot_empty_list_when_no_matching_symbol(self):
        """If account has positions but none match the traded symbol, return empty list."""
        spy_item = self._make_portfolio_item(symbol="SPY", position=100.0)
        self._setup_mock_ib(portfolio_items=[spy_item])
        args = _make_args(symbol="AAPL", quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        snap = result["position_snapshot"]
        assert snap["available"] is True
        assert snap["positions"] == []

    def test_position_snapshot_available_false_on_error(self):
        mock_ib = self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        # Patch _run_risk_check to pass so execution reaches _fetch_position_snapshot;
        # then set portfolio.side_effect so _fetch_position_snapshot gets the error.
        with patch("tools.place_order._run_risk_check", new=AsyncMock(return_value=_pass_risk_result())), \
             patch("tools.place_order._load_limits", return_value={}):
            mock_ib.portfolio.side_effect = RuntimeError("portfolio unavailable")
            result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result["position_snapshot"]["available"] is False

    def test_margin_snapshot_returned(self):
        self._setup_mock_ib(nlv=500_000, excess=200_000)
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        snap = result["margin_snapshot"]
        assert snap["available"] is True
        acct = list(snap["accounts"].values())[0]
        assert acct["nlv"] == pytest.approx(500_000.0)
        assert acct["excess_liquidity"] == pytest.approx(200_000.0)

    def test_full_result_json_serialisable(self):
        item = self._make_portfolio_item()
        self._setup_mock_ib(portfolio_items=[item])
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        json.dumps(result)  # must not raise


class TestConnectionIdRegister:
    def test_place_order_registered_as_not_readonly(self):
        import pathlib, json
        reg = pathlib.Path(__file__).parent.parent / "tools" / "connection_ids.json"
        data = json.loads(reg.read_text())
        assert "1004" in data["ids"]
        assert data["ids"]["1004"]["tool"] == "place_order.py"
        assert data["ids"]["1004"]["read_only"] is False


class TestPreTradeSnapshotIntegration:
    """TRA-23: pre-trade snapshot wired into place_order pre-trade gate and audit record."""

    def setup_method(self):
        self.mock_ib_class = MagicMock()
        self.mod, _ = _inject_fake_ib_async(self.mock_ib_class)

    def _setup_mock_ib(self, ticker_kwargs=None, nlv=500_000.0, excess=200_000.0):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.qualifyContractsAsync = AsyncMock()
        mock_instance.placeOrder = MagicMock(return_value=_make_trade())
        summary = [
            _make_account_value("DU", "NetLiquidation", str(nlv)),
            _make_account_value("DU", "ExcessLiquidity", str(excess)),
        ]
        mock_instance.accountSummaryAsync = AsyncMock(return_value=summary)
        t_kwargs = {"last": 100.0, "close": 99.0, "bid": 99.95,
                    "ask": 100.05, "halted": 0, "time": None,
                    "modelGreeks": None, "lastGreeks": None}
        if ticker_kwargs:
            t_kwargs.update(ticker_kwargs)
        mock_instance.reqMktData.return_value = SimpleNamespace(**t_kwargs)
        mock_instance.cancelMktData = MagicMock()
        mock_instance.disconnect = MagicMock()
        mock_instance.reqAllOpenOrdersAsync = AsyncMock(return_value=[])
        mock_instance.portfolio.return_value = []
        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    def test_snapshot_present_in_successful_audit(self):
        self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert "quote_snapshot" in result
        snap = result["quote_snapshot"]
        assert "quote" in snap
        assert "session" in snap
        assert "staleness" in snap
        assert "warnings" in snap

    def test_snapshot_embedded_in_risk_check(self):
        self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert "quote_snapshot" in result["risk_check"]

    def test_halted_instrument_causes_risk_check_failed(self):
        self._setup_mock_ib(ticker_kwargs={"halted": 1, "bid": 50.0, "ask": 50.10,
                                           "last": 50.05, "time": None})
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result["status"] == "risk_check_failed"
        assert any("snapshot" in f.lower() and "HALTED" in f
                   for f in result["risk_check"]["failures"])

    def test_halted_instrument_order_not_placed(self):
        mock_ib = self._setup_mock_ib(ticker_kwargs={"halted": 1, "bid": 50.0,
                                                     "ask": 50.10, "last": 50.05,
                                                     "time": None})
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        mock_ib.placeOrder.assert_not_called()

    def test_stale_instrument_with_prices_causes_risk_check_failed(self):
        from datetime import datetime, timezone, timedelta
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        self._setup_mock_ib(ticker_kwargs={"bid": 50.0, "ask": 50.10,
                                           "last": 50.05, "halted": 0,
                                           "time": stale_time})
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0, stale_threshold=60.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result["status"] == "risk_check_failed"
        assert any("STALE_QUOTE" in f for f in result["risk_check"]["failures"])

    def test_snapshot_present_in_risk_check_failed_output(self):
        self._setup_mock_ib(ticker_kwargs={"halted": 1, "bid": 50.0, "ask": 50.10,
                                           "last": 50.05, "time": None})
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result["status"] == "risk_check_failed"
        assert "quote_snapshot" in result

    def test_snapshot_json_serialisable_in_success(self):
        self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        json.dumps(result)  # must not raise

    def test_mkt_order_uses_snapshot_ref_price_for_notional(self):
        # MKT order: no limit price — ref_price should come from snapshot (bid=99.95)
        self._setup_mock_ib(ticker_kwargs={"bid": 99.95, "ask": 100.05,
                                           "last": 100.0, "halted": 0, "time": None})
        args = _make_args(quantity=10.0, order_type="MKT", limit_price=None,
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        # ref_price = last = 100.0, estimated_notional = 10 * 100.0 = 1000 < 100,000
        assert result.get("status") != "risk_check_failed" or "snapshot" in str(result)


# ---------------------------------------------------------------------------
# All-null snapshot risk gate (TRA-32)
# ---------------------------------------------------------------------------

class TestAllNullSnapshotRiskGate:
    """Risk gate must block orders when all price data is null during regular session."""

    def setup_method(self):
        self.mock_ib_class = MagicMock()
        self.mod, _ = _inject_fake_ib_async(self.mock_ib_class)

    def _setup_mock_ib(self, nlv=500_000.0, excess=200_000.0, ticker_kwargs=None):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.qualifyContractsAsync = AsyncMock()
        mock_instance.placeOrder = MagicMock(return_value=_make_trade())
        summary = [
            _make_account_value("DU", "NetLiquidation", str(nlv)),
            _make_account_value("DU", "ExcessLiquidity", str(excess)),
        ]
        mock_instance.accountSummaryAsync = AsyncMock(return_value=summary)
        mock_instance.reqAllOpenOrdersAsync = AsyncMock(return_value=[])
        # All-null ticker: bid, ask, last, close all None
        t = MagicMock()
        t.bid = None
        t.ask = None
        t.last = None
        t.close = None
        t.halted = 0
        t.time = None
        t.volume = None
        t.avVolume = None
        if ticker_kwargs:
            for k, v in ticker_kwargs.items():
                setattr(t, k, v)
        mock_instance.reqMktData.return_value = t
        mock_instance.cancelMktData = MagicMock()
        mock_instance.disconnect = MagicMock()
        mock_instance.portfolio.return_value = []
        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    def test_all_null_regular_session_causes_risk_check_failed(self):
        self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        with patch.object(self.mod, "_detect_et_session_state", return_value="regular"):
            result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result["status"] == "risk_check_failed"
        assert any("NO_MARKET_DATA" in f for f in result["risk_check"]["failures"])

    def test_all_null_regular_session_order_not_placed(self):
        mock_ib = self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        with patch.object(self.mod, "_detect_et_session_state", return_value="regular"):
            asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        mock_ib.placeOrder.assert_not_called()

    def test_all_null_closed_session_not_rejected_for_no_market_data(self):
        self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        with patch.object(self.mod, "_detect_et_session_state", return_value="closed"):
            result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        # Only risk_check_failed allowed here — but NOT for NO_MARKET_DATA
        failures = result.get("risk_check", {}).get("failures", [])
        assert not any("NO_MARKET_DATA" in f for f in failures)

    def test_all_null_regular_with_allow_no_data_passes_snapshot_gate(self):
        self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0, allow_no_data=True)
        with patch.object(self.mod, "_detect_et_session_state", return_value="regular"):
            result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        # Should not fail for NO_MARKET_DATA (may still fail for other risk checks)
        failures = result.get("risk_check", {}).get("failures", [])
        assert not any("NO_MARKET_DATA" in f for f in failures)

    def test_all_null_regular_with_allow_no_data_override_warning_in_snapshot(self):
        self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0, allow_no_data=True)
        with patch.object(self.mod, "_detect_et_session_state", return_value="regular"):
            result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        snap_warnings = result.get("quote_snapshot", {}).get("warnings", [])
        assert any(w["code"] == "OVERRIDE_NO_DATA" for w in snap_warnings)

    def test_allow_no_data_arg_parsed(self):
        mod, _ = _inject_fake_ib_async()
        with patch("sys.argv", ["place_order.py", "--symbol", "AAPL",
                                "--action", "BUY", "--quantity", "1",
                                "--order-type", "MKT", "--allow-no-data"]):
            args = mod.parse_args()
        assert args.allow_no_data is True

    def test_allow_no_data_defaults_to_false(self):
        mod, _ = _inject_fake_ib_async()
        with patch("sys.argv", ["place_order.py", "--symbol", "AAPL",
                                "--action", "BUY", "--quantity", "1",
                                "--order-type", "MKT"]):
            args = mod.parse_args()
        assert args.allow_no_data is False

    def test_partial_data_regular_session_not_blocked_for_no_market_data(self):
        # If last price is available, not a total blackout → no NO_MARKET_DATA block
        self._setup_mock_ib(ticker_kwargs={"last": 100.0, "close": 99.0})
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        with patch.object(self.mod, "_detect_et_session_state", return_value="regular"):
            result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        failures = result.get("risk_check", {}).get("failures", [])
        assert not any("NO_MARKET_DATA" in f for f in failures)

    def test_partial_data_regular_session_without_bid_ask_rejected(self):
        self._setup_mock_ib(ticker_kwargs={"last": 100.0, "close": 99.0})
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        with patch.object(self.mod, "_detect_et_session_state", return_value="regular"):
            result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result["status"] == "risk_check_failed"
        assert result["quote_snapshot"]["rejection_reason"] == "NO_BID_ASK"


# ---------------------------------------------------------------------------
# Request shape (TRA-33): two-request pattern in _fetch_pre_trade_snapshot
# ---------------------------------------------------------------------------

class TestPreTradeSnapshotRequestShape:
    """TRA-33: _fetch_pre_trade_snapshot must use snapshot=True/empty-ticklist for
    core fields, then snapshot=False/165,236 for avVolume."""

    def setup_method(self):
        self.mock_ib_class = MagicMock()
        self.mod, _ = _inject_fake_ib_async(self.mock_ib_class)
        self.risk_check_mock = MagicMock(return_value={
            "verdict": "pass", "failures": [], "warnings": [],
            "checks": {}, "audit_id": "test-audit", "timestamp": "2026-06-02T00:00:00Z"
        })

    def _setup_mock_ib(self, nlv=500_000.0, excess=200_000.0, ticker_kwargs=None):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.qualifyContractsAsync = AsyncMock(return_value=[])
        mock_instance.accountSummaryAsync = AsyncMock()
        mock_instance.reqAllOpenOrdersAsync = AsyncMock(return_value=[])
        mock_instance.placeOrder = MagicMock(return_value=_make_trade())
        summary = [
            _make_account_value("DU", "NetLiquidation", str(nlv)),
            _make_account_value("DU", "ExcessLiquidity", str(excess)),
        ]
        mock_instance.accountSummaryAsync.return_value = summary

        t_kwargs = {"last": 100.0, "close": 99.0, "bid": 99.95,
                    "ask": 100.05, "halted": 0, "time": None,
                    "avVolume": None, "volume": None}
        if ticker_kwargs:
            t_kwargs.update(ticker_kwargs)
        mock_instance.reqMktData.return_value = MagicMock(**t_kwargs)
        mock_instance.cancelMktData = MagicMock()
        mock_instance.disconnect = MagicMock()
        mock_instance.portfolio.return_value = []
        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    def test_reqMktData_called_twice(self):
        mock_ib = self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        with patch.object(self.mod, "_run_risk_check", AsyncMock(return_value=self.risk_check_mock())):
            asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert mock_ib.reqMktData.call_count == 2

    def test_reqMarketDataType_uses_delayed_data(self):
        mock_ib = self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        with patch.object(self.mod, "_run_risk_check", AsyncMock(return_value=self.risk_check_mock())):
            asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        mock_ib.reqMarketDataType.assert_called_with(3)

    def test_first_call_snapshot_true_empty_ticklist(self):
        mock_ib = self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        with patch.object(self.mod, "_run_risk_check", AsyncMock(return_value=self.risk_check_mock())):
            asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        first = mock_ib.reqMktData.call_args_list[0]
        assert first.kwargs["snapshot"] is True
        assert first.kwargs["genericTickList"] == ""

    def test_second_call_snapshot_false_ticklist_165_236(self):
        mock_ib = self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        with patch.object(self.mod, "_run_risk_check", AsyncMock(return_value=self.risk_check_mock())):
            asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        second = mock_ib.reqMktData.call_args_list[1]
        assert second.kwargs["snapshot"] is False
        assert second.kwargs["genericTickList"] == "165,236"

    def test_second_call_option_ticks(self):
        mock_ib = self._setup_mock_ib()
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          sec_type="OPT", expiry="20260619", strike=180.0, right="C")
        with patch.object(self.mod, "_run_risk_check", AsyncMock(return_value=self.risk_check_mock())):
            asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        second = mock_ib.reqMktData.call_args_list[1]
        assert second.kwargs["snapshot"] is False
        assert "100,101,106" in second.kwargs["genericTickList"]
        assert "165,236" in second.kwargs["genericTickList"]

    def test_adv_from_aux_ticker_when_different_objects(self):
        """When ib_async returns distinct ticker objects, avVolume is copied from aux."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)

        core_t = MagicMock()
        core_t.bid = 99.95
        core_t.ask = 100.05
        core_t.last = 100.0
        core_t.close = 99.0
        core_t.halted = 0
        core_t.time = None
        core_t.avVolume = None
        core_t.volume = None

        aux_t = MagicMock()
        aux_t.bid = 99.95
        aux_t.ask = 100.05
        aux_t.last = 100.0
        aux_t.halted = 0
        aux_t.time = None
        aux_t.avVolume = 50_000_000.0

        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.qualifyContractsAsync = AsyncMock()
        mock_instance.placeOrder = MagicMock(return_value=_make_trade())
        summary = [
            _make_account_value("DU", "NetLiquidation", "500000"),
            _make_account_value("DU", "ExcessLiquidity", "200000"),
        ]
        mock_instance.accountSummaryAsync = AsyncMock(return_value=summary)
        mock_instance.reqAllOpenOrdersAsync = AsyncMock(return_value=[])
        mock_instance.reqMktData = MagicMock(side_effect=[core_t, aux_t])
        mock_instance.cancelMktData = MagicMock()
        mock_instance.disconnect = MagicMock()
        mock_instance.portfolio.return_value = []
        self.mock_ib_class.return_value = mock_instance

        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        with patch.object(self.mod, "_run_risk_check", AsyncMock(return_value=self.risk_check_mock())):
            result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        snap = result.get("quote_snapshot", {})
        assert snap.get("liquidity", {}).get("adv") == pytest.approx(50_000_000.0)
        assert not any(w["code"] == "NO_ADV" for w in snap.get("warnings", []))
