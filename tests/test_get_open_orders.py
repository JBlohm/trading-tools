"""Tests for get_open_orders.py."""

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
        "symbol": "AAPL",
        "secType": "STK",
        "currency": "USD",
        "exchange": "SMART",
        "lastTradeDateOrContractMonth": None,
        "strike": None,
        "right": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_order(**kwargs):
    defaults = {
        "orderId": 42,
        "permId": 999,
        "clientId": 1003,
        "action": "BUY",
        "orderType": "LMT",
        "totalQuantity": 100.0,
        "lmtPrice": 175.00,
        "auxPrice": IB_UNSET,
        "tif": "DAY",
        "account": "DU123456",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_order_status(**kwargs):
    defaults = {
        "status": "PreSubmitted",
        "filled": 0.0,
        "remaining": 100.0,
        "avgFillPrice": 0.0,
        "whyHeld": "",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_execution(**kwargs):
    defaults = {
        "execId": "0000e1a7.5f3e1234.01.01",
        "time": "20251220 10:30:00 US/Eastern",
        "shares": 50.0,
        "price": 175.50,
        "cumQty": 50.0,
        "avgPrice": 175.50,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_fill(**kwargs):
    return SimpleNamespace(execution=_make_execution(**kwargs))


def _make_trade(contract=None, order=None, status=None, fills=None):
    return SimpleNamespace(
        contract=contract or _make_contract(),
        order=order or _make_order(),
        orderStatus=status or _make_order_status(),
        fills=fills or [],
    )


def _inject_fake_ib_async(mock_ib_class=None):
    fake = types.ModuleType("ib_async")
    fake.IB = mock_ib_class or MagicMock
    sys.modules["ib_async"] = fake
    if "tools.get_open_orders" in sys.modules:
        del sys.modules["tools.get_open_orders"]
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    import tools.get_open_orders as mod
    return mod, fake


class TestTradeToDict:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_basic_fields(self):
        trade = _make_trade()
        result = self.mod._trade_to_dict(trade)
        assert result["order_id"] == 42
        assert result["symbol"] == "AAPL"
        assert result["action"] == "BUY"
        assert result["order_type"] == "LMT"
        assert result["total_quantity"] == 100.0
        assert result["lmt_price"] == pytest.approx(175.00)
        assert result["status"] == "PreSubmitted"
        assert result["fills"] == []

    def test_unset_lmt_price_becomes_none(self):
        order = _make_order(lmtPrice=IB_UNSET)
        trade = _make_trade(order=order)
        result = self.mod._trade_to_dict(trade)
        assert result["lmt_price"] is None

    def test_fills_are_included(self):
        fill = _make_fill(shares=50.0, price=175.50)
        trade = _make_trade(fills=[fill])
        result = self.mod._trade_to_dict(trade)
        assert len(result["fills"]) == 1
        assert result["fills"][0]["shares"] == 50.0
        assert result["fills"][0]["price"] == pytest.approx(175.50)

    def test_empty_why_held_becomes_none(self):
        status = _make_order_status(whyHeld="")
        trade = _make_trade(status=status)
        result = self.mod._trade_to_dict(trade)
        assert result["why_held"] is None

    def test_result_is_json_serialisable(self):
        trade = _make_trade()
        result = self.mod._trade_to_dict(trade)
        json.dumps(result)


class TestArgParsing:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_defaults_to_paper(self):
        with patch("sys.argv", ["get_open_orders.py"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_PAPER

    def test_live_flag(self):
        with patch("sys.argv", ["get_open_orders.py", "--live"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_LIVE

    def test_custom_port(self):
        with patch("sys.argv", ["get_open_orders.py", "--port", "4002"]):
            args = self.mod.parse_args()
        assert args.port == 4002


class TestFetchOpenOrders:
    def setup_method(self):
        self.mock_ib_class = MagicMock()
        self.mod, _ = _inject_fake_ib_async(self.mock_ib_class)

    def _setup_mock_ib(self, trades=None):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.reqAllOpenOrdersAsync = AsyncMock(return_value=trades or [])
        mock_instance.disconnect = MagicMock()
        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    def test_empty_returns_empty_list(self):
        self._setup_mock_ib(trades=[])
        result = asyncio.run(self.mod.fetch_open_orders("127.0.0.1", 7497, 1003))
        assert result == []

    def test_returns_serialised_trades(self):
        trades = [_make_trade(), _make_trade(order=_make_order(orderId=43))]
        self._setup_mock_ib(trades=trades)
        result = asyncio.run(self.mod.fetch_open_orders("127.0.0.1", 7497, 1003))
        assert len(result) == 2
        order_ids = {r["order_id"] for r in result}
        assert order_ids == {42, 43}

    def test_readonly_connection(self):
        mock_ib = self._setup_mock_ib()
        asyncio.run(self.mod.fetch_open_orders("127.0.0.1", 7497, 1003))
        mock_ib.connectAsync.assert_called_once_with(
            "127.0.0.1", 7497, clientId=1003, readonly=True
        )

    def test_uses_all_open_orders_not_client_scoped(self):
        # Verifies reqAllOpenOrdersAsync is used so orders from other clients are visible
        mock_ib = self._setup_mock_ib(trades=[_make_trade(order=_make_order(clientId=1004))])
        result = asyncio.run(self.mod.fetch_open_orders("127.0.0.1", 7497, 1003))
        assert len(result) == 1
        assert result[0]["client_id"] == 1004
        mock_ib.reqAllOpenOrdersAsync.assert_called_once()

    def test_disconnects_after_success(self):
        mock_ib = self._setup_mock_ib()
        asyncio.run(self.mod.fetch_open_orders("127.0.0.1", 7497, 1003))
        mock_ib.disconnect.assert_called_once()

    def test_raises_connection_error_on_timeout(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=asyncio.TimeoutError())
        with pytest.raises(ConnectionError, match="Timed out"):
            asyncio.run(self.mod.fetch_open_orders("127.0.0.1", 7497, 1003))

    def test_raises_connection_error_on_refused(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        with pytest.raises(ConnectionError, match="Cannot reach TWS"):
            asyncio.run(self.mod.fetch_open_orders("127.0.0.1", 7497, 1003))


class TestMain:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_connection_error_exits_1(self, capsys):
        async def raise_err(host, port, client_id):
            raise ConnectionError("Cannot reach TWS")

        with patch("sys.argv", ["get_open_orders.py"]):
            with patch.object(self.mod, "fetch_open_orders", raise_err):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()

        assert exc.value.code == 1
        err = json.loads(capsys.readouterr().err)
        assert err["status"] == "tws_unavailable"

    def test_timestamp_format(self):
        assert self.mod.utc_timestamp().endswith("Z")


class TestConnectionIdRegister:
    def test_get_open_orders_registered(self):
        import pathlib, json
        reg = pathlib.Path(__file__).parent.parent / "tools" / "connection_ids.json"
        data = json.loads(reg.read_text())
        assert "1003" in data["ids"]
        assert data["ids"]["1003"]["tool"] == "get_open_orders.py"
        assert data["ids"]["1003"]["read_only"] is True
