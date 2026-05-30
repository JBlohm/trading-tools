"""Tests for get_margin_usage.py."""

import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_account_value(account: str, tag: str, value: str, currency: str = "USD"):
    return SimpleNamespace(account=account, tag=tag, value=value, currency=currency)


def _inject_fake_ib_async(mock_ib_class=None):
    fake = types.ModuleType("ib_async")
    fake.IB = mock_ib_class or MagicMock
    sys.modules["ib_async"] = fake
    if "tools.get_margin_usage" in sys.modules:
        del sys.modules["tools.get_margin_usage"]
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    import tools.get_margin_usage as mod
    return mod, fake


class TestParseFloat:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_valid_float(self):
        assert self.mod._parse_float("12345.67") == pytest.approx(12345.67)

    def test_none_returns_none(self):
        assert self.mod._parse_float(None) is None

    def test_non_numeric_string_returns_none(self):
        assert self.mod._parse_float("N/A") is None


class TestArgParsing:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_defaults_to_paper(self):
        with patch("sys.argv", ["get_margin_usage.py"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_PAPER
        assert args.host == self.mod.DEFAULT_HOST
        assert args.account == ""

    def test_live_flag(self):
        with patch("sys.argv", ["get_margin_usage.py", "--live"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_LIVE

    def test_account_filter(self):
        with patch("sys.argv", ["get_margin_usage.py", "--account", "DU999"]):
            args = self.mod.parse_args()
        assert args.account == "DU999"


class TestFetchMarginUsage:
    def setup_method(self):
        self.mock_ib_class = MagicMock()
        self.mod, _ = _inject_fake_ib_async(self.mock_ib_class)

    def _setup_mock_ib(self, summary_items=None):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.reqAccountSummaryAsync = AsyncMock(return_value=summary_items or [])
        mock_instance.disconnect = MagicMock()
        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    def _make_summary(self, account="DU123456"):
        return [
            _make_account_value(account, "NetLiquidation", "500000.00"),
            _make_account_value(account, "ExcessLiquidity", "200000.00"),
            _make_account_value(account, "BuyingPower", "400000.00"),
            _make_account_value(account, "InitMarginReq", "100000.00"),
            _make_account_value(account, "MaintMarginReq", "80000.00"),
            _make_account_value(account, "AvailableFunds", "200000.00"),
            _make_account_value(account, "GrossPositionValue", "300000.00"),
            _make_account_value(account, "TotalCashValue", "250000.00"),
            _make_account_value(account, "UnrealizedPnL", "50000.00"),
            _make_account_value(account, "RealizedPnL", "10000.00"),
        ]

    def test_returns_correct_nlv_and_excess(self):
        self._setup_mock_ib(summary_items=self._make_summary())
        result = asyncio.run(self.mod.fetch_margin_usage("127.0.0.1", 7497, 1002))
        acct = result["accounts"]["DU123456"]
        assert acct["nlv"] == pytest.approx(500_000.0)
        assert acct["excess_liquidity"] == pytest.approx(200_000.0)

    def test_result_has_timestamp(self):
        self._setup_mock_ib(summary_items=[])
        result = asyncio.run(self.mod.fetch_margin_usage("127.0.0.1", 7497, 1002))
        assert result["timestamp"].endswith("Z")

    def test_account_filter_excludes_other_accounts(self):
        items = self._make_summary("DU111") + self._make_summary("DU222")
        self._setup_mock_ib(summary_items=items)
        result = asyncio.run(self.mod.fetch_margin_usage("127.0.0.1", 7497, 1002, account="DU111"))
        assert "DU111" in result["accounts"]
        assert "DU222" not in result["accounts"]

    def test_multiple_accounts_without_filter(self):
        items = self._make_summary("DU111") + self._make_summary("DU222")
        self._setup_mock_ib(summary_items=items)
        result = asyncio.run(self.mod.fetch_margin_usage("127.0.0.1", 7497, 1002))
        assert "DU111" in result["accounts"]
        assert "DU222" in result["accounts"]

    def test_readonly_connection(self):
        mock_ib = self._setup_mock_ib()
        asyncio.run(self.mod.fetch_margin_usage("127.0.0.1", 7497, 1002))
        mock_ib.connectAsync.assert_called_once_with(
            "127.0.0.1", 7497, clientId=1002, readonly=True
        )

    def test_disconnects_on_success(self):
        mock_ib = self._setup_mock_ib(summary_items=self._make_summary())
        asyncio.run(self.mod.fetch_margin_usage("127.0.0.1", 7497, 1002))
        mock_ib.disconnect.assert_called_once()

    def test_raises_connection_error_on_timeout(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=asyncio.TimeoutError())
        with pytest.raises(ConnectionError, match="Timed out"):
            asyncio.run(self.mod.fetch_margin_usage("127.0.0.1", 7497, 1002))

    def test_raises_connection_error_on_refused(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        with pytest.raises(ConnectionError, match="Cannot reach TWS"):
            asyncio.run(self.mod.fetch_margin_usage("127.0.0.1", 7497, 1002))

    def test_output_is_json_serialisable(self):
        self._setup_mock_ib(summary_items=self._make_summary())
        result = asyncio.run(self.mod.fetch_margin_usage("127.0.0.1", 7497, 1002))
        json.dumps(result)


class TestMain:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_connection_error_exits_1(self, capsys):
        async def raise_err(host, port, client_id, account):
            raise ConnectionError("Cannot reach TWS")

        with patch("sys.argv", ["get_margin_usage.py"]):
            with patch.object(self.mod, "fetch_margin_usage", raise_err):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()

        assert exc.value.code == 1
        err = json.loads(capsys.readouterr().err)
        assert err["status"] == "tws_unavailable"
        assert "try again later" in err["hint"]


class TestConnectionIdRegister:
    def test_get_margin_usage_registered(self):
        import pathlib, json
        reg = pathlib.Path(__file__).parent.parent / "tools" / "connection_ids.json"
        data = json.loads(reg.read_text())
        assert "1002" in data["ids"]
        assert data["ids"]["1002"]["tool"] == "get_margin_usage.py"
        assert data["ids"]["1002"]["read_only"] is True
