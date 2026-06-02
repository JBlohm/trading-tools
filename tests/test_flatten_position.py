"""Tests for flatten_position.py."""

import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_contract(**kwargs):
    defaults = {
        "symbol": "AAPL",
        "secType": "STK",
        "currency": "USD",
        "primaryExchange": "NASDAQ",
        "exchange": "SMART",
        "conId": 12345,
        "lastTradeDateOrContractMonth": None,
        "strike": None,
        "right": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_opt_contract(**kwargs):
    defaults = {
        "symbol": "SPY",
        "secType": "OPT",
        "currency": "USD",
        "primaryExchange": "CBOE",
        "exchange": "SMART",
        "conId": 99999,
        "lastTradeDateOrContractMonth": "20251220",
        "strike": 500.0,
        "right": "C",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_portfolio_item(contract=None, position=100.0, account="DU123456", **kwargs):
    if contract is None:
        contract = _make_contract()
    return SimpleNamespace(
        contract=contract,
        position=position,
        marketPrice=150.0,
        marketValue=150.0 * position,
        unrealizedPNL=500.0,
        account=account,
        **kwargs,
    )


def _make_args(scope="symbol", scope_value="AAPL", simulate=True, confirm=False, port=7497, host="127.0.0.1"):
    return SimpleNamespace(
        scope=scope,
        scope_value=scope_value,
        simulate=simulate,
        confirm=confirm,
        port=port,
        host=host,
    )


def _inject_fake_ib_async(mock_ib_class=None):
    fake = types.ModuleType("ib_async")
    fake.IB = mock_ib_class or MagicMock
    fake.Contract = MagicMock
    fake.MarketOrder = MagicMock
    fake.Option = MagicMock
    fake.Future = MagicMock
    fake.Stock = MagicMock
    sys.modules["ib_async"] = fake
    for key in list(sys.modules):
        if "tools.flatten_position" in key:
            del sys.modules[key]
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    import tools.flatten_position as mod
    return mod, fake


class TestHelpers:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_closing_action_long_is_sell(self):
        assert self.mod._closing_action(10.0) == "SELL"

    def test_closing_action_short_is_buy(self):
        assert self.mod._closing_action(-5.0) == "BUY"

    def test_position_matches_scope_symbol(self):
        item = _make_portfolio_item(contract=_make_contract(symbol="AAPL"))
        assert self.mod._position_matches_scope(item, "symbol", "AAPL")
        assert not self.mod._position_matches_scope(item, "symbol", "GOOG")

    def test_position_matches_scope_symbol_case_insensitive(self):
        item = _make_portfolio_item(contract=_make_contract(symbol="SPY"))
        assert self.mod._position_matches_scope(item, "symbol", "spy")

    def test_position_matches_scope_account(self):
        item = _make_portfolio_item(account="DU999")
        assert self.mod._position_matches_scope(item, "account", "DU999")
        assert not self.mod._position_matches_scope(item, "account", "DU000")

    def test_position_to_summary_fields(self):
        item = _make_portfolio_item()
        summary = self.mod._position_to_summary(item)
        assert summary["symbol"] == "AAPL"
        assert summary["sec_type"] == "STK"
        assert summary["position"] == 100.0

    def test_closing_order_spec_sell_for_long(self):
        item = _make_portfolio_item(position=50.0)
        spec = self.mod._closing_order_spec(item)
        assert spec["action"] == "SELL"
        assert spec["quantity"] == 50.0
        assert spec["order_type"] == "MKT"

    def test_closing_order_spec_buy_for_short(self):
        item = _make_portfolio_item(position=-20.0)
        spec = self.mod._closing_order_spec(item)
        assert spec["action"] == "BUY"
        assert spec["quantity"] == 20.0


class TestArgParsing:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_required_scope_and_value(self):
        with patch("sys.argv", ["flatten_position.py", "--scope", "symbol", "--scope-value", "AAPL"]):
            args = self.mod.parse_args()
        assert args.scope == "symbol"
        assert args.scope_value == "AAPL"

    def test_simulate_flag(self):
        with patch("sys.argv", ["flatten_position.py", "--scope", "symbol", "--scope-value", "AAPL", "--simulate"]):
            args = self.mod.parse_args()
        assert args.simulate is True

    def test_defaults_to_paper_port(self):
        with patch("sys.argv", ["flatten_position.py", "--scope", "symbol", "--scope-value", "X"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_PAPER

    def test_live_flag(self):
        with patch("sys.argv", ["flatten_position.py", "--scope", "symbol", "--scope-value", "X", "--live"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_LIVE

    def test_scope_invalid_choice_fails(self):
        with patch("sys.argv", ["flatten_position.py", "--scope", "invalid", "--scope-value", "X"]):
            with pytest.raises(SystemExit):
                self.mod.parse_args()


class TestFlattenPosition:
    def setup_method(self):
        self.mock_ib_class = MagicMock()
        self.mod, _ = _inject_fake_ib_async(self.mock_ib_class)

    def _setup_mock_ib(self, portfolio_items=None, simulate=True):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.portfolio.return_value = portfolio_items or []
        mock_instance.disconnect = MagicMock()

        if not simulate:
            mock_trade = MagicMock()
            mock_trade.order.orderId = 42
            mock_trade.order.permId = 9999
            mock_trade.orderStatus.status = "Submitted"
            mock_instance.placeOrder.return_value = mock_trade

        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    def test_flatten_single_symbol_simulate(self):
        item = _make_portfolio_item(contract=_make_contract(symbol="AAPL"), position=100.0)
        self._setup_mock_ib(portfolio_items=[item], simulate=True)
        args = _make_args(scope="symbol", scope_value="AAPL", simulate=True)
        result = asyncio.run(self.mod.flatten_position("127.0.0.1", 7497, 1006, args))
        assert result["status"] == "simulated"
        assert len(result["orders"]) == 1
        assert result["orders"][0]["action"] == "SELL"
        assert result["orders"][0]["quantity"] == 100.0
        assert result["orders"][0]["simulated"] is True

    def test_flatten_strategy_matches_symbol(self):
        opt = _make_portfolio_item(contract=_make_opt_contract(symbol="SPY"), position=5.0)
        self._setup_mock_ib(portfolio_items=[opt], simulate=True)
        args = _make_args(scope="strategy", scope_value="SPY", simulate=True)
        result = asyncio.run(self.mod.flatten_position("127.0.0.1", 7497, 1006, args))
        assert result["status"] == "simulated"
        assert len(result["orders"]) == 1

    def test_flatten_filters_out_non_matching_symbols(self):
        aapl = _make_portfolio_item(contract=_make_contract(symbol="AAPL"), position=100.0)
        goog = _make_portfolio_item(contract=_make_contract(symbol="GOOG"), position=50.0)
        self._setup_mock_ib(portfolio_items=[aapl, goog], simulate=True)
        args = _make_args(scope="symbol", scope_value="AAPL", simulate=True)
        result = asyncio.run(self.mod.flatten_position("127.0.0.1", 7497, 1006, args))
        assert len(result["orders"]) == 1
        assert result["orders"][0]["symbol"] == "AAPL"

    def test_flatten_zero_position_skipped(self):
        item = _make_portfolio_item(contract=_make_contract(symbol="AAPL"), position=0.0)
        self._setup_mock_ib(portfolio_items=[item], simulate=True)
        args = _make_args(scope="symbol", scope_value="AAPL", simulate=True)
        result = asyncio.run(self.mod.flatten_position("127.0.0.1", 7497, 1006, args))
        assert result["status"] == "no_positions"
        assert result["orders"] == []

    def test_flatten_no_matching_positions(self):
        item = _make_portfolio_item(contract=_make_contract(symbol="GOOG"), position=10.0)
        self._setup_mock_ib(portfolio_items=[item], simulate=True)
        args = _make_args(scope="symbol", scope_value="AAPL", simulate=True)
        result = asyncio.run(self.mod.flatten_position("127.0.0.1", 7497, 1006, args))
        assert result["status"] == "no_positions"
        assert result["positions_found"] == []

    def test_account_scope_requires_confirm(self):
        self._setup_mock_ib()
        args = _make_args(scope="account", scope_value="DU123456", simulate=True, confirm=False)
        with pytest.raises(ValueError, match="--confirm"):
            asyncio.run(self.mod.flatten_position("127.0.0.1", 7497, 1006, args))

    def test_account_scope_with_confirm_succeeds(self):
        item = _make_portfolio_item(account="DU123456", position=100.0)
        self._setup_mock_ib(portfolio_items=[item], simulate=True)
        args = _make_args(scope="account", scope_value="DU123456", simulate=True, confirm=True)
        result = asyncio.run(self.mod.flatten_position("127.0.0.1", 7497, 1006, args))
        assert result["status"] == "simulated"

    def test_result_contains_audit_id(self):
        self._setup_mock_ib()
        args = _make_args()
        result = asyncio.run(self.mod.flatten_position("127.0.0.1", 7497, 1006, args))
        assert "audit_id" in result
        assert len(result["audit_id"]) == 36  # UUID format

    def test_result_contains_timestamp(self):
        self._setup_mock_ib()
        args = _make_args()
        result = asyncio.run(self.mod.flatten_position("127.0.0.1", 7497, 1006, args))
        assert result["timestamp"].endswith("Z")

    def test_result_is_json_serialisable(self):
        item = _make_portfolio_item(position=10.0)
        self._setup_mock_ib(portfolio_items=[item], simulate=True)
        args = _make_args()
        result = asyncio.run(self.mod.flatten_position("127.0.0.1", 7497, 1006, args))
        json.dumps(result)

    def test_disconnects_on_success(self):
        mock_ib = self._setup_mock_ib()
        args = _make_args()
        asyncio.run(self.mod.flatten_position("127.0.0.1", 7497, 1006, args))
        mock_ib.disconnect.assert_called_once()

    def test_raises_connection_error_on_timeout(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=asyncio.TimeoutError())
        args = _make_args()
        with pytest.raises(ConnectionError, match="Timed out"):
            asyncio.run(self.mod.flatten_position("127.0.0.1", 7497, 1006, args))

    def test_raises_connection_error_on_refused(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        args = _make_args()
        with pytest.raises(ConnectionError, match="Cannot reach TWS"):
            asyncio.run(self.mod.flatten_position("127.0.0.1", 7497, 1006, args))

    def test_short_position_generates_buy_order(self):
        item = _make_portfolio_item(contract=_make_contract(symbol="TSLA"), position=-30.0)
        self._setup_mock_ib(portfolio_items=[item], simulate=True)
        args = _make_args(scope="symbol", scope_value="TSLA", simulate=True)
        result = asyncio.run(self.mod.flatten_position("127.0.0.1", 7497, 1006, args))
        assert result["orders"][0]["action"] == "BUY"
        assert result["orders"][0]["quantity"] == 30.0

    def test_simulate_uses_readonly_connection(self):
        mock_ib = self._setup_mock_ib()
        args = _make_args(simulate=True)
        asyncio.run(self.mod.flatten_position("127.0.0.1", 7497, 1006, args))
        mock_ib.connectAsync.assert_called_once_with(
            "127.0.0.1", 7497, clientId=1006, readonly=True
        )

    def test_scope_and_value_in_result(self):
        self._setup_mock_ib()
        args = _make_args(scope="symbol", scope_value="AAPL", simulate=True)
        result = asyncio.run(self.mod.flatten_position("127.0.0.1", 7497, 1006, args))
        assert result["scope"]["type"] == "symbol"
        assert result["scope"]["value"] == "AAPL"

    def test_no_new_risk_introduced(self):
        """Closing orders are strictly opposite to the existing position direction."""
        long_item = _make_portfolio_item(position=50.0)
        short_item = _make_portfolio_item(
            contract=_make_contract(symbol="GOOG"), position=-20.0, account="DU123456"
        )
        self._setup_mock_ib(portfolio_items=[long_item, short_item], simulate=True)
        args = _make_args(scope="account", scope_value="DU123456", simulate=True, confirm=True)
        result = asyncio.run(self.mod.flatten_position("127.0.0.1", 7497, 1006, args))
        for order in result["orders"]:
            if order["symbol"] == "AAPL":
                assert order["action"] == "SELL"
            elif order["symbol"] == "GOOG":
                assert order["action"] == "BUY"


class TestMain:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_connection_error_exits_1(self, capsys):
        async def raise_err(host, port, client_id, args):
            raise ConnectionError("Cannot reach TWS")

        with patch("sys.argv", ["flatten_position.py", "--scope", "symbol", "--scope-value", "X"]):
            with patch.object(self.mod, "flatten_position", raise_err):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()

        assert exc.value.code == 1
        err = json.loads(capsys.readouterr().err)
        assert err["status"] == "tws_unavailable"

    def test_value_error_exits_1(self, capsys):
        async def raise_err(host, port, client_id, args):
            raise ValueError("--confirm required")

        with patch("sys.argv", ["flatten_position.py", "--scope", "account", "--scope-value", "DU1"]):
            with patch.object(self.mod, "flatten_position", raise_err):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()

        assert exc.value.code == 1
        err = json.loads(capsys.readouterr().err)
        assert err["status"] == "invalid_input"


class TestConnectionIdRegister:
    def test_flatten_position_registered(self):
        import pathlib, json
        reg = pathlib.Path(__file__).parent.parent / "tools" / "connection_ids.json"
        data = json.loads(reg.read_text())
        assert "1006" in data["ids"]
        assert data["ids"]["1006"]["tool"] == "flatten_position.py"
        assert data["ids"]["1006"]["read_only"] is False
