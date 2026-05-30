#!/usr/bin/env python3
"""
get_margin_usage.py — Retrieve account margin and liquidity metrics from TWS.

Returns Net Liquidation Value (NLV), excess liquidity, buying power, and
related margin fields as JSON to stdout.

Usage:
    python get_margin_usage.py [--host HOST] [--port PORT] [--paper] [--live]
    python get_margin_usage.py [--account ACCOUNT]

Connection ID: 1002 (see tools/connection_ids.json)
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
CLIENT_ID = 1002
CONNECT_TIMEOUT = 10

# Account summary tags we care about
SUMMARY_TAGS = [
    "NetLiquidation",
    "ExcessLiquidity",
    "BuyingPower",
    "InitMarginReq",
    "MaintMarginReq",
    "AvailableFunds",
    "GrossPositionValue",
    "TotalCashValue",
    "UnrealizedPnL",
    "RealizedPnL",
]


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_float(value: str):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def fetch_margin_usage(host: str, port: int, client_id: int, account: str = "") -> dict:
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
        tag_list = ",".join(SUMMARY_TAGS)
        summary_items = await ib.reqAccountSummaryAsync(group="All", tags=tag_list)

        # Build per-account dictionaries
        accounts: dict[str, dict] = {}
        for item in summary_items:
            acct = item.account
            if account and acct != account:
                continue
            if acct not in accounts:
                accounts[acct] = {}
            accounts[acct][item.tag] = _parse_float(item.value)

        result = {}
        for acct, values in accounts.items():
            result[acct] = {
                "nlv": values.get("NetLiquidation"),
                "excess_liquidity": values.get("ExcessLiquidity"),
                "buying_power": values.get("BuyingPower"),
                "init_margin_req": values.get("InitMarginReq"),
                "maint_margin_req": values.get("MaintMarginReq"),
                "available_funds": values.get("AvailableFunds"),
                "gross_position_value": values.get("GrossPositionValue"),
                "total_cash": values.get("TotalCashValue"),
                "unrealized_pnl": values.get("UnrealizedPnL"),
                "realized_pnl": values.get("RealizedPnL"),
            }

        return {"timestamp": utc_timestamp(), "accounts": result}
    finally:
        ib.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch margin usage and liquidity metrics from Interactive Brokers TWS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python get_margin_usage.py                          # paper trading (default)
  python get_margin_usage.py --live                   # live trading account
  python get_margin_usage.py --account DU123456       # filter to one account
  python get_margin_usage.py --host 10.0.0.1         # custom TWS host
        """,
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"TWS hostname or IP (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--account",
        default="",
        help="Filter to a specific account ID (default: all accounts)",
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
        result = asyncio.run(fetch_margin_usage(args.host, args.port, CLIENT_ID, args.account))
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

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
