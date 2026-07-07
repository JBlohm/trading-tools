#!/usr/bin/env python3
"""
get_positions.py — Retrieve current positions from Interactive Brokers TWS.

Returns positions as a JSON array to stdout.

Usage:
    python get_positions.py [--host HOST] [--port PORT] [--paper] [--live]

Connection ID: 1000 (see ../tools/connection_ids.json)
"""

import argparse
import asyncio
import contextlib
import io
import json
import sys
from datetime import datetime, timezone
from typing import Any

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
CLIENT_ID = 1000
CONNECT_TIMEOUT = 10  # seconds


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def position_to_dict(item: Any) -> dict:
    contract = item.contract
    position = {
        "symbol": contract.symbol,
        "sec_type": contract.secType,
        "currency": contract.currency,
        "exchange": contract.primaryExchange or contract.exchange,
        "con_id": contract.conId,
        "position": item.position,
        "market_price": item.marketPrice,
        "market_value": item.marketValue,
        "average_cost": item.averageCost,
        "unrealized_pnl": item.unrealizedPNL,
        "realized_pnl": item.realizedPNL,
        "account": item.account,
    }
    if contract.secType == "OPT":
        position.update(
            {
                "expiry": contract.lastTradeDateOrContractMonth,
                "strike": contract.strike,
                "right": contract.right,
            }
        )
    return position


async def fetch_positions(host: str, port: int, client_id: int) -> list[dict]:
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
        portfolio: list[Any] = ib.portfolio()
        return [position_to_dict(item) for item in portfolio]
    finally:
        ib.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch current positions from Interactive Brokers TWS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python get_positions.py                     # paper trading (default)
  python get_positions.py --live              # live trading account
  python get_positions.py --host 10.0.0.1    # custom TWS host
  python get_positions.py --port 4002        # custom port (IB Gateway)
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
        positions = asyncio.run(fetch_positions(args.host, args.port, CLIENT_ID))
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

    print(json.dumps(positions, indent=2))


if __name__ == "__main__":
    main()
