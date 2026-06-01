#!/usr/bin/env python3
"""
get_order_status.py — Poll the lifecycle status of a specific order from TWS.

Returns structured order lifecycle information including current state, fill
details, commissions, a log of every state change with timestamps, and the
mapping between client_order_id and broker_order_id (perm_id).

Lifecycle states (lifecycle_state field):
  submitted    — PreSubmitted / PendingSubmit / ApiPending
  accepted     — Submitted (acknowledged by the exchange)
  partial_fill — PartiallyFilled (some quantity executed, remainder open)
  filled       — Filled (fully executed)
  canceled     — Cancelled / ApiCancelled / PendingCancel
  expired      — Expired (TIF elapsed without fill)
  rejected     — Inactive (IB/exchange rejected the order)

Searches open orders first, then completed orders (fills and recent cancels).

Usage:
    python get_order_status.py --order-id ORDER_ID [options]

Connection ID: 1011 (see tools/connection_ids.json)
"""

import argparse
import asyncio
import contextlib
import io
import json
import sys
from datetime import datetime, timezone

try:
    from ib_async import IB
except ImportError:
    print(
        json.dumps({
            "error": "ib_async not installed. Run: pip install ib_async",
            "status": "dependency_missing",
        }),
        file=sys.stderr,
    )
    sys.exit(1)

DEFAULT_HOST = "192.168.2.187"
PORT_PAPER = 7497
PORT_LIVE = 7496
CLIENT_ID = 1011
CONNECT_TIMEOUT = 10
COMPLETED_ORDERS_TIMEOUT = 5.0
IB_UNSET_PRICE = 1.7976931348623157e308

IB_STATUS_MAP = {
    "PreSubmitted": "submitted",
    "PendingSubmit": "submitted",
    "ApiPending": "submitted",
    "Submitted": "accepted",
    "PartiallyFilled": "partial_fill",
    "Filled": "filled",
    "Cancelled": "canceled",
    "ApiCancelled": "canceled",
    "PendingCancel": "canceled",
    "Expired": "expired",
    "Inactive": "rejected",
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _map_ib_status(ib_status: str) -> str:
    return IB_STATUS_MAP.get(ib_status, "unknown")


def _trade_to_status_dict(trade, source: str) -> dict:
    contract = trade.contract
    order = trade.order
    status = trade.orderStatus

    ib_status = status.status
    lifecycle_state = _map_ib_status(ib_status)

    fills = []
    total_commission = 0.0
    commission_currency = contract.currency

    for fill in trade.fills:
        exec_info = {
            "exec_id": fill.execution.execId,
            "time": fill.execution.time,
            "shares": fill.execution.shares,
            "price": fill.execution.price,
            "cum_qty": fill.execution.cumQty,
            "avg_price": fill.execution.avgPrice,
            "commission": None,
        }
        cr = getattr(fill, "commissionReport", None)
        if cr:
            commission = getattr(cr, "commission", None)
            if commission is not None and commission != IB_UNSET_PRICE:
                exec_info["commission"] = commission
                total_commission += commission
                commission_currency = getattr(cr, "currency", commission_currency) or commission_currency
        fills.append(exec_info)

    log_entries = []
    for entry in getattr(trade, "log", []):
        entry_status = getattr(entry, "status", None)
        log_entries.append({
            "time": str(entry.time) if getattr(entry, "time", None) else None,
            "ib_status": entry_status,
            "lifecycle_state": _map_ib_status(entry_status) if entry_status else None,
            "message": getattr(entry, "message", None),
        })

    return {
        "timestamp": utc_timestamp(),
        "source": source,
        "client_order_id": order.orderId,
        "broker_order_id": order.permId,
        "client_id": order.clientId,
        "symbol": contract.symbol,
        "sec_type": contract.secType,
        "expiry": getattr(contract, "lastTradeDateOrContractMonth", None),
        "strike": getattr(contract, "strike", None),
        "right": getattr(contract, "right", None),
        "currency": contract.currency,
        "exchange": contract.exchange,
        "action": order.action,
        "order_type": order.orderType,
        "total_quantity": order.totalQuantity,
        "lmt_price": order.lmtPrice if order.lmtPrice != IB_UNSET_PRICE else None,
        "tif": order.tif,
        "lifecycle_state": lifecycle_state,
        "ib_status": ib_status,
        "filled_qty": status.filled,
        "remaining_qty": status.remaining,
        "avg_fill_price": status.avgFillPrice if status.avgFillPrice else None,
        "total_commission": total_commission if total_commission > 0 else None,
        "commission_currency": commission_currency if total_commission > 0 else None,
        "why_held": status.whyHeld or None,
        "fills": fills,
        "log": log_entries,
    }


async def get_order_status(host: str, port: int, client_id: int, order_id: int) -> dict:
    ib = IB()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            await asyncio.wait_for(
                ib.connectAsync(host, port, clientId=client_id, readonly=True),
                timeout=CONNECT_TIMEOUT,
            )
    except asyncio.TimeoutError:
        raise ConnectionError(f"Timed out connecting to TWS at {host}:{port}")
    except Exception as exc:
        raise ConnectionError(f"Cannot reach TWS at {host}:{port} — {exc}") from exc

    try:
        with contextlib.redirect_stdout(sys.stderr):
            open_trades = await ib.reqAllOpenOrdersAsync()

        for trade in open_trades:
            if trade.order.orderId == order_id:
                return _trade_to_status_dict(trade, "open_orders")

        # Not in open orders — check completed orders (filled, recently cancelled)
        completed_trades = []
        try:
            with contextlib.redirect_stdout(sys.stderr):
                completed_trades = await asyncio.wait_for(
                    ib.reqCompletedOrdersAsync(apiOnly=False),
                    timeout=COMPLETED_ORDERS_TIMEOUT,
                )
        except Exception:
            pass

        for trade in completed_trades:
            if trade.order.orderId == order_id:
                return _trade_to_status_dict(trade, "completed_orders")

        return {
            "timestamp": utc_timestamp(),
            "status": "not_found",
            "order_id": order_id,
            "message": (
                f"No order found with order_id {order_id} in open or completed orders"
            ),
        }
    finally:
        ib.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Poll lifecycle status of a specific order from Interactive Brokers TWS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python get_order_status.py --order-id 12345
  python get_order_status.py --order-id 12345 --live
        """,
    )
    parser.add_argument("--order-id", required=True, type=int, help="TWS order ID to look up")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"TWS hostname or IP (default: {DEFAULT_HOST})")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--paper", dest="port", action="store_const", const=PORT_PAPER,
                      help=f"Paper trading (port {PORT_PAPER}, default)")
    mode.add_argument("--live", dest="port", action="store_const", const=PORT_LIVE,
                      help=f"Live trading (port {PORT_LIVE})")
    mode.add_argument("--port", dest="port", type=int, help="Custom port number")
    parser.set_defaults(port=PORT_PAPER)

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        result = asyncio.run(get_order_status(args.host, args.port, CLIENT_ID, args.order_id))
    except ConnectionError as exc:
        print(
            json.dumps({
                "error": str(exc),
                "status": "tws_unavailable",
                "hint": "The TWS API is temporarily not available. Please try again later.",
                "timestamp": utc_timestamp(),
            }),
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as exc:
        print(
            json.dumps({
                "error": f"Unexpected error: {exc}",
                "status": "error",
                "timestamp": utc_timestamp(),
            }),
            file=sys.stderr,
        )
        sys.exit(1)

    if result.get("status") == "not_found":
        print(json.dumps(result, indent=2), file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
