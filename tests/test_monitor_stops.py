"""Tests for monitor_stops.py."""

import asyncio
import json
import sys
import types
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_contract(**kwargs):
    defaults = {
        "symbol": "SPY",
        "secType": "OPT",
        "currency": "USD",
        "primaryExchange": "CBOE",
        "exchange": "SMART",
        "conId": 99001,
        "lastTradeDateOrContractMonth": "20251220",
        "strike": 500.0,
        "right": "P",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_stk_contract(**kwargs):
    defaults = {
        "symbol": "AAPL",
        "secType": "STK",
        "currency": "USD",
        "primaryExchange": "NASDAQ",
        "exchange": "SMART",
        "conId": 99002,
    }
    defaults.update(kwargs)
    c = SimpleNamespace(**defaults)
    c.lastTradeDateOrContractMonth = None
    c.strike = None
    c.right = None
    return c


def _make_portfolio_item(contract=None, position=-1.0, market_price=8.0, account="DU123456"):
    if contract is None:
        contract = _make_contract()
    return SimpleNamespace(
        contract=contract,
        position=position,
        marketPrice=market_price,
        marketValue=market_price * abs(position),
        unrealizedPNL=-200.0,
        account=account,
    )


def _make_order(order_type="STP", action="BUY", total_qty=1.0, aux_price=490.0, lmt_price=1.7976931348623157e308, order_id=1, perm_id=100, tif="GTC"):
    o = SimpleNamespace(
        orderType=order_type,
        action=action,
        totalQuantity=total_qty,
        auxPrice=aux_price,
        lmtPrice=lmt_price,
        orderId=order_id,
        permId=perm_id,
        tif=tif,
    )
    return o


def _make_trade(contract=None, order=None, status="Submitted", log_time=None):
    if contract is None:
        contract = _make_contract()
    if order is None:
        order = _make_order()
    t = datetime.now(timezone.utc) if log_time is None else log_time
    log_entry = SimpleNamespace(time=t)
    return SimpleNamespace(
        contract=contract,
        order=order,
        orderStatus=SimpleNamespace(status=status),
        log=[log_entry],
    )


def _inject_fake_ib_async(mock_ib_class=None):
    fake = types.ModuleType("ib_async")
    fake.IB = mock_ib_class or MagicMock
    sys.modules["ib_async"] = fake
    for key in list(sys.modules):
        if "tools.monitor_stops" in key:
            del sys.modules[key]
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    import tools.monitor_stops as mod
    return mod, fake


class TestHelpers:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_closing_action_for_short_position_is_buy(self):
        assert self.mod._closing_action_for(-1.0) == "BUY"

    def test_closing_action_for_long_position_is_sell(self):
        assert self.mod._closing_action_for(10.0) == "SELL"

    def test_contracts_match_same_option(self):
        c1 = _make_contract(symbol="SPY", secType="OPT", lastTradeDateOrContractMonth="20251220", strike=500.0, right="P")
        c2 = _make_contract(symbol="SPY", secType="OPT", lastTradeDateOrContractMonth="20251220", strike=500.0, right="P")
        assert self.mod._contracts_match(c1, c2)

    def test_contracts_match_different_strike(self):
        c1 = _make_contract(strike=500.0)
        c2 = _make_contract(strike=505.0)
        assert not self.mod._contracts_match(c1, c2)

    def test_contracts_match_different_symbol(self):
        c1 = _make_contract(symbol="SPY")
        c2 = _make_contract(symbol="QQQ")
        assert not self.mod._contracts_match(c1, c2)

    def test_contracts_match_different_right(self):
        c1 = _make_contract(right="C")
        c2 = _make_contract(right="P")
        assert not self.mod._contracts_match(c1, c2)

    def test_get_order_age_hours_fresh(self):
        recent_time = datetime.now(timezone.utc) - timedelta(hours=2)
        trade = _make_trade(log_time=recent_time)
        age = self.mod._get_order_age_hours(trade)
        assert age == pytest.approx(2.0, abs=0.1)

    def test_get_order_age_hours_no_log(self):
        trade = SimpleNamespace(log=[], contract=_make_contract(), order=_make_order(), orderStatus=SimpleNamespace(status="Submitted"))
        age = self.mod._get_order_age_hours(trade)
        assert age is None

    def test_target_reached_sell_when_price_above_limit(self):
        trade = _make_trade(order=_make_order(order_type="LMT", action="SELL", lmt_price=500.0, aux_price=1.7976931348623157e308))
        result = self.mod._order_to_target_dict(trade, pos_market_price=510.0)
        assert result["target_reached"] is True

    def test_target_not_reached_sell_when_price_below_limit(self):
        trade = _make_trade(order=_make_order(order_type="LMT", action="SELL", lmt_price=500.0, aux_price=1.7976931348623157e308))
        result = self.mod._order_to_target_dict(trade, pos_market_price=490.0)
        assert result["target_reached"] is False

    def test_target_reached_buy_when_price_below_limit(self):
        trade = _make_trade(order=_make_order(order_type="LMT", action="BUY", lmt_price=400.0, aux_price=1.7976931348623157e308))
        result = self.mod._order_to_target_dict(trade, pos_market_price=390.0)
        assert result["target_reached"] is True


class TestArgParsing:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_defaults_to_paper_port(self):
        with patch("sys.argv", ["monitor_stops.py"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_PAPER

    def test_live_flag(self):
        with patch("sys.argv", ["monitor_stops.py", "--live"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_LIVE

    def test_custom_max_stop_age(self):
        with patch("sys.argv", ["monitor_stops.py", "--max-stop-age-hours", "24"]):
            args = self.mod.parse_args()
        assert args.max_stop_age_hours == 24.0


class TestMonitorStops:
    def setup_method(self):
        self.mock_ib_class = MagicMock()
        self.mod, _ = _inject_fake_ib_async(self.mock_ib_class)

    def _setup_mock_ib(self, portfolio_items=None, open_trades=None):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.portfolio.return_value = portfolio_items or []
        mock_instance.reqAllOpenOrdersAsync = AsyncMock(return_value=open_trades or [])
        mock_instance.disconnect = MagicMock()
        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    def test_missing_stop_warning_when_no_stop_order(self):
        pos = _make_portfolio_item(position=-1.0)
        self._setup_mock_ib(portfolio_items=[pos], open_trades=[])
        result = asyncio.run(self.mod.monitor_stops("127.0.0.1", 7497, 1007, 48.0))
        assert result["positions"][0]["stop_status"] == "missing"
        assert result["summary"]["missing"] == 1

    def test_covered_when_stop_order_exists(self):
        pos = _make_portfolio_item(position=-1.0)
        stop_trade = _make_trade(
            contract=_make_contract(),
            order=_make_order(order_type="STP", action="BUY"),
            log_time=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        self._setup_mock_ib(portfolio_items=[pos], open_trades=[stop_trade])
        result = asyncio.run(self.mod.monitor_stops("127.0.0.1", 7497, 1007, 48.0))
        assert result["positions"][0]["stop_status"] == "covered"
        assert result["summary"]["covered"] == 1

    def test_stale_stop_warning(self):
        pos = _make_portfolio_item(position=-1.0)
        old_time = datetime.now(timezone.utc) - timedelta(hours=72)
        stop_trade = _make_trade(
            contract=_make_contract(),
            order=_make_order(order_type="STP", action="BUY"),
            log_time=old_time,
        )
        self._setup_mock_ib(portfolio_items=[pos], open_trades=[stop_trade])
        result = asyncio.run(self.mod.monitor_stops("127.0.0.1", 7497, 1007, 48.0))
        assert result["positions"][0]["stop_status"] == "stale"
        assert result["summary"]["stale"] == 1

    def test_target_reached_signal(self):
        pos = _make_portfolio_item(position=1.0, market_price=510.0)
        target_trade = _make_trade(
            contract=_make_contract(),
            order=_make_order(order_type="LMT", action="SELL", lmt_price=500.0, aux_price=1.7976931348623157e308),
        )
        self._setup_mock_ib(portfolio_items=[pos], open_trades=[target_trade])
        result = asyncio.run(self.mod.monitor_stops("127.0.0.1", 7497, 1007, 48.0))
        pos_out = result["positions"][0]
        assert pos_out["target_orders"][0]["target_reached"] is True
        assert result["summary"]["targets_reached"] == 1

    def test_trail_stop_type_recognized(self):
        pos = _make_portfolio_item(position=-1.0)
        trail_trade = _make_trade(
            contract=_make_contract(),
            order=_make_order(order_type="TRAIL", action="BUY"),
        )
        self._setup_mock_ib(portfolio_items=[pos], open_trades=[trail_trade])
        result = asyncio.run(self.mod.monitor_stops("127.0.0.1", 7497, 1007, 48.0))
        assert result["positions"][0]["stop_status"] == "covered"

    def test_zero_position_skipped(self):
        pos = _make_portfolio_item(position=0.0)
        self._setup_mock_ib(portfolio_items=[pos], open_trades=[])
        result = asyncio.run(self.mod.monitor_stops("127.0.0.1", 7497, 1007, 48.0))
        assert result["positions"] == []
        assert result["summary"]["total_positions"] == 0

    def test_stop_in_wrong_direction_ignored(self):
        pos = _make_portfolio_item(position=-1.0)
        wrong_trade = _make_trade(
            contract=_make_contract(),
            order=_make_order(order_type="STP", action="SELL"),  # SELL not BUY — wrong direction for short
        )
        self._setup_mock_ib(portfolio_items=[pos], open_trades=[wrong_trade])
        result = asyncio.run(self.mod.monitor_stops("127.0.0.1", 7497, 1007, 48.0))
        assert result["positions"][0]["stop_status"] == "missing"

    def test_result_has_timestamp(self):
        self._setup_mock_ib()
        result = asyncio.run(self.mod.monitor_stops("127.0.0.1", 7497, 1007, 48.0))
        assert result["timestamp"].endswith("Z")

    def test_result_is_json_serialisable(self):
        pos = _make_portfolio_item(position=-1.0)
        self._setup_mock_ib(portfolio_items=[pos])
        result = asyncio.run(self.mod.monitor_stops("127.0.0.1", 7497, 1007, 48.0))
        json.dumps(result)

    def test_readonly_connection(self):
        mock_ib = self._setup_mock_ib()
        asyncio.run(self.mod.monitor_stops("127.0.0.1", 7497, 1007, 48.0))
        mock_ib.connectAsync.assert_called_once_with(
            "127.0.0.1", 7497, clientId=1007, readonly=True
        )

    def test_disconnects_on_success(self):
        mock_ib = self._setup_mock_ib()
        asyncio.run(self.mod.monitor_stops("127.0.0.1", 7497, 1007, 48.0))
        mock_ib.disconnect.assert_called_once()

    def test_raises_connection_error_on_timeout(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=asyncio.TimeoutError())
        with pytest.raises(ConnectionError, match="Timed out"):
            asyncio.run(self.mod.monitor_stops("127.0.0.1", 7497, 1007, 48.0))

    def test_raises_connection_error_on_refused(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        with pytest.raises(ConnectionError, match="Cannot reach TWS"):
            asyncio.run(self.mod.monitor_stops("127.0.0.1", 7497, 1007, 48.0))

    def test_order_for_different_contract_not_matched(self):
        pos = _make_portfolio_item(position=-1.0, contract=_make_contract(symbol="SPY"))
        other_trade = _make_trade(
            contract=_make_contract(symbol="QQQ"),
            order=_make_order(order_type="STP", action="BUY"),
        )
        self._setup_mock_ib(portfolio_items=[pos], open_trades=[other_trade])
        result = asyncio.run(self.mod.monitor_stops("127.0.0.1", 7497, 1007, 48.0))
        assert result["positions"][0]["stop_status"] == "missing"


class TestMain:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_connection_error_exits_1(self, capsys):
        async def raise_err(host, port, client_id, max_stop_age_hours):
            raise ConnectionError("Cannot reach TWS")

        with patch("sys.argv", ["monitor_stops.py"]):
            with patch.object(self.mod, "monitor_stops", raise_err):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()

        assert exc.value.code == 1
        err = json.loads(capsys.readouterr().err)
        assert err["status"] == "tws_unavailable"


class TestConnectionIdRegister:
    def test_monitor_stops_registered(self):
        import pathlib, json
        reg = pathlib.Path(__file__).parent.parent / "tools" / "connection_ids.json"
        data = json.loads(reg.read_text())
        assert "1007" in data["ids"]
        assert data["ids"]["1007"]["tool"] == "monitor_stops.py"
        assert data["ids"]["1007"]["read_only"] is True
