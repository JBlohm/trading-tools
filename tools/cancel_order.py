#!/usr/bin/env python3
"""
cancel_order.py — Cancel an open order via Interactive Brokers TWS.

Looks up the order by order ID across all API clients (reqAllOpenOrders) and
sends the cancel request from the order's owning client ID, so orders placed by
any client ID (including place_order.py's client 1004) can be cancelled.
Returns the final order status as JSON to stdout.

Usage:
    python cancel_order.py --order-id ORDER_ID [--host HOST] [--port PORT] [--paper] [--live]

Connection ID: 1005 (see tools/connection_ids.json)
"""

import argparse
import asyncio
import contextlib
import io
import json
import sys
from datetime import datetime, timezone

try:
    from ib_async import IB, Order
except ImportError:
    print(
        json.dumps(
            {
                "error": "ib_async not installed. Run: pip install ib_async",
                "status": "dependency_missing",
            }
        ),
        file=sys.stderr,
    )
    sys.exit(1)

DEFAULT_HOST = "192.168.2.187"
PORT_PAPER = 7497
PORT_LIVE = 7496
CLIENT_ID = 1005
CONNECT_TIMEOUT = 10
CANCEL_WAIT = 2.0  # seconds to wait for cancel acknowledgement
COMPLETED_ORDERS_TIMEOUT = 5.0


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def _connect(host: str, port: int, client_id: int) -> IB:
    ib = IB()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            await asyncio.wait_for(
                ib.connectAsync(host, port, clientId=client_id, readonly=False),
                timeout=CONNECT_TIMEOUT,
            )
    except asyncio.TimeoutError:
        raise ConnectionError(f"Timed out connecting to TWS at {host}:{port}")
    except Exception as exc:
        raise ConnectionError(f"Cannot reach TWS at {host}:{port} — {exc}") from exc
    return ib


async def _check_completed_order(ib: IB, order_id: int) -> dict | None:
    """Return an already_done result if the order exists in completed orders."""
    try:
        completed = await asyncio.wait_for(
            ib.reqCompletedOrdersAsync(apiOnly=False),
            timeout=COMPLETED_ORDERS_TIMEOUT,
        )
    except Exception:
        return None

    for trade in completed:
        if trade.order.orderId == order_id:
            order = trade.order
            contract = trade.contract
            final_status = trade.orderStatus.status
            return {
                "timestamp": utc_timestamp(),
                "status": "already_done",
                "order_id": order_id,
                "perm_id": order.permId,
                "symbol": contract.symbol,
                "sec_type": contract.secType,
                "currency": contract.currency,
                "action": order.action,
                "order_type": order.orderType,
                "quantity": order.totalQuantity,
                "final_order_status": final_status,
                "message": (
                    f"Order {order_id} is already in terminal state: {final_status}"
                ),
            }
    return None


async def _find_open_trade(ib: IB, order_id: int):
    open_trades = await ib.reqAllOpenOrdersAsync()
    for trade in open_trades:
        if trade.order.orderId == order_id:
            return trade
    return None


async def cancel_order(host: str, port: int, client_id: int, order_id: int) -> dict:
    ib = await _connect(host, port, client_id)

    try:
        with contextlib.redirect_stdout(sys.stderr):
            target_trade = await _find_open_trade(ib, order_id)

        if target_trade is None:
            # Idempotent: check completed orders before declaring not_found
            completed_status = await _check_completed_order(ib, order_id)
            if completed_status is not None:
                return completed_status
            return {
                "timestamp": utc_timestamp(),
                "status": "not_found",
                "order_id": order_id,
                "message": f"No open order found with order_id {order_id}",
            }

        order_client_id = getattr(target_trade.order, "clientId", client_id) or client_id
        if order_client_id != client_id:
            ib.disconnect()
            ib = await _connect(host, port, order_client_id)
            with contextlib.redirect_stdout(sys.stderr):
                target_trade = await _find_open_trade(ib, order_id)

            if target_trade is None:
                return {
                    "timestamp": utc_timestamp(),
                    "status": "not_found",
                    "order_id": order_id,
                    "message": f"Order {order_id} was found on client_id {order_client_id} but is no longer open",
                }

        contract = target_trade.contract
        order = target_trade.order

        with contextlib.redirect_stdout(sys.stderr):
            ib.cancelOrder(order)
            await asyncio.sleep(CANCEL_WAIT)

        final_status = target_trade.orderStatus.status

        return {
            "timestamp": utc_timestamp(),
            "status": "cancel_sent",
            "order_id": order_id,
            "perm_id": order.permId,
            "symbol": contract.symbol,
            "sec_type": contract.secType,
            "currency": contract.currency,
            "action": order.action,
            "order_type": order.orderType,
            "quantity": order.totalQuantity,
            "cancel_client_id": order_client_id,
            "final_order_status": final_status,
        }
    finally:
        ib.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cancel an open order via Interactive Brokers TWS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cancel_order.py --order-id 12345
  python cancel_order.py --order-id 12345 --live
  python cancel_order.py --order-id 12345 --host 10.0.0.1
        """,
    )
    parser.add_argument("--order-id", required=True, type=int, help="TWS order ID to cancel")
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
        result = asyncio.run(cancel_order(args.host, args.port, CLIENT_ID, args.order_id))
    except ConnectionError as exc:
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "status": "tws_unavailable",
                    "hint": "The TWS API is temporarily not available. Please try again later.",
                    "timestamp": utc_timestamp(),
                }
            ),
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error": f"Unexpected error: {exc}",
                    "status": "error",
                    "timestamp": utc_timestamp(),
                }
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    if result.get("status") == "not_found":
        print(json.dumps(result, indent=2), file=sys.stderr)
        sys.exit(1)

    # already_done is idempotent success — print to stdout with exit 0
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
