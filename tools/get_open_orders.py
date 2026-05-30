#!/usr/bin/env python3
"""
get_open_orders.py — Retrieve open/pending orders from Interactive Brokers TWS.

Returns all open orders across all API client connections as a JSON array to
stdout. Uses reqAllOpenOrders so orders placed by any client ID (including
place_order.py's client 1004) are visible. Includes order details and current
fill status.

Usage:
    python get_open_orders.py [--host HOST] [--port PORT] [--paper] [--live]

Connection ID: 1003 (see tools/connection_ids.json)
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
CLIENT_ID = 1003
CONNECT_TIMEOUT = 10


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trade_to_dict(trade) -> dict:
    contract = trade.contract
    order = trade.order
    status = trade.orderStatus

    fills = []
    for fill in trade.fills:
        fills.append(
            {
                "exec_id": fill.execution.execId,
                "time": fill.execution.time,
                "shares": fill.execution.shares,
                "price": fill.execution.price,
                "cum_qty": fill.execution.cumQty,
                "avg_price": fill.execution.avgPrice,
            }
        )

    return {
        "order_id": order.orderId,
        "perm_id": order.permId,
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
        "lmt_price": order.lmtPrice if order.lmtPrice != 1.7976931348623157e+308 else None,
        "aux_price": order.auxPrice if order.auxPrice != 1.7976931348623157e+308 else None,
        "tif": order.tif,
        "account": order.account,
        "status": status.status,
        "filled": status.filled,
        "remaining": status.remaining,
        "avg_fill_price": status.avgFillPrice if status.avgFillPrice else None,
        "why_held": status.whyHeld or None,
        "fills": fills,
    }


async def fetch_open_orders(host: str, port: int, client_id: int) -> list[dict]:
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
        trades = await ib.reqAllOpenOrdersAsync()
        return [_trade_to_dict(t) for t in trades]
    finally:
        ib.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch open/pending orders from Interactive Brokers TWS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python get_open_orders.py                     # paper trading (default)
  python get_open_orders.py --live              # live trading account
  python get_open_orders.py --host 10.0.0.1    # custom TWS host
        """,
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"TWS hostname or IP (default: {DEFAULT_HOST})",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--paper",
        dest="port",
        action="store_const",
        const=PORT_PAPER,
        help=f"Connect to paper trading account (port {PORT_PAPER}, default)",
    )
    mode.add_argument(
        "--live",
        dest="port",
        action="store_const",
        const=PORT_LIVE,
        help=f"Connect to live trading account (port {PORT_LIVE})",
    )
    mode.add_argument(
        "--port",
        dest="port",
        type=int,
        help="Custom TWS port number",
    )

    parser.set_defaults(port=PORT_PAPER)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        orders = asyncio.run(fetch_open_orders(args.host, args.port, CLIENT_ID))
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

    print(json.dumps(orders, indent=2))


if __name__ == "__main__":
    main()
