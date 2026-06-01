"""Tests for get_risk_breaches.py."""

import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


IB_UNSET = 1.7976931348623157e308


def _make_contract(**kwargs):
    defaults = {
        "symbol": "SPY",
        "secType": "OPT",
        "currency": "USD",
        "primaryExch": "CBOE",
        "exchange": "SMART",
        "conId": 99001,
        "lastTradeDateOrContractMonth": "20251220",
        "strike": 500.0,
        "right": "P",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_stk_contract(**kwargs):
    c = SimpleNamespace(
        symbol="AAPL",
        secType="STK",
        currency="USD",
        primaryExch="NASDAQ",
        exchange="SMART",
        conId=12345,
        lastTradeDateOrContractMonth=None,
        strike=None,
        right=None,
        **kwargs,
    )
    return c


def _make_portfolio_item(contract=None, position=-5.0, market_price=8.0, market_value=-40.0, account="DU123456"):
    if contract is None:
        contract = _make_contract()
    return SimpleNamespace(
        contract=contract,
        position=position,
        marketPrice=market_price,
        marketValue=market_value,
        unrealizedPNL=-200.0,
        account=account,
    )


def _make_account_value(tag: str, value: str, account="ALL", currency="USD"):
    return SimpleNamespace(account=account, tag=tag, value=value, currency=currency)


def _make_greeks(**kwargs):
    defaults = {"delta": 0.55, "gamma": 0.02, "theta": -0.10, "vega": 0.30}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_open_order(symbol="SPY", sec_type="OPT", action="BUY", qty=1, order_type="LMT", order_id=1):
    c = _make_contract(symbol=symbol, secType=sec_type)
    o = SimpleNamespace(
        orderId=order_id,
        action=action,
        totalQuantity=qty,
        orderType=order_type,
    )
    s = SimpleNamespace(status="Submitted")
    return SimpleNamespace(contract=c, order=o, orderStatus=s)


def _inject_fake_ib_async(mock_ib_class=None):
    fake = types.ModuleType("ib_async")
    fake.IB = mock_ib_class or MagicMock
    sys.modules["ib_async"] = fake
    for key in list(sys.modules):
        if "tools.get_risk_breaches" in key:
            del sys.modules[key]
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    import tools.get_risk_breaches as mod
    return mod, fake


class TestHelpers:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_greeks_to_dict_none_returns_nones(self):
        result = self.mod._greeks_to_dict(None)
        assert result == {"delta": None, "gamma": None, "theta": None, "vega": None}

    def test_greeks_to_dict_extracts_fields(self):
        g = _make_greeks()
        result = self.mod._greeks_to_dict(g)
        assert result["delta"] == pytest.approx(0.55)
        assert result["vega"] == pytest.approx(0.30)

    def test_add_breach_appends_correctly(self):
        breaches = []
        self.mod._add_breach(breaches, "TEST_CODE", "metric_x", 999.0, 100.0, "warning", "detail")
        assert len(breaches) == 1
        assert breaches[0]["reason_code"] == "TEST_CODE"
        assert breaches[0]["severity"] == "warning"
        assert breaches[0]["current_value"] == 999.0


class TestArgParsing:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_defaults(self):
        with patch("sys.argv", ["get_risk_breaches.py"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_PAPER
        assert args.max_delta == self.mod.DEFAULT_MAX_DELTA
        assert args.max_vega == self.mod.DEFAULT_MAX_VEGA
        assert args.min_cushion_pct == self.mod.DEFAULT_MIN_CUSHION_PCT

    def test_live_flag(self):
        with patch("sys.argv", ["get_risk_breaches.py", "--live"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_LIVE

    def test_custom_max_delta(self):
        with patch("sys.argv", ["get_risk_breaches.py", "--max-delta", "300"]):
            args = self.mod.parse_args()
        assert args.max_delta == 300.0

    def test_custom_min_cushion_pct(self):
        with patch("sys.argv", ["get_risk_breaches.py", "--min-cushion-pct", "20"]):
            args = self.mod.parse_args()
        assert args.min_cushion_pct == 20.0


class TestFetchRiskBreaches:
    def setup_method(self):
        self.mock_ib_class = MagicMock()
        self.mod, _ = _inject_fake_ib_async(self.mock_ib_class)

    def _setup_mock_ib(self, portfolio_items=None, summary_items=None, open_trades=None, ticker_greeks=None):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.portfolio.return_value = portfolio_items or []
        mock_instance.accountSummaryAsync = AsyncMock(return_value=summary_items or [])
        mock_instance.reqAllOpenOrdersAsync = AsyncMock(return_value=open_trades or [])

        mock_ticker = MagicMock()
        mock_ticker.modelGreeks = ticker_greeks
        mock_ticker.lastGreeks = None
        mock_instance.reqMktData.return_value = mock_ticker
        mock_instance.cancelMktData = MagicMock()
        mock_instance.disconnect = MagicMock()

        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    def _make_summary(self, nlv=500_000.0, excess=200_000.0, account="ALL"):
        return [
            _make_account_value("NetLiquidation", str(nlv), account=account),
            _make_account_value("ExcessLiquidity", str(excess), account=account),
        ]

    def test_no_positions_no_breaches(self):
        self._setup_mock_ib(summary_items=self._make_summary())
        result = asyncio.run(
            self.mod.fetch_risk_breaches("127.0.0.1", 7497, 1008, 500.0, 10000.0, 20.0, 10.0)
        )
        assert result["current_breaches"] == []
        assert result["projected_breaches"] == []

    def test_delta_breach_triggers_warning(self):
        nlv = 500_000.0
        excess = 200_000.0
        opt = _make_portfolio_item(position=1000.0, market_value=8_000.0)
        greeks = _make_greeks(delta=0.6, vega=0.2)
        self._setup_mock_ib(
            portfolio_items=[opt],
            summary_items=self._make_summary(nlv=nlv, excess=excess),
            ticker_greeks=greeks,
        )
        result = asyncio.run(
            self.mod.fetch_risk_breaches("127.0.0.1", 7497, 1008, 100.0, 10000.0, 20.0, 10.0)
        )
        codes = [b["reason_code"] for b in result["current_breaches"]]
        assert "MAX_DELTA_EXCEEDED" in codes

    def test_vega_breach_triggers_warning(self):
        nlv = 500_000.0
        opt = _make_portfolio_item(position=1000.0, market_value=8_000.0)
        greeks = _make_greeks(delta=0.1, vega=20.0)
        self._setup_mock_ib(
            portfolio_items=[opt],
            summary_items=self._make_summary(nlv=nlv, excess=200_000.0),
            ticker_greeks=greeks,
        )
        result = asyncio.run(
            self.mod.fetch_risk_breaches("127.0.0.1", 7497, 1008, 500.0, 1000.0, 20.0, 10.0)
        )
        codes = [b["reason_code"] for b in result["current_breaches"]]
        assert "MAX_VEGA_EXCEEDED" in codes

    def test_margin_cushion_breach(self):
        self._setup_mock_ib(
            summary_items=self._make_summary(nlv=100_000.0, excess=5_000.0)
        )
        result = asyncio.run(
            self.mod.fetch_risk_breaches("127.0.0.1", 7497, 1008, 500.0, 10000.0, 20.0, 10.0)
        )
        codes = [b["reason_code"] for b in result["current_breaches"]]
        assert "MARGIN_CUSHION_LOW" in codes

    def test_margin_cushion_ok_when_above_threshold(self):
        self._setup_mock_ib(
            summary_items=self._make_summary(nlv=100_000.0, excess=50_000.0)
        )
        result = asyncio.run(
            self.mod.fetch_risk_breaches("127.0.0.1", 7497, 1008, 500.0, 10000.0, 20.0, 10.0)
        )
        codes = [b["reason_code"] for b in result["current_breaches"]]
        assert "MARGIN_CUSHION_LOW" not in codes

    def test_concentration_breach(self):
        large_pos = _make_portfolio_item(position=1.0, market_value=300_000.0)
        self._setup_mock_ib(
            portfolio_items=[large_pos],
            summary_items=self._make_summary(nlv=500_000.0, excess=200_000.0),
            ticker_greeks=_make_greeks(delta=0.5),
        )
        result = asyncio.run(
            self.mod.fetch_risk_breaches("127.0.0.1", 7497, 1008, 500.0, 10000.0, 20.0, 10.0)
        )
        codes = [b["reason_code"] for b in result["current_breaches"]]
        assert "CONCENTRATION_LIMIT" in codes

    def test_open_order_delta_impact_tracked_for_stocks(self):
        stk_order = _make_open_order(symbol="AAPL", sec_type="STK", action="BUY", qty=100)
        self._setup_mock_ib(
            summary_items=self._make_summary(),
            open_trades=[stk_order],
        )
        result = asyncio.run(
            self.mod.fetch_risk_breaches("127.0.0.1", 7497, 1008, 500.0, 10000.0, 20.0, 10.0)
        )
        assert len(result["open_order_impacts"]) == 1
        impact = result["open_order_impacts"][0]
        assert impact["estimated_delta_impact"] == pytest.approx(100.0)

    def test_result_has_thresholds(self):
        self._setup_mock_ib(summary_items=self._make_summary())
        result = asyncio.run(
            self.mod.fetch_risk_breaches("127.0.0.1", 7497, 1008, 300.0, 8000.0, 15.0, 12.0)
        )
        assert result["thresholds"]["max_delta"] == 300.0
        assert result["thresholds"]["max_vega"] == 8000.0
        assert result["thresholds"]["min_cushion_pct_nlv"] == 12.0

    def test_result_has_timestamp(self):
        self._setup_mock_ib(summary_items=self._make_summary())
        result = asyncio.run(
            self.mod.fetch_risk_breaches("127.0.0.1", 7497, 1008, 500.0, 10000.0, 20.0, 10.0)
        )
        assert result["timestamp"].endswith("Z")

    def test_result_is_json_serialisable(self):
        self._setup_mock_ib(summary_items=self._make_summary())
        result = asyncio.run(
            self.mod.fetch_risk_breaches("127.0.0.1", 7497, 1008, 500.0, 10000.0, 20.0, 10.0)
        )
        json.dumps(result)

    def test_readonly_connection(self):
        mock_ib = self._setup_mock_ib(summary_items=self._make_summary())
        asyncio.run(
            self.mod.fetch_risk_breaches("127.0.0.1", 7497, 1008, 500.0, 10000.0, 20.0, 10.0)
        )
        mock_ib.connectAsync.assert_called_once_with(
            "127.0.0.1", 7497, clientId=1008, readonly=True
        )

    def test_disconnects_on_success(self):
        mock_ib = self._setup_mock_ib(summary_items=self._make_summary())
        asyncio.run(
            self.mod.fetch_risk_breaches("127.0.0.1", 7497, 1008, 500.0, 10000.0, 20.0, 10.0)
        )
        mock_ib.disconnect.assert_called_once()

    def test_raises_connection_error_on_timeout(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=asyncio.TimeoutError())
        with pytest.raises(ConnectionError, match="Timed out"):
            asyncio.run(
                self.mod.fetch_risk_breaches("127.0.0.1", 7497, 1008, 500.0, 10000.0, 20.0, 10.0)
            )

    def test_raises_connection_error_on_refused(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        with pytest.raises(ConnectionError, match="Cannot reach TWS"):
            asyncio.run(
                self.mod.fetch_risk_breaches("127.0.0.1", 7497, 1008, 500.0, 10000.0, 20.0, 10.0)
            )

    def test_critical_severity_for_large_delta_breach(self):
        opt = _make_portfolio_item(position=3000.0, market_value=8_000.0)
        greeks = _make_greeks(delta=0.6)
        self._setup_mock_ib(
            portfolio_items=[opt],
            summary_items=self._make_summary(),
            ticker_greeks=greeks,
        )
        result = asyncio.run(
            self.mod.fetch_risk_breaches("127.0.0.1", 7497, 1008, 100.0, 10000.0, 20.0, 10.0)
        )
        delta_breach = next(b for b in result["current_breaches"] if b["reason_code"] == "MAX_DELTA_EXCEEDED")
        assert delta_breach["severity"] == "critical"


class TestMain:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_connection_error_exits_1(self, capsys):
        async def raise_err(host, port, client_id, max_delta, max_vega, max_position_pct, min_cushion_pct):
            raise ConnectionError("Cannot reach TWS")

        with patch("sys.argv", ["get_risk_breaches.py"]):
            with patch.object(self.mod, "fetch_risk_breaches", raise_err):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()

        assert exc.value.code == 1
        err = json.loads(capsys.readouterr().err)
        assert err["status"] == "tws_unavailable"


class TestConnectionIdRegister:
    def test_get_risk_breaches_registered(self):
        import pathlib, json
        reg = pathlib.Path(__file__).parent.parent / "tools" / "connection_ids.json"
        data = json.loads(reg.read_text())
        assert "1008" in data["ids"]
        assert data["ids"]["1008"]["tool"] == "get_risk_breaches.py"
        assert data["ids"]["1008"]["read_only"] is True
