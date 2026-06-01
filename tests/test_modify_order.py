"""Tests for modify_order.py — order modification, cancel/replace, reject modify."""

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
        "orderId": 42, "permId": 999, "clientId": 1009,
        "action": "BUY", "orderType": "LMT", "totalQuantity": 100.0,
        "lmtPrice": 175.00, "tif": "DAY", "account": "DU123456",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_order_status(**kwargs):
    defaults = {"status": "Submitted", "filled": 0.0, "remaining": 100.0,
                "avgFillPrice": 0.0, "whyHeld": ""}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_trade(contract=None, order=None, status=None):
    return SimpleNamespace(
        contract=contract or _make_contract(),
        order=order or _make_order(),
        orderStatus=status or _make_order_status(),
        fills=[],
    )


def _make_account_value(account, tag, value):
    return SimpleNamespace(account=account, tag=tag, value=value)


def _inject_fake_ib_async(mock_ib_class=None):
    fake = types.ModuleType("ib_async")
    fake.IB = mock_ib_class or MagicMock

    class LimitOrder:
        def __init__(self, action, qty, price, tif="DAY"):
            self.action = action
            self.totalQuantity = qty
            self.lmtPrice = price
            self.orderType = "LMT"
            self.tif = tif
            self.orderId = 0
            self.permId = 0

    class MarketOrder:
        def __init__(self, action, qty, tif="DAY"):
            self.action = action
            self.totalQuantity = qty
            self.lmtPrice = IB_UNSET
            self.orderType = "MKT"
            self.tif = tif
            self.orderId = 0
            self.permId = 0

    fake.LimitOrder = LimitOrder
    fake.MarketOrder = MarketOrder

    sys.modules["ib_async"] = fake
    if "tools.modify_order" in sys.modules:
        del sys.modules["tools.modify_order"]
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    import tools.modify_order as mod
    return mod, fake


