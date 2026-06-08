"""Tests for get_order_status.py — lifecycle states, fill details, log entries."""

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
        "lastTradeDateOrContractMonth": None, "strike": None, "right": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_order(**kwargs):
    defaults = {
        "orderId": 42, "permId": 9001, "clientId": 1010,
        "action": "BUY", "orderType": "LMT", "totalQuantity": 100.0,
        "lmtPrice": 175.00, "tif": "DAY",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_order_status(**kwargs):
    defaults = {
        "status": "Submitted", "filled": 0.0, "remaining": 100.0,
        "avgFillPrice": 0.0, "whyHeld": "",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_execution(**kwargs):
    defaults = {
        "execId": "exec001", "time": "20260101 10:00:00 US/Eastern",
        "shares": 50.0, "price": 175.50, "cumQty": 50.0, "avgPrice": 175.50,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_commission_report(**kwargs):
    defaults = {"commission": 1.25, "currency": "USD"}
    defaults.update(kwargs)
    return SimpleNamespace(**kwargs if kwargs else defaults)


def _make_fill(execution=None, commission_report=None):
    fill = SimpleNamespace(execution=execution or _make_execution())
    if commission_report is not None:
        fill.commissionReport = commission_report
    return fill


def _make_log_entry(**kwargs):
    defaults = {"time": "2026-01-01 10:00:00", "status": "Submitted", "message": ""}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_trade(contract=None, order=None, status=None, fills=None, log=None):
    return SimpleNamespace(
        contract=contract or _make_contract(),
        order=order or _make_order(),
        orderStatus=status or _make_order_status(),
        fills=fills or [],
        log=log or [],
    )


def _inject_fake_ib_async(mock_ib_class=None):
    fake = types.ModuleType("ib_async")
    fake.IB = mock_ib_class or MagicMock
    sys.modules["ib_async"] = fake
    if "tools.get_order_status" in sys.modules:
        del sys.modules["tools.get_order_status"]
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    import tools.get_order_status as mod
    return mod, fake


class TestIbStatusMapping:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_presubmitted_maps_to_submitted(self):
        assert self.mod._map_ib_status("PreSubmitted") == "submitted"

    def test_pending_submit_maps_to_submitted(self):
        assert self.mod._map_ib_status("PendingSubmit") == "submitted"

    def test_api_pending_maps_to_submitted(self):
        assert self.mod._map_ib_status("ApiPending") == "submitted"

    def test_submitted_maps_to_accepted(self):
        assert self.mod._map_ib_status("Submitted") == "accepted"

    def test_partially_filled_maps_to_partial_fill(self):
        assert self.mod._map_ib_status("PartiallyFilled") == "partial_fill"

    def test_filled_maps_to_filled(self):
        assert self.mod._map_ib_status("Filled") == "filled"

    def test_cancelled_maps_to_canceled(self):
        assert self.mod._map_ib_status("Cancelled") == "canceled"

    def test_api_cancelled_maps_to_canceled(self):
        assert self.mod._map_ib_status("ApiCancelled") == "canceled"

    def test_pending_cancel_maps_to_canceled(self):
        assert self.mod._map_ib_status("PendingCancel") == "canceled"

    def test_expired_maps_to_expired(self):
        assert self.mod._map_ib_status("Expired") == "expired"

    def test_inactive_maps_to_rejected(self):
        assert self.mod._map_ib_status("Inactive") == "rejected"

    def test_unknown_status_maps_to_unknown(self):
        assert self.mod._map_ib_status("SomethingNew") == "unknown"


class TestTradeToStatusDict:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_submitted_state(self):
        status = _make_order_status(status="PreSubmitted", filled=0.0, remaining=100.0)
        trade = _make_trade(status=status)
        result = self.mod._trade_to_status_dict(trade, "open_orders")
        assert result["lifecycle_state"] == "submitted"
        assert result["ib_status"] == "PreSubmitted"

    def test_accepted_state(self):
        status = _make_order_status(status="Submitted", filled=0.0, remaining=100.0)
        trade = _make_trade(status=status)
        result = self.mod._trade_to_status_dict(trade, "open_orders")
        assert result["lifecycle_state"] == "accepted"

    def test_partial_fill_state(self):
        status = _make_order_status(
            status="PartiallyFilled", filled=50.0, remaining=50.0, avgFillPrice=175.50
        )
        fill = _make_fill(_make_execution(shares=50.0, price=175.50, cumQty=50.0))
        trade = _make_trade(status=status, fills=[fill])
        result = self.mod._trade_to_status_dict(trade, "open_orders")
        assert result["lifecycle_state"] == "partial_fill"
        assert result["filled_qty"] == 50.0
        assert result["remaining_qty"] == 50.0
        assert result["avg_fill_price"] == pytest.approx(175.50)
        assert len(result["fills"]) == 1
        assert result["fills"][0]["shares"] == 50.0
        assert result["fills"][0]["price"] == pytest.approx(175.50)

    def test_filled_state(self):
        status = _make_order_status(
            status="Filled", filled=100.0, remaining=0.0, avgFillPrice=175.25
        )
        fill = _make_fill(_make_execution(shares=100.0, cumQty=100.0, avgPrice=175.25))
        trade = _make_trade(status=status, fills=[fill])
        result = self.mod._trade_to_status_dict(trade, "completed_orders")
        assert result["lifecycle_state"] == "filled"
        assert result["filled_qty"] == 100.0
        assert result["remaining_qty"] == 0.0

    def test_canceled_state(self):
        status = _make_order_status(status="Cancelled", filled=0.0, remaining=100.0)
        trade = _make_trade(status=status)
        result = self.mod._trade_to_status_dict(trade, "completed_orders")
        assert result["lifecycle_state"] == "canceled"

    def test_expired_state(self):
        status = _make_order_status(status="Expired", filled=0.0, remaining=100.0)
        trade = _make_trade(status=status)
        result = self.mod._trade_to_status_dict(trade, "completed_orders")
        assert result["lifecycle_state"] == "expired"

    def test_rejected_state(self):
        status = _make_order_status(status="Inactive", filled=0.0, remaining=100.0)
        trade = _make_trade(status=status)
        result = self.mod._trade_to_status_dict(trade, "open_orders")
        assert result["lifecycle_state"] == "rejected"

    def test_client_and_broker_order_id_mapping(self):
        order = _make_order(orderId=42, permId=9001)
        trade = _make_trade(order=order)
        result = self.mod._trade_to_status_dict(trade, "open_orders")
        assert result["client_order_id"] == 42
        assert result["broker_order_id"] == 9001

    def test_commission_included_in_fill(self):
        cr = _make_commission_report(commission=1.25, currency="USD")
        fill = _make_fill(_make_execution(shares=100.0), commission_report=cr)
        status = _make_order_status(status="Filled", filled=100.0, remaining=0.0, avgFillPrice=175.0)
        trade = _make_trade(status=status, fills=[fill])
        result = self.mod._trade_to_status_dict(trade, "completed_orders")
        assert result["total_commission"] == pytest.approx(1.25)
        assert result["commission_currency"] == "USD"
        assert result["fills"][0]["commission"] == pytest.approx(1.25)

    def test_no_commission_when_unset(self):
        cr = _make_commission_report(commission=IB_UNSET, currency="USD")
        fill = _make_fill(_make_execution(), commission_report=cr)
        trade = _make_trade(fills=[fill])
        result = self.mod._trade_to_status_dict(trade, "open_orders")
        assert result["total_commission"] is None
        assert result["fills"][0]["commission"] is None

    def test_log_entries_include_lifecycle_state(self):
        log = [
            _make_log_entry(time="2026-01-01 09:00:00", status="PreSubmitted", message=""),
            _make_log_entry(time="2026-01-01 09:01:00", status="Submitted", message=""),
        ]
        trade = _make_trade(log=log)
        result = self.mod._trade_to_status_dict(trade, "open_orders")
        assert len(result["log"]) == 2
        assert result["log"][0]["ib_status"] == "PreSubmitted"
        assert result["log"][0]["lifecycle_state"] == "submitted"
        assert result["log"][1]["lifecycle_state"] == "accepted"

    def test_unset_lmt_price_becomes_none(self):
        order = _make_order(lmtPrice=IB_UNSET)
        trade = _make_trade(order=order)
        result = self.mod._trade_to_status_dict(trade, "open_orders")
        assert result["lmt_price"] is None

    def test_result_is_json_serialisable(self):
        status = _make_order_status(status="Filled", filled=100.0, remaining=0.0, avgFillPrice=175.0)
        fill = _make_fill(_make_execution())
        trade = _make_trade(status=status, fills=[fill])
        result = self.mod._trade_to_status_dict(trade, "completed_orders")
        json.dumps(result)

    def test_source_field_preserved(self):
        trade = _make_trade()
        result = self.mod._trade_to_status_dict(trade, "completed_orders")
        assert result["source"] == "completed_orders"

    def test_timestamp_ends_with_z(self):
        trade = _make_trade()
        result = self.mod._trade_to_status_dict(trade, "open_orders")
        assert result["timestamp"].endswith("Z")

    def test_multiple_fills_summed_for_commission(self):
        cr1 = _make_commission_report(commission=1.0, currency="USD")
        cr2 = _make_commission_report(commission=1.5, currency="USD")
        fill1 = _make_fill(_make_execution(shares=50.0), commission_report=cr1)
        fill2 = _make_fill(_make_execution(shares=50.0, cumQty=100.0), commission_report=cr2)
        status = _make_order_status(status="Filled", filled=100.0, remaining=0.0, avgFillPrice=175.0)
        trade = _make_trade(status=status, fills=[fill1, fill2])
        result = self.mod._trade_to_status_dict(trade, "completed_orders")
        assert result["total_commission"] == pytest.approx(2.5)


class TestGetOrderStatus:
    def setup_method(self):
        self.mock_ib_class = MagicMock()
        self.mod, _ = _inject_fake_ib_async(self.mock_ib_class)

    def _setup_mock_ib(self, open_trades=None, completed_trades=None):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.reqAllOpenOrdersAsync = AsyncMock(return_value=open_trades or [])
        mock_instance.reqCompletedOrdersAsync = AsyncMock(return_value=completed_trades or [])
        mock_instance.disconnect = MagicMock()
        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    def test_found_in_open_orders(self):
        trade = _make_trade(order=_make_order(orderId=42))
        self._setup_mock_ib(open_trades=[trade])
        result = asyncio.run(self.mod.get_order_status("127.0.0.1", 7497, 1010, 42))
        assert result["client_order_id"] == 42
        assert result["source"] == "open_orders"

    def test_found_in_completed_orders_when_not_open(self):
        completed = _make_trade(
            order=_make_order(orderId=99),
            status=_make_order_status(status="Filled", filled=100.0, remaining=0.0, avgFillPrice=175.0),
        )
        self._setup_mock_ib(open_trades=[], completed_trades=[completed])
        result = asyncio.run(self.mod.get_order_status("127.0.0.1", 7497, 1010, 99))
        assert result["client_order_id"] == 99
        assert result["source"] == "completed_orders"
        assert result["lifecycle_state"] == "filled"

    def test_not_found_returns_not_found(self):
        self._setup_mock_ib(open_trades=[], completed_trades=[])
        result = asyncio.run(self.mod.get_order_status("127.0.0.1", 7497, 1010, 999))
        assert result["status"] == "not_found"
        assert result["order_id"] == 999

    def test_not_found_has_not_retained_lifecycle_state(self):
        self._setup_mock_ib(open_trades=[], completed_trades=[])
        result = asyncio.run(self.mod.get_order_status("127.0.0.1", 7497, 1010, 999))
        assert result["lifecycle_state"] == "not_retained"

    def test_not_found_has_retention_note(self):
        self._setup_mock_ib(open_trades=[], completed_trades=[])
        result = asyncio.run(self.mod.get_order_status("127.0.0.1", 7497, 1010, 999))
        assert "retention_note" in result
        assert len(result["retention_note"]) > 0

    def test_readonly_connection(self):
        mock_ib = self._setup_mock_ib(open_trades=[_make_trade(order=_make_order(orderId=1))])
        asyncio.run(self.mod.get_order_status("127.0.0.1", 7497, 1010, 1))
        mock_ib.connectAsync.assert_called_once_with(
            "127.0.0.1", 7497, clientId=1010, readonly=True
        )

    def test_disconnects_after_found(self):
        trade = _make_trade(order=_make_order(orderId=42))
        mock_ib = self._setup_mock_ib(open_trades=[trade])
        asyncio.run(self.mod.get_order_status("127.0.0.1", 7497, 1010, 42))
        mock_ib.disconnect.assert_called_once()

    def test_disconnects_when_not_found(self):
        mock_ib = self._setup_mock_ib(open_trades=[], completed_trades=[])
        asyncio.run(self.mod.get_order_status("127.0.0.1", 7497, 1010, 999))
        mock_ib.disconnect.assert_called_once()

    def test_cancel_already_filled_returns_not_found_in_open(self):
        # A filled order is no longer in open orders — cancel_order would return not_found
        filled = _make_trade(
            order=_make_order(orderId=55),
            status=_make_order_status(status="Filled", filled=100.0, remaining=0.0, avgFillPrice=175.0),
        )
        # Not in open, only in completed
        mock_ib = self._setup_mock_ib(open_trades=[], completed_trades=[filled])
        result = asyncio.run(self.mod.get_order_status("127.0.0.1", 7497, 1010, 55))
        # Found in completed orders as filled — not in open orders (cancel_order would reject it)
        assert result["lifecycle_state"] == "filled"
        assert result["source"] == "completed_orders"

    def test_raises_connection_error_on_timeout(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=asyncio.TimeoutError())
        with pytest.raises(ConnectionError, match="Timed out"):
            asyncio.run(self.mod.get_order_status("127.0.0.1", 7497, 1010, 1))

    def test_raises_connection_error_on_refused(self):
        mock_ib = self._setup_mock_ib()
        mock_ib.connectAsync = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        with pytest.raises(ConnectionError, match="Cannot reach TWS"):
            asyncio.run(self.mod.get_order_status("127.0.0.1", 7497, 1010, 1))

    def test_completed_orders_error_returns_lookup_error(self):
        # If reqCompletedOrdersAsync throws, we surface the lookup failure explicitly.
        mock_ib = self._setup_mock_ib()
        mock_ib.reqCompletedOrdersAsync = AsyncMock(side_effect=Exception("timeout"))
        result = asyncio.run(self.mod.get_order_status("127.0.0.1", 7497, 1010, 999))
        assert result["status"] == "lookup_error"
        assert result["lifecycle_state"] == "unknown"
        assert result["completed_orders_error"] == "timeout"


class TestArgParsing:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_order_id_required(self):
        with patch("sys.argv", ["get_order_status.py"]):
            with pytest.raises(SystemExit):
                self.mod.parse_args()

    def test_order_id_parsed(self):
        with patch("sys.argv", ["get_order_status.py", "--order-id", "12345"]):
            args = self.mod.parse_args()
        assert args.order_id == 12345

    def test_defaults_to_paper(self):
        with patch("sys.argv", ["get_order_status.py", "--order-id", "1"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_PAPER

    def test_live_flag(self):
        with patch("sys.argv", ["get_order_status.py", "--order-id", "1", "--live"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_LIVE


class TestMain:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_connection_error_exits_1(self, capsys):
        async def raise_err(host, port, client_id, order_id):
            raise ConnectionError("Cannot reach TWS")

        with patch("sys.argv", ["get_order_status.py", "--order-id", "1"]):
            with patch.object(self.mod, "get_order_status", raise_err):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()

        assert exc.value.code == 1
        err = json.loads(capsys.readouterr().err)
        assert err["status"] == "tws_unavailable"

    def test_not_found_exits_2(self, capsys):
        async def return_not_found(host, port, client_id, order_id):
            return {
                "status": "not_found",
                "lifecycle_state": "not_retained",
                "order_id": order_id,
                "message": "not found",
                "retention_note": "IB does not guarantee retention",
            }

        with patch("sys.argv", ["get_order_status.py", "--order-id", "999"]):
            with patch.object(self.mod, "get_order_status", return_not_found):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()

        assert exc.value.code == 2

    def test_lookup_error_exits_3(self, capsys):
        async def return_lookup_error(host, port, client_id, order_id):
            return {
                "status": "lookup_error",
                "lifecycle_state": "unknown",
                "order_id": order_id,
                "message": "lookup failed",
                "completed_orders_error": "timeout",
            }

        with patch("sys.argv", ["get_order_status.py", "--order-id", "999"]):
            with patch.object(self.mod, "get_order_status", return_lookup_error):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()

        assert exc.value.code == 3

    def test_not_found_prints_to_stdout(self, capsys):
        async def return_not_found(host, port, client_id, order_id):
            return {
                "status": "not_found",
                "lifecycle_state": "not_retained",
                "order_id": order_id,
                "message": "not found",
                "retention_note": "IB does not guarantee retention",
            }

        with patch("sys.argv", ["get_order_status.py", "--order-id", "999"]):
            with patch.object(self.mod, "get_order_status", return_not_found):
                with pytest.raises(SystemExit):
                    self.mod.main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "not_found"
        assert data["lifecycle_state"] == "not_retained"
        assert "retention_note" in data
        assert captured.err == ""

    def test_success_prints_json_to_stdout(self, capsys):
        async def return_ok(host, port, client_id, order_id):
            return {
                "timestamp": "2026-01-01T00:00:00Z",
                "source": "open_orders",
                "client_order_id": 42,
                "broker_order_id": 9001,
                "client_id": 1010,
                "symbol": "AAPL",
                "sec_type": "STK",
                "currency": "USD",
                "exchange": "SMART",
                "action": "BUY",
                "order_type": "LMT",
                "total_quantity": 100.0,
                "lmt_price": 175.0,
                "tif": "DAY",
                "lifecycle_state": "accepted",
                "ib_status": "Submitted",
                "filled_qty": 0.0,
                "remaining_qty": 100.0,
                "avg_fill_price": None,
                "total_commission": None,
                "commission_currency": None,
                "why_held": None,
                "fills": [],
                "log": [],
            }

        with patch("sys.argv", ["get_order_status.py", "--order-id", "42"]):
            with patch.object(self.mod, "get_order_status", return_ok):
                self.mod.main()

        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["lifecycle_state"] == "accepted"
        assert data["client_order_id"] == 42


class TestConnectionIdRegister:
    def test_get_order_status_registered(self):
        import pathlib
        reg = pathlib.Path(__file__).parent.parent / "tools" / "connection_ids.json"
        data = json.loads(reg.read_text())
        assert "1011" in data["ids"]
        assert data["ids"]["1011"]["tool"] == "get_order_status.py"
        assert data["ids"]["1011"]["read_only"] is True
