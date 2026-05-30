"""Tests for get_greeks.py."""

import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_contract(**kwargs):
    defaults = {
        "symbol": "SPY",
        "secType": "OPT",
        "currency": "USD",
        "primaryExch": "CBOE",
        "exchange": "SMART",
        "conId": 123456,
        "lastTradeDateOrContractMonth": "20251220",
        "strike": 500.0,
        "right": "C",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_portfolio_item(contract=None, position=1.0, **kwargs):
    if contract is None:
        contract = _make_contract()
    defaults = {
        "contract": contract,
        "position": position,
        "marketPrice": 10.0,
        "marketValue": 10.0 * position,
        "averageCost": 8.0,
        "unrealizedPNL": 2.0,
        "realizedPNL": 0.0,
        "account": "DU123456",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_greeks(**kwargs):
    defaults = {"delta": 0.55, "gamma": 0.02, "theta": -0.10, "vega": 0.30, "impliedVol": 0.25}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _inject_fake_ib_async(mock_ib_class=None):
    fake = types.ModuleType("ib_async")
    fake.IB = mock_ib_class or MagicMock
    fake.PortfolioItem = object
    sys.modules["ib_async"] = fake
    if "tools.get_greeks" in sys.modules:
        del sys.modules["tools.get_greeks"]
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    import tools.get_greeks as mod
    return mod, fake


class TestGreekHelpers:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_greeks_to_dict_none_returns_nones(self):
        result = self.mod._greeks_to_dict(None)
        assert result == {"delta": None, "gamma": None, "theta": None, "vega": None, "iv": None}

    def test_greeks_to_dict_extracts_fields(self):
        g = _make_greeks()
        result = self.mod._greeks_to_dict(g)
        assert result["delta"] == pytest.approx(0.55)
        assert result["gamma"] == pytest.approx(0.02)
        assert result["theta"] == pytest.approx(-0.10)
        assert result["vega"] == pytest.approx(0.30)
        assert result["iv"] == pytest.approx(0.25)

    def test_scale_greeks_multiplies_by_position(self):
        g = {"delta": 0.5, "gamma": 0.01, "theta": -0.1, "vega": 0.2, "iv": 0.25}
        result = self.mod._scale_greeks(g, 10.0)
        assert result["delta"] == pytest.approx(5.0)
        assert result["gamma"] == pytest.approx(0.1)
        assert result["theta"] == pytest.approx(-1.0)
        assert result["vega"] == pytest.approx(2.0)
        assert result["iv"] == pytest.approx(2.5)

    def test_scale_greeks_propagates_none(self):
        g = {"delta": None, "gamma": 0.01, "theta": None, "vega": 0.2, "iv": None}
        result = self.mod._scale_greeks(g, 5.0)
        assert result["delta"] is None
        assert result["theta"] is None
        assert result["gamma"] == pytest.approx(0.05)


class TestArgParsing:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_defaults_to_paper_port(self):
        with patch("sys.argv", ["get_greeks.py"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_PAPER
        assert args.host == self.mod.DEFAULT_HOST

    def test_live_flag_sets_live_port(self):
        with patch("sys.argv", ["get_greeks.py", "--live"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_LIVE

    def test_custom_host(self):
        with patch("sys.argv", ["get_greeks.py", "--host", "10.0.0.5"]):
            args = self.mod.parse_args()
        assert args.host == "10.0.0.5"

    def test_live_and_paper_mutually_exclusive(self):
        with patch("sys.argv", ["get_greeks.py", "--live", "--paper"]):
            with pytest.raises(SystemExit):
                self.mod.parse_args()


class TestFetchGreeks:
    def setup_method(self):
        self.mock_ib_class = MagicMock()
        self.mod, _ = _inject_fake_ib_async(self.mock_ib_class)

    def _setup_mock_ib(self, portfolio_items=None, ticker_greeks=None):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.portfolio.return_value = portfolio_items or []

        mock_ticker = MagicMock()
        mock_ticker.modelGreeks = ticker_greeks
        mock_ticker.lastGreeks = None
        mock_instance.reqMktData.return_value = mock_ticker
        mock_instance.waitOnUpdateAsync = AsyncMock()
        mock_instance.cancelMktData = MagicMock()
        mock_instance.disconnect = MagicMock()

        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    def test_empty_portfolio_returns_zero_greeks(self):
        self._setup_mock_ib(portfolio_items=[])
        result = asyncio.run(self.mod.fetch_greeks("127.0.0.1", 7497, 1001))
        pg = result["portfolio_greeks"]
        assert pg["delta"] == 0.0
        assert pg["gamma"] == 0.0
        assert pg["theta"] == 0.0
        assert pg["vega"] == 0.0
        assert result["positions"] == []

    def test_option_position_aggregates_greeks(self):
        item = _make_portfolio_item(position=2.0)
        mock_ib = self._setup_mock_ib(
            portfolio_items=[item],
            ticker_greeks=_make_greeks(delta=0.5, gamma=0.01, theta=-0.1, vega=0.2),
        )
        result = asyncio.run(self.mod.fetch_greeks("127.0.0.1", 7497, 1001))
        pg = result["portfolio_greeks"]
        # Scaled by position=2
        assert pg["delta"] == pytest.approx(1.0)
        assert pg["gamma"] == pytest.approx(0.02)
        assert pg["theta"] == pytest.approx(-0.2)
        assert pg["vega"] == pytest.approx(0.4)

    def test_non_option_position_has_zero_greeks_in_portfolio(self):
        stk_contract = _make_contract(secType="STK", strike=None, right=None, expiry=None)
        del stk_contract.lastTradeDateOrContractMonth
        stk_contract.lastTradeDateOrContractMonth = None
        item = _make_portfolio_item(contract=stk_contract, position=100.0)
        self._setup_mock_ib(portfolio_items=[item])
        result = asyncio.run(self.mod.fetch_greeks("127.0.0.1", 7497, 1001))
        pg = result["portfolio_greeks"]
        assert pg["delta"] == 0.0
        pos = result["positions"][0]
        assert pos["sec_type"] == "STK"
        assert pos["greeks"]["delta"] is None

    def test_disconnects_after_success(self):
        mock_ib = self._setup_mock_ib(portfolio_items=[])
        asyncio.run(self.mod.fetch_greeks("127.0.0.1", 7497, 1001))
        mock_ib.disconnect.assert_called_once()

    def test_raises_connection_error_on_timeout(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=asyncio.TimeoutError())
        with pytest.raises(ConnectionError, match="Timed out"):
            asyncio.run(self.mod.fetch_greeks("127.0.0.1", 7497, 1001))

    def test_raises_connection_error_on_refused(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        with pytest.raises(ConnectionError, match="Cannot reach TWS"):
            asyncio.run(self.mod.fetch_greeks("127.0.0.1", 7497, 1001))

    def test_result_contains_timestamp(self):
        self._setup_mock_ib(portfolio_items=[])
        result = asyncio.run(self.mod.fetch_greeks("127.0.0.1", 7497, 1001))
        assert result["timestamp"].endswith("Z")

    def test_readonly_connection(self):
        mock_ib = self._setup_mock_ib(portfolio_items=[])
        asyncio.run(self.mod.fetch_greeks("127.0.0.1", 7497, 1001))
        mock_ib.connectAsync.assert_called_once_with(
            "127.0.0.1", 7497, clientId=1001, readonly=True
        )

    def test_output_is_json_serialisable(self):
        item = _make_portfolio_item(position=1.0)
        self._setup_mock_ib(
            portfolio_items=[item],
            ticker_greeks=_make_greeks(),
        )
        result = asyncio.run(self.mod.fetch_greeks("127.0.0.1", 7497, 1001))
        json.dumps(result)  # must not raise


class TestMain:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_connection_error_exits_with_code_1(self, capsys):
        async def raise_err(host, port, client_id):
            raise ConnectionError("Cannot reach TWS")

        with patch("sys.argv", ["get_greeks.py"]):
            with patch.object(self.mod, "fetch_greeks", raise_err):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()

        assert exc.value.code == 1
        err = json.loads(capsys.readouterr().err)
        assert err["status"] == "tws_unavailable"

    def test_timestamp_ends_with_z(self):
        assert self.mod.utc_timestamp().endswith("Z")


class TestConnectionIdRegister:
    def test_get_greeks_registered(self):
        import pathlib, json
        reg = pathlib.Path(__file__).parent.parent / "tools" / "connection_ids.json"
        data = json.loads(reg.read_text())
        assert "1001" in data["ids"]
        assert data["ids"]["1001"]["tool"] == "get_greeks.py"
        assert data["ids"]["1001"]["read_only"] is True
