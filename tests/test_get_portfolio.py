"""Tests for get_portfolio.py."""

import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to construct fake ib_async objects without the real package
# ---------------------------------------------------------------------------


def _make_contract(**kwargs):
    defaults = {
        "symbol": "AAPL",
        "secType": "STK",
        "currency": "USD",
        "primaryExch": "NASDAQ",
        "exchange": "SMART",
        "conId": 265598,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_portfolio_item(**kwargs):
    defaults = {
        "contract": _make_contract(),
        "position": 100.0,
        "marketPrice": 175.50,
        "marketValue": 17550.0,
        "averageCost": 150.0,
        "unrealizedPNL": 2550.0,
        "realizedPNL": 0.0,
        "account": "DU123456",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Unit tests for portfolio_item_to_dict
# ---------------------------------------------------------------------------


class TestPortfolioItemToDict:
    def setup_method(self):
        # Ensure the module can be imported even without ib_async installed
        import importlib
        import types

        fake_ib_async = types.ModuleType("ib_async")
        fake_ib_async.IB = MagicMock
        fake_ib_async.PortfolioItem = object
        sys.modules.setdefault("ib_async", fake_ib_async)

        # Re-import to pick up the fake module if needed
        if "tools.get_portfolio" in sys.modules:
            del sys.modules["tools.get_portfolio"]

        sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
        from tools.get_portfolio import portfolio_item_to_dict

        self.fn = portfolio_item_to_dict

    def test_basic_fields_present(self):
        item = _make_portfolio_item()
        result = self.fn(item)
        assert result["symbol"] == "AAPL"
        assert result["sec_type"] == "STK"
        assert result["currency"] == "USD"
        assert result["position"] == 100.0
        assert result["market_price"] == 175.50
        assert result["market_value"] == 17550.0
        assert result["average_cost"] == 150.0
        assert result["unrealized_pnl"] == 2550.0
        assert result["realized_pnl"] == 0.0
        assert result["account"] == "DU123456"

    def test_uses_primary_exchange_when_available(self):
        item = _make_portfolio_item()
        item.contract = _make_contract(primaryExch="NASDAQ", exchange="SMART")
        result = self.fn(item)
        assert result["exchange"] == "NASDAQ"

    def test_falls_back_to_exchange_when_primary_empty(self):
        item = _make_portfolio_item()
        item.contract = _make_contract(primaryExch="", exchange="SMART")
        result = self.fn(item)
        assert result["exchange"] == "SMART"

    def test_negative_position_short(self):
        item = _make_portfolio_item(position=-50.0, unrealizedPNL=-500.0)
        result = self.fn(item)
        assert result["position"] == -50.0
        assert result["unrealized_pnl"] == -500.0

    def test_result_is_json_serialisable(self):
        item = _make_portfolio_item()
        result = self.fn(item)
        # Should not raise
        serialised = json.dumps(result)
        roundtrip = json.loads(serialised)
        assert roundtrip["symbol"] == "AAPL"


# ---------------------------------------------------------------------------
# Unit tests for argument parsing
# ---------------------------------------------------------------------------


class TestArgParsing:
    def setup_method(self):
        import types

        fake_ib_async = types.ModuleType("ib_async")
        fake_ib_async.IB = MagicMock
        fake_ib_async.PortfolioItem = object
        sys.modules.setdefault("ib_async", fake_ib_async)

        if "tools.get_portfolio" in sys.modules:
            del sys.modules["tools.get_portfolio"]

        sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
        import tools.get_portfolio as mod

        self.mod = mod

    def test_defaults_to_paper_port(self):
        with patch("sys.argv", ["get_portfolio.py"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_PAPER
        assert args.host == self.mod.DEFAULT_HOST

    def test_live_flag_sets_live_port(self):
        with patch("sys.argv", ["get_portfolio.py", "--live"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_LIVE

    def test_paper_flag_sets_paper_port(self):
        with patch("sys.argv", ["get_portfolio.py", "--paper"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_PAPER

    def test_custom_host(self):
        with patch("sys.argv", ["get_portfolio.py", "--host", "10.0.0.5"]):
            args = self.mod.parse_args()
        assert args.host == "10.0.0.5"

    def test_custom_port(self):
        with patch("sys.argv", ["get_portfolio.py", "--port", "4002"]):
            args = self.mod.parse_args()
        assert args.port == 4002

    def test_live_and_paper_are_mutually_exclusive(self):
        with patch("sys.argv", ["get_portfolio.py", "--live", "--paper"]):
            with pytest.raises(SystemExit):
                self.mod.parse_args()


# ---------------------------------------------------------------------------
# Unit tests for CLI error output
# ---------------------------------------------------------------------------


class TestMain:
    def setup_method(self):
        import types

        fake_ib_async = types.ModuleType("ib_async")
        fake_ib_async.IB = MagicMock
        fake_ib_async.PortfolioItem = object
        sys.modules.setdefault("ib_async", fake_ib_async)

        if "tools.get_portfolio" in sys.modules:
            del sys.modules["tools.get_portfolio"]

        sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
        import tools.get_portfolio as mod

        self.mod = mod

    def test_utc_timestamp_is_zulu(self):
        assert self.mod.utc_timestamp().endswith("Z")

    def test_connection_error_prints_json_to_stderr(self, capsys):
        async def raise_connection_error(host, port, client_id):
            raise ConnectionError("Cannot reach TWS at 127.0.0.1:9")

        with patch("sys.argv", ["get_portfolio.py", "--host", "127.0.0.1", "--port", "9"]):
            with patch.object(self.mod, "fetch_portfolio", raise_connection_error):
                with pytest.raises(SystemExit) as exc_info:
                    self.mod.main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""

        error = json.loads(captured.err)
        assert error["status"] == "tws_unavailable"
        assert error["hint"] == "The TWS API is temporarily not available. Please try again later."
        assert error["timestamp"].endswith("Z")


# ---------------------------------------------------------------------------
# Integration-style tests for fetch_portfolio (mocking IB)
# ---------------------------------------------------------------------------


class TestFetchPortfolio:
    def setup_method(self):
        import types

        self.mock_ib_class = MagicMock()
        fake_ib_async = types.ModuleType("ib_async")
        fake_ib_async.IB = self.mock_ib_class
        fake_ib_async.PortfolioItem = object
        sys.modules["ib_async"] = fake_ib_async

        if "tools.get_portfolio" in sys.modules:
            del sys.modules["tools.get_portfolio"]

        sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
        import tools.get_portfolio as mod

        self.mod = mod

    def _setup_mock_ib(self, portfolio_items=None):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.portfolio.return_value = portfolio_items or []
        mock_instance.disconnect = MagicMock()
        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    def test_returns_empty_list_for_empty_portfolio(self):
        self._setup_mock_ib(portfolio_items=[])
        result = asyncio.run(self.mod.fetch_portfolio("127.0.0.1", 7497, 1000))
        assert result == []

    def test_returns_correct_items(self):
        items = [_make_portfolio_item(), _make_portfolio_item(position=200.0)]
        mock_ib = self._setup_mock_ib(portfolio_items=items)
        result = asyncio.run(self.mod.fetch_portfolio("127.0.0.1", 7497, 1000))
        assert len(result) == 2
        assert result[0]["symbol"] == "AAPL"
        assert result[1]["position"] == 200.0

    def test_disconnects_after_success(self):
        mock_ib = self._setup_mock_ib()
        asyncio.run(self.mod.fetch_portfolio("127.0.0.1", 7497, 1000))
        mock_ib.disconnect.assert_called_once()

    def test_disconnects_after_portfolio_error(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.portfolio.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            asyncio.run(self.mod.fetch_portfolio("127.0.0.1", 7497, 1000))
        mock_ib.disconnect.assert_called_once()

    def test_raises_connection_error_on_timeout(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch.object(self.mod, "CONNECT_TIMEOUT", 0.001):
            with pytest.raises(ConnectionError, match="Timed out"):
                asyncio.run(self.mod.fetch_portfolio("127.0.0.1", 7497, 1000))

    def test_raises_connection_error_on_refused(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(
            side_effect=ConnectionRefusedError("Connection refused")
        )
        with pytest.raises(ConnectionError, match="Cannot reach TWS"):
            asyncio.run(self.mod.fetch_portfolio("127.0.0.1", 7497, 1000))

    def test_readonly_flag_passed(self):
        mock_ib = self._setup_mock_ib()
        asyncio.run(self.mod.fetch_portfolio("127.0.0.1", 7497, 1000))
        mock_ib.connectAsync.assert_called_once_with(
            "127.0.0.1", 7497, clientId=1000, readonly=True
        )


# ---------------------------------------------------------------------------
# Connection ID register test
# ---------------------------------------------------------------------------


class TestConnectionIdRegister:
    def test_register_file_exists_and_contains_tool(self):
        import pathlib

        register_path = (
            pathlib.Path(__file__).parent.parent / "tools" / "connection_ids.json"
        )
        assert register_path.exists(), "connection_ids.json register not found"
        data = json.loads(register_path.read_text())
        assert "ids" in data
        assert "1000" in data["ids"]
        assert data["ids"]["1000"]["tool"] == "get_portfolio.py"
        assert data["ids"]["1000"]["read_only"] is True

    def test_all_ids_are_1000_or_above(self):
        import pathlib

        register_path = (
            pathlib.Path(__file__).parent.parent / "tools" / "connection_ids.json"
        )
        data = json.loads(register_path.read_text())
        for id_str in data["ids"]:
            assert int(id_str) >= 1000, f"ID {id_str} is below 1000"
