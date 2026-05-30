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
    fake.IB = mock_ib_class or MagicMock

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
        "max_notional": 100_000.0,
        "max_pct_nlv": 10.0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


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
        assert args.max_notional == self.mod.DEFAULT_MAX_NOTIONAL
        assert args.max_pct_nlv == self.mod.DEFAULT_MAX_PCT_NLV

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


class TestRiskCheckLogic:
    def setup_method(self):
        self.mock_ib_class = MagicMock()
        self.mod, _ = _inject_fake_ib_async(self.mock_ib_class)

    def _setup_mock_ib(self, nlv=500_000.0, excess=200_000.0, last_price=0.0):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.qualifyContractsAsync = AsyncMock()
        mock_instance.placeOrder = MagicMock(return_value=_make_trade())

        summary = [
            _make_account_value("DU", "NetLiquidation", str(nlv)),
            _make_account_value("DU", "ExcessLiquidity", str(excess)),
        ]
        mock_instance.accountSummaryAsync = AsyncMock(return_value=summary)

        mock_ticker = MagicMock()
        mock_ticker.last = last_price
        mock_ticker.close = last_price
        mock_instance.reqMktData.return_value = mock_ticker
        mock_instance.cancelMktData = MagicMock()
        mock_instance.disconnect = MagicMock()

        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    def test_passes_when_within_limits(self):
        self._setup_mock_ib(nlv=500_000, excess=200_000)
        args = _make_args(quantity=10.0, limit_price=100.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result.get("status") != "risk_check_failed"
        assert result["risk_check"]["passed"] is True

    def test_fails_when_notional_exceeds_max(self):
        self._setup_mock_ib(nlv=500_000, excess=200_000)
        args = _make_args(quantity=1000.0, limit_price=200.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result["status"] == "risk_check_failed"
        assert result["risk_check"]["passed"] is False
        assert any("notional" in f.lower() for f in result["risk_check"]["failures"])

    def test_fails_when_exceeds_pct_nlv(self):
        self._setup_mock_ib(nlv=50_000, excess=20_000)
        # notional = 10 * 600 = 6000 which is > 10% of 50000=5000
        args = _make_args(quantity=10.0, limit_price=600.0, order_type="LMT",
                          max_notional=1_000_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result["status"] == "risk_check_failed"
        assert any("NLV" in f or "nlv" in f.lower() for f in result["risk_check"]["failures"])

    def test_fails_when_no_excess_liquidity(self):
        self._setup_mock_ib(nlv=500_000, excess=-1_000)
        args = _make_args(quantity=1.0, limit_price=10.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert result["status"] == "risk_check_failed"
        assert any("margin" in f.lower() or "excess" in f.lower()
                   for f in result["risk_check"]["failures"])

    def test_places_order_when_check_passes(self):
        mock_ib = self._setup_mock_ib(nlv=500_000, excess=200_000)
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        mock_ib.placeOrder.assert_called_once()

    def test_does_not_place_order_when_check_fails(self):
        mock_ib = self._setup_mock_ib(nlv=500_000, excess=200_000)
        args = _make_args(quantity=10_000.0, limit_price=200.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        mock_ib.placeOrder.assert_not_called()

    def test_raises_connection_error_on_timeout(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=asyncio.TimeoutError())
        args = _make_args()
        with pytest.raises(ConnectionError, match="Timed out"):
            asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))

    def test_result_json_serialisable(self):
        self._setup_mock_ib(nlv=500_000, excess=200_000)
        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        result = asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        json.dumps(result)

    def test_ib_warning_does_not_appear_on_stdout(self, capsys):
        # Simulate ib_async printing a validation warning (e.g. code 399) to stdout
        mock_ib = self._setup_mock_ib(nlv=500_000, excess=200_000)
        trade = _make_trade()

        def place_with_warning(contract, order):
            print("IBKR API validation warning: Warning 399 — order held")
            return trade

        mock_ib.placeOrder.side_effect = place_with_warning

        args = _make_args(quantity=1.0, limit_price=50.0, order_type="LMT",
                          max_notional=100_000, max_pct_nlv=10.0)
        asyncio.run(self.mod.place_order("127.0.0.1", 7497, 1004, args))
        assert capsys.readouterr().out == ""


class TestConnectionIdRegister:
    def test_place_order_registered_as_not_readonly(self):
        import pathlib, json
        reg = pathlib.Path(__file__).parent.parent / "tools" / "connection_ids.json"
        data = json.loads(reg.read_text())
        assert "1004" in data["ids"]
        assert data["ids"]["1004"]["tool"] == "place_order.py"
        assert data["ids"]["1004"]["read_only"] is False
