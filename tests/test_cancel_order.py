"""Tests for cancel_order.py."""

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
        "symbol": "AAPL", "secType": "STK", "currency": "USD", "exchange": "SMART",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_order(**kwargs):
    defaults = {
        "orderId": 42, "permId": 999, "clientId": 1005,
        "action": "BUY", "orderType": "LMT", "totalQuantity": 100.0,
        "lmtPrice": 175.00, "tif": "DAY", "account": "DU123456",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_order_status(**kwargs):
    defaults = {"status": "Cancelled", "filled": 0.0, "remaining": 100.0,
                "avgFillPrice": 0.0, "whyHeld": ""}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_trade(contract=None, order=None, status=None):
    t = SimpleNamespace(
        contract=contract or _make_contract(),
        order=order or _make_order(),
        orderStatus=status or _make_order_status(),
        fills=[],
    )
    return t


def _inject_fake_ib_async(mock_ib_class=None):
    fake = types.ModuleType("ib_async")
    fake.IB = mock_ib_class or MagicMock
    fake.Order = object
    sys.modules["ib_async"] = fake
    if "tools.cancel_order" in sys.modules:
        del sys.modules["tools.cancel_order"]
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    import tools.cancel_order as mod
    return mod, fake


class TestArgParsing:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_order_id_required(self):
        with patch("sys.argv", ["cancel_order.py"]):
            with pytest.raises(SystemExit):
                self.mod.parse_args()

    def test_order_id_parsed(self):
        with patch("sys.argv", ["cancel_order.py", "--order-id", "12345"]):
            args = self.mod.parse_args()
        assert args.order_id == 12345

    def test_defaults_to_paper(self):
        with patch("sys.argv", ["cancel_order.py", "--order-id", "1"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_PAPER

    def test_live_flag(self):
        with patch("sys.argv", ["cancel_order.py", "--order-id", "1", "--live"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_LIVE

    def test_custom_host(self):
        with patch("sys.argv", ["cancel_order.py", "--order-id", "1", "--host", "10.0.0.1"]):
            args = self.mod.parse_args()
        assert args.host == "10.0.0.1"


class TestCancelOrder:
    def setup_method(self):
        self.mock_ib_class = MagicMock()
        self.mod, _ = _inject_fake_ib_async(self.mock_ib_class)

    def _setup_mock_ib(self, open_trades=None):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.reqOpenOrdersAsync = AsyncMock(return_value=open_trades or [])
        mock_instance.cancelOrder = MagicMock()
        mock_instance.disconnect = MagicMock()
        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    def test_order_not_found_returns_not_found(self):
        self._setup_mock_ib(open_trades=[])
        result = asyncio.run(self.mod.cancel_order("127.0.0.1", 7497, 1005, order_id=999))
        assert result["status"] == "not_found"
        assert result["order_id"] == 999

    def test_cancel_sent_when_found(self):
        trade = _make_trade(order=_make_order(orderId=42))
        mock_ib = self._setup_mock_ib(open_trades=[trade])
        result = asyncio.run(self.mod.cancel_order("127.0.0.1", 7497, 1005, order_id=42))
        assert result["status"] == "cancel_sent"
        assert result["order_id"] == 42
        mock_ib.cancelOrder.assert_called_once()

    def test_cancel_result_contains_symbol(self):
        trade = _make_trade(contract=_make_contract(symbol="SPY"), order=_make_order(orderId=77))
        self._setup_mock_ib(open_trades=[trade])
        result = asyncio.run(self.mod.cancel_order("127.0.0.1", 7497, 1005, order_id=77))
        assert result["symbol"] == "SPY"

    def test_cancel_result_json_serialisable(self):
        trade = _make_trade(order=_make_order(orderId=42))
        self._setup_mock_ib(open_trades=[trade])
        result = asyncio.run(self.mod.cancel_order("127.0.0.1", 7497, 1005, order_id=42))
        json.dumps(result)

    def test_not_readonly_connection(self):
        mock_ib = self._setup_mock_ib()
        asyncio.run(self.mod.cancel_order("127.0.0.1", 7497, 1005, order_id=1))
        mock_ib.connectAsync.assert_called_once_with(
            "127.0.0.1", 7497, clientId=1005, readonly=False
        )

    def test_disconnects_after_success(self):
        trade = _make_trade(order=_make_order(orderId=42))
        mock_ib = self._setup_mock_ib(open_trades=[trade])
        asyncio.run(self.mod.cancel_order("127.0.0.1", 7497, 1005, order_id=42))
        mock_ib.disconnect.assert_called_once()

    def test_disconnects_when_not_found(self):
        mock_ib = self._setup_mock_ib(open_trades=[])
        asyncio.run(self.mod.cancel_order("127.0.0.1", 7497, 1005, order_id=999))
        mock_ib.disconnect.assert_called_once()

    def test_raises_connection_error_on_timeout(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=asyncio.TimeoutError())
        with pytest.raises(ConnectionError, match="Timed out"):
            asyncio.run(self.mod.cancel_order("127.0.0.1", 7497, 1005, order_id=1))

    def test_raises_connection_error_on_refused(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        with pytest.raises(ConnectionError, match="Cannot reach TWS"):
            asyncio.run(self.mod.cancel_order("127.0.0.1", 7497, 1005, order_id=1))

    def test_result_has_timestamp(self):
        trade = _make_trade(order=_make_order(orderId=42))
        self._setup_mock_ib(open_trades=[trade])
        result = asyncio.run(self.mod.cancel_order("127.0.0.1", 7497, 1005, order_id=42))
        assert result["timestamp"].endswith("Z")


class TestMain:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_connection_error_exits_1(self, capsys):
        async def raise_err(host, port, client_id, order_id):
            raise ConnectionError("Cannot reach TWS")

        with patch("sys.argv", ["cancel_order.py", "--order-id", "1"]):
            with patch.object(self.mod, "cancel_order", raise_err):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()

        assert exc.value.code == 1
        err = json.loads(capsys.readouterr().err)
        assert err["status"] == "tws_unavailable"
        assert "try again later" in err["hint"]

    def test_not_found_exits_1(self, capsys):
        async def return_not_found(host, port, client_id, order_id):
            return {"status": "not_found", "order_id": order_id, "message": "x"}

        with patch("sys.argv", ["cancel_order.py", "--order-id", "999"]):
            with patch.object(self.mod, "cancel_order", return_not_found):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()

        assert exc.value.code == 1

    def test_success_prints_json_to_stdout(self, capsys):
        async def return_ok(host, port, client_id, order_id):
            return {
                "timestamp": "2025-01-01T00:00:00Z",
                "status": "cancel_sent",
                "order_id": order_id,
                "symbol": "AAPL",
                "sec_type": "STK",
                "currency": "USD",
                "action": "BUY",
                "order_type": "LMT",
                "quantity": 10.0,
                "perm_id": 888,
                "final_order_status": "Cancelled",
            }

        with patch("sys.argv", ["cancel_order.py", "--order-id", "42"]):
            with patch.object(self.mod, "cancel_order", return_ok):
                self.mod.main()

        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["status"] == "cancel_sent"
        assert data["order_id"] == 42


class TestConnectionIdRegister:
    def test_cancel_order_registered_as_not_readonly(self):
        import pathlib, json
        reg = pathlib.Path(__file__).parent.parent / "tools" / "connection_ids.json"
        data = json.loads(reg.read_text())
        assert "1005" in data["ids"]
        assert data["ids"]["1005"]["tool"] == "cancel_order.py"
        assert data["ids"]["1005"]["read_only"] is False