def _make_args(**kwargs):
    defaults = {
        "host": "127.0.0.1",
        "port": 7497,
        "order_id": 42,
        "quantity": None,
        "limit_price": None,
        "order_type": None,
        "tif": None,
        "cancel_replace": False,
        "max_notional": 100_000.0,
        "max_pct_nlv": 10.0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestArgParsing:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_order_id_required(self):
        with patch("sys.argv", ["modify_order.py"]):
            with pytest.raises(SystemExit):
                self.mod.parse_args()

    def test_order_id_parsed(self):
        with patch("sys.argv", ["modify_order.py", "--order-id", "12345"]):
            args = self.mod.parse_args()
        assert args.order_id == 12345

    def test_defaults_to_paper(self):
        with patch("sys.argv", ["modify_order.py", "--order-id", "1"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_PAPER

    def test_live_flag(self):
        with patch("sys.argv", ["modify_order.py", "--order-id", "1", "--live"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_LIVE

    def test_limit_price_parsed(self):
        with patch("sys.argv", ["modify_order.py", "--order-id", "1", "--limit-price", "180.50"]):
            args = self.mod.parse_args()
        assert args.limit_price == pytest.approx(180.50)

    def test_quantity_parsed(self):
        with patch("sys.argv", ["modify_order.py", "--order-id", "1", "--quantity", "50"]):
            args = self.mod.parse_args()
        assert args.quantity == 50.0

    def test_cancel_replace_flag(self):
        with patch("sys.argv", ["modify_order.py", "--order-id", "1", "--cancel-replace"]):
            args = self.mod.parse_args()
        assert args.cancel_replace is True

    def test_tif_parsed(self):
        with patch("sys.argv", ["modify_order.py", "--order-id", "1", "--tif", "GTC"]):
            args = self.mod.parse_args()
        assert args.tif == "GTC"


class TestModifyOrder:
    def setup_method(self):
        self.mock_ib_class = MagicMock()
        self.mod, _ = _inject_fake_ib_async(self.mock_ib_class)

    def _setup_mock_ib(self, open_trades=None, nlv=500_000.0, excess=200_000.0):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.reqAllOpenOrdersAsync = AsyncMock(return_value=open_trades or [])
        mock_instance.placeOrder = MagicMock(return_value=_make_trade())
        mock_instance.cancelOrder = MagicMock()
        mock_instance.disconnect = MagicMock()

        summary = [
            _make_account_value("DU", "NetLiquidation", str(nlv)),
            _make_account_value("DU", "ExcessLiquidity", str(excess)),
        ]
        mock_instance.accountSummaryAsync = AsyncMock(return_value=summary)
        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    # --- order not found ---

    def test_order_not_found_returns_not_found(self):
        self._setup_mock_ib(open_trades=[])
        args = _make_args(order_id=999)
        result = asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        assert result["status"] == "not_found"
        assert result["order_id"] == 999

    def test_not_found_has_timestamp(self):
        self._setup_mock_ib(open_trades=[])
        args = _make_args(order_id=999)
        result = asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        assert result["timestamp"].endswith("Z")

    # --- native modify ---

    def test_native_modify_with_new_price(self):
        trade = _make_trade(order=_make_order(orderId=42))
        mock_ib = self._setup_mock_ib(open_trades=[trade])
        args = _make_args(order_id=42, limit_price=180.0)
        result = asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        assert result["status"] == "modified"
        assert result["modification_mode"] == "native_modify"
        mock_ib.placeOrder.assert_called_once()

    def test_native_modify_with_new_quantity(self):
        trade = _make_trade(order=_make_order(orderId=42))
        mock_ib = self._setup_mock_ib(open_trades=[trade])
        args = _make_args(order_id=42, quantity=50.0)
        result = asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        assert result["status"] == "modified"
        mock_ib.placeOrder.assert_called_once()

    def test_native_modify_result_json_serialisable(self):
        trade = _make_trade(order=_make_order(orderId=42))
        self._setup_mock_ib(open_trades=[trade])
        args = _make_args(order_id=42, limit_price=180.0)
        result = asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        json.dumps(result)

    def test_native_modify_has_timestamp(self):
        trade = _make_trade(order=_make_order(orderId=42))
        self._setup_mock_ib(open_trades=[trade])
        args = _make_args(order_id=42, limit_price=180.0)
        result = asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        assert result["timestamp"].endswith("Z")

    # --- cancel/replace ---

    def test_cancel_replace_returns_replaced(self):
        trade = _make_trade(order=_make_order(orderId=42))
        mock_ib = self._setup_mock_ib(open_trades=[trade])
        args = _make_args(order_id=42, limit_price=180.0, cancel_replace=True)
        result = asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        assert result["status"] == "replaced"
        assert result["modification_mode"] == "cancel_replace"
        assert result["cancelled_order_id"] == 42
        mock_ib.cancelOrder.assert_called_once()
        mock_ib.placeOrder.assert_called_once()

    def test_cancel_replace_to_mkt_order(self):
        trade = _make_trade(order=_make_order(orderId=42, orderType="LMT"))
        mock_ib = self._setup_mock_ib(open_trades=[trade])
        args = _make_args(order_id=42, order_type="MKT", cancel_replace=True)
        result = asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        assert result["status"] == "replaced"
        mock_ib.cancelOrder.assert_called_once()

    # --- risk re-check ---

    def test_risk_check_runs_when_quantity_changes(self):
        trade = _make_trade(order=_make_order(orderId=42, totalQuantity=100.0))
        mock_ib = self._setup_mock_ib(open_trades=[trade])
        args = _make_args(order_id=42, quantity=200.0)
        asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        mock_ib.accountSummaryAsync.assert_called_once()

    def test_risk_check_runs_when_price_changes(self):
        trade = _make_trade(order=_make_order(orderId=42, lmtPrice=100.0))
        mock_ib = self._setup_mock_ib(open_trades=[trade])
        args = _make_args(order_id=42, limit_price=200.0)
        asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        mock_ib.accountSummaryAsync.assert_called_once()

    def test_risk_check_not_run_when_nothing_changes(self):
        trade = _make_trade(order=_make_order(orderId=42))
        mock_ib = self._setup_mock_ib(open_trades=[trade])
        args = _make_args(order_id=42)  # no fields changed
        asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        mock_ib.accountSummaryAsync.assert_not_called()

    def test_reject_modify_when_risk_check_fails(self):
        """Reject modify flow: failed risk check leaves original order untouched."""
        trade = _make_trade(order=_make_order(orderId=42, totalQuantity=100.0))
        mock_ib = self._setup_mock_ib(open_trades=[trade], nlv=50_000, excess=20_000)
        # new quantity * price = 1000 * 175 = 175000 > 100000 max_notional
        args = _make_args(order_id=42, quantity=1000.0, max_notional=100_000)
        result = asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        assert result["status"] == "risk_check_failed"
        assert result["risk_check"]["passed"] is False
        # original order must NOT be modified
        mock_ib.placeOrder.assert_not_called()
        mock_ib.cancelOrder.assert_not_called()

    def test_reject_modify_result_json_serialisable(self):
        trade = _make_trade(order=_make_order(orderId=42, totalQuantity=100.0))
        self._setup_mock_ib(open_trades=[trade], nlv=50_000, excess=20_000)
        args = _make_args(order_id=42, quantity=1000.0, max_notional=100_000)
        result = asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        json.dumps(result)

    def test_modify_passes_when_risk_check_passes(self):
        trade = _make_trade(order=_make_order(orderId=42, totalQuantity=100.0))
        mock_ib = self._setup_mock_ib(open_trades=[trade], nlv=500_000, excess=200_000)
        args = _make_args(order_id=42, quantity=110.0)  # within limits
        result = asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        assert result["status"] == "modified"
        mock_ib.placeOrder.assert_called_once()

    # --- cross-client modify ---

    def test_modify_order_placed_by_different_client(self):
        trade = _make_trade(order=_make_order(orderId=42, clientId=1004))
        mock_ib = self._setup_mock_ib(open_trades=[trade])
        args = _make_args(order_id=42, limit_price=180.0)
        result = asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        assert result["status"] == "modified"
        mock_ib.connectAsync.assert_any_call("127.0.0.1", 7497, clientId=1009, readonly=False)
        mock_ib.connectAsync.assert_any_call("127.0.0.1", 7497, clientId=1004, readonly=False)

    # --- connection lifecycle ---

    def test_disconnects_after_success(self):
        trade = _make_trade(order=_make_order(orderId=42))
        mock_ib = self._setup_mock_ib(open_trades=[trade])
        args = _make_args(order_id=42, limit_price=180.0)
        asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        mock_ib.disconnect.assert_called_once()

    def test_disconnects_when_not_found(self):
        mock_ib = self._setup_mock_ib(open_trades=[])
        args = _make_args(order_id=999)
        asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        mock_ib.disconnect.assert_called_once()

    def test_raises_connection_error_on_timeout(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=asyncio.TimeoutError())
        args = _make_args()
        with pytest.raises(ConnectionError, match="Timed out"):
            asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))

    def test_raises_connection_error_on_refused(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        args = _make_args()
        with pytest.raises(ConnectionError, match="Cannot reach TWS"):
            asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))

    # --- invalid input ---

    def test_lmt_order_without_price_returns_invalid_input(self):
        # order is already LMT, no new price given, no original lmt to fall back to
        trade = _make_trade(order=_make_order(orderId=42, orderType="MKT", lmtPrice=IB_UNSET))
        self._setup_mock_ib(open_trades=[trade])
        args = _make_args(order_id=42, order_type="LMT", limit_price=None)
        result = asyncio.run(self.mod.modify_order("127.0.0.1", 7497, 1009, args))
        assert result["status"] == "invalid_input"


class TestRiskMaterialChanged:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def _order(self, **kwargs):
        return _make_order(**kwargs)

    def test_quantity_change_is_material(self):
        order = self._order(totalQuantity=100.0)
        args = _make_args(quantity=200.0)
        assert self.mod._risk_material_changed(order, args) is True

    def test_same_quantity_is_not_material(self):
        order = self._order(totalQuantity=100.0)
        args = _make_args(quantity=100.0)
        assert self.mod._risk_material_changed(order, args) is False

    def test_no_fields_specified_is_not_material(self):
        order = self._order()
        args = _make_args()
        assert self.mod._risk_material_changed(order, args) is False

    def test_price_change_is_material(self):
        order = self._order(lmtPrice=100.0)
        args = _make_args(limit_price=200.0)
        assert self.mod._risk_material_changed(order, args) is True

    def test_tif_change_is_material(self):
        order = self._order(tif="DAY")
        args = _make_args(tif="GTC")
        assert self.mod._risk_material_changed(order, args) is True

    def test_order_type_change_is_material(self):
        order = self._order(orderType="LMT")
        args = _make_args(order_type="MKT")
        assert self.mod._risk_material_changed(order, args) is True


class TestMain:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_connection_error_exits_1(self, capsys):
        async def raise_err(host, port, client_id, args):
            raise ConnectionError("Cannot reach TWS")

        with patch("sys.argv", ["modify_order.py", "--order-id", "1"]):
            with patch.object(self.mod, "modify_order", raise_err):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()

        assert exc.value.code == 1
        err = json.loads(capsys.readouterr().err)
        assert err["status"] == "tws_unavailable"

    def test_not_found_exits_1(self, capsys):
        async def return_not_found(host, port, client_id, args):
            return {"status": "not_found", "order_id": 1, "message": "not found"}

        with patch("sys.argv", ["modify_order.py", "--order-id", "1"]):
            with patch.object(self.mod, "modify_order", return_not_found):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()

        assert exc.value.code == 1

    def test_risk_check_failed_exits_1(self, capsys):
        async def return_risk_fail(host, port, client_id, args):
            return {
                "status": "risk_check_failed",
                "order_id": 1,
                "message": "risk fail",
                "risk_check": {"passed": False, "failures": ["notional too high"]},
            }

        with patch("sys.argv", ["modify_order.py", "--order-id", "1"]):
            with patch.object(self.mod, "modify_order", return_risk_fail):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()

        assert exc.value.code == 1

    def test_success_prints_json_to_stdout(self, capsys):
        async def return_ok(host, port, client_id, args):
            return {
                "timestamp": "2025-01-01T00:00:00Z",
                "status": "modified",
                "modification_mode": "native_modify",
                "order_id": 42,
                "perm_id": 999,
                "symbol": "AAPL",
                "sec_type": "STK",
                "currency": "USD",
                "exchange": "SMART",
                "action": "BUY",
                "order_type": "LMT",
                "quantity": 100.0,
                "lmt_price": 180.0,
                "tif": "DAY",
                "order_status": "Submitted",
            }

        with patch("sys.argv", ["modify_order.py", "--order-id", "42", "--limit-price", "180.0"]):
            with patch.object(self.mod, "modify_order", return_ok):
                self.mod.main()

        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["status"] == "modified"
        assert data["order_id"] == 42


class TestConnectionIdRegister:
    def test_modify_order_registered(self):
        import pathlib
        reg = pathlib.Path(__file__).parent.parent / "tools" / "connection_ids.json"
        data = json.loads(reg.read_text())
        assert "1010" in data["ids"]
        assert data["ids"]["1010"]["tool"] == "modify_order.py"
        assert data["ids"]["1010"]["read_only"] is False
