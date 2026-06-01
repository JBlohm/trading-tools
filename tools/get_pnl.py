#!/usr/bin/env python3
"""
get_pnl.py — Report current P&L (unrealized + realized) for the account and each position.

Usage:
    python get_pnl.py
    python get_pnl.py --live

Connection ID: 1014 (see tools/connection_ids.json)
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
        json.dumps({"error": "ib_async not installed. Run: pip install ib_async",
                    "status": "dependency_missing"}),
        file=sys.stderr,
    )
    sys.exit(1)

DEFAULT_HOST = "192.168.2.187"
PORT_PAPER = 7497
PORT_LIVE = 7496
CLIENT_ID = 1014
CONNECT_TIMEOUT = 10
ACCOUNT_DATA_TIMEOUT = 5


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def _get_account_pnl(ib) -> dict:
    try:
        summary = await asyncio.wait_for(ib.accountSummaryAsync(), timeout=ACCOUNT_DATA_TIMEOUT)
    except asyncio.TimeoutError:
        return {}
    tags: dict = {}
    for item in summary:
        try:
            v = float(item.value)
        except (TypeError, ValueError):
            continue
        tags[item.tag] = tags.get(item.tag, 0.0) + v
    return {
        "nlv": tags.get("NetLiquidation", 0.0),
        "unrealized_pnl": tags.get("UnrealizedPnL", 0.0),
        "realized_pnl": tags.get("RealizedPnL", 0.0),
    }


def _build_pnl_report(portfolio_items, account: dict) -> dict:
    positions = []
    total_unrealized = 0.0
    total_realized = 0.0

    for item in portfolio_items:
        c = item.contract
        upnl = item.unrealizedPNL or 0.0
        rpnl = item.realizedPNL or 0.0
        total_unrealized += upnl
        total_realized += rpnl
        positions.append({
            "symbol": c.symbol,
            "sec_type": c.secType,
            "currency": getattr(c, "currency", "USD"),
            "position": item.position,
            "market_price": item.marketPrice,
            "market_value": item.marketValue,
            "average_cost": item.averageCost,
            "unrealized_pnl": round(upnl, 2),
            "realized_pnl": round(rpnl, 2),
            "total_pnl": round(upnl + rpnl, 2),
        })

    nlv = account.get("nlv", 0.0)
    total = total_unrealized + total_realized
    return {
        "timestamp": utc_timestamp(),
        "account": {
            "nlv": round(nlv, 2),
            "unrealized_pnl": round(account.get("unrealized_pnl", total_unrealized), 2),
            "realized_pnl": round(account.get("realized_pnl", total_realized), 2),
            "total_pnl": round(
                account.get("unrealized_pnl", total_unrealized)
                + account.get("realized_pnl", total_realized), 2),
            "pnl_pct_nlv": round(total / nlv * 100.0, 4) if nlv > 0 else 0.0,
        },
        "positions": positions,
    }


async def get_pnl(host: str, port: int) -> dict:
    ib = IB()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            await asyncio.wait_for(
                ib.connectAsync(host, port, clientId=CLIENT_ID, readonly=True),
                timeout=CONNECT_TIMEOUT,
            )
    except asyncio.TimeoutError:
        raise ConnectionError(f"Timed out connecting to TWS at {host}:{port}")
    except Exception as exc:
        raise ConnectionError(f"Cannot reach TWS at {host}:{port} — {exc}") from exc

    try:
        account = await _get_account_pnl(ib)
        portfolio = ib.portfolio()
        return _build_pnl_report(portfolio, account)
    finally:
        ib.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report current P&L for the account and each position.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python get_pnl.py
  python get_pnl.py --live
        """,
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--paper", dest="port", action="store_const", const=PORT_PAPER)
    mode.add_argument("--live", dest="port", action="store_const", const=PORT_LIVE)
    mode.add_argument("--port", dest="port", type=int)
    parser.set_defaults(port=PORT_PAPER)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        result = asyncio.run(get_pnl(args.host, args.port))
    except ConnectionError as exc:
        print(json.dumps({"error": str(exc), "status": "tws_unavailable",
                          "timestamp": utc_timestamp()}), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(json.dumps({"error": f"Unexpected error: {exc}", "status": "error",
                          "timestamp": utc_timestamp()}), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
