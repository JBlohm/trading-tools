#!/usr/bin/env python3
"""
cancel_order.py — Cancel an open order via Interactive Brokers TWS.

Looks up the order by order ID among the currently open orders and sends a
cancel request. Returns the final order status as JSON to stdout.

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


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def cancel_order(host: str, port: int, client_id: int, order_id: int) -> dict:
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

    try:
        open_trades = await ib.reqOpenOrdersAsync()

        target_trade = None
        for trade in open_trades:
            if trade.order.orderId == order_id:
                target_trade = trade
                break

        if target_trade is None:
            return {
                "timestamp": utc_timestamp(),
                "status": "not_found",
                "order_id": order_id,
                "message": f"No open order found with order_id {order_id}",
            }

        contract = target_trade.contract
        order = target_trade.order

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

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
