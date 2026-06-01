#!/usr/bin/env python3
"""
get_exposure.py — Report current portfolio exposure by symbol, asset class, and strategy.

Connects to TWS, reads the current portfolio, and computes gross/net exposure,
% NLV, and beta-adjusted exposure for each position and in aggregate.

Usage:
    python get_exposure.py
    python get_exposure.py --live
    python get_exposure.py --by asset_class

Connection ID: 1013 (see tools/connection_ids.json)
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
CLIENT_ID = 1013
CONNECT_TIMEOUT = 10
ACCOUNT_DATA_TIMEOUT = 5

EQUITY_BETA = {
    "STK": 1.0,
    "ETF": 1.0,
    "OPT": 0.0,
    "FOP": 0.0,
    "FUT": 0.5,
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def _get_nlv(ib) -> float:
    try:
        summary = await asyncio.wait_for(ib.accountSummaryAsync(), timeout=ACCOUNT_DATA_TIMEOUT)
        nlv = 0.0
        for item in summary:
            if item.tag == "NetLiquidation":
                try:
                    nlv += float(item.value)
                except (ValueError, TypeError):
                    pass
        return nlv
    except asyncio.TimeoutError:
        return 0.0


def _build_exposure_report(portfolio_items, nlv: float) -> dict:
    positions = []
    total_gross = 0.0
    total_long = 0.0
    total_short = 0.0
    by_asset_class: dict = {}

    for item in portfolio_items:
        c = item.contract
        sec_type = c.secType
        mv = item.marketValue
        pos = item.position
        symbol = c.symbol
        currency = getattr(c, "currency", "USD")

        sign = 1.0 if mv >= 0 else -1.0
        gross = abs(mv)
        pct_nlv = (mv / nlv * 100.0) if nlv > 0 else 0.0
        beta = EQUITY_BETA.get(sec_type, 1.0)
        beta_adj = mv * beta

        if mv > 0:
            total_long += mv
        else:
            total_short += mv
        total_gross += gross

        entry = {
            "symbol": symbol,
            "sec_type": sec_type,
            "currency": currency,
            "position": pos,
            "market_value": round(mv, 2),
            "gross_exposure": round(gross, 2),
            "pct_nlv": round(pct_nlv, 4),
            "beta": beta,
            "beta_adjusted_exposure": round(beta_adj, 2),
        }
        positions.append(entry)

        ac = by_asset_class.setdefault(sec_type, {"gross": 0.0, "net": 0.0, "beta_adj": 0.0})
        ac["gross"] += gross
        ac["net"] += mv
        ac["beta_adj"] += beta_adj

    net = total_long + total_short
    pct_gross = (total_gross / nlv * 100.0) if nlv > 0 else 0.0
    pct_net = (net / nlv * 100.0) if nlv > 0 else 0.0

    asset_class_summary = {
        k: {
            "gross_exposure": round(v["gross"], 2),
            "net_exposure": round(v["net"], 2),
            "beta_adjusted_exposure": round(v["beta_adj"], 2),
            "pct_nlv_gross": round(v["gross"] / nlv * 100.0, 4) if nlv > 0 else 0.0,
        }
        for k, v in by_asset_class.items()
    }

    return {
        "timestamp": utc_timestamp(),
        "nlv": round(nlv, 2),
        "summary": {
            "total_gross_exposure": round(total_gross, 2),
            "total_long_exposure": round(total_long, 2),
            "total_short_exposure": round(total_short, 2),
            "net_exposure": round(net, 2),
            "gross_pct_nlv": round(pct_gross, 4),
            "net_pct_nlv": round(pct_net, 4),
        },
        "by_asset_class": asset_class_summary,
        "positions": positions,
    }


async def get_exposure(host: str, port: int) -> dict:
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
        nlv = await _get_nlv(ib)
        portfolio = ib.portfolio()
        return _build_exposure_report(portfolio, nlv)
    finally:
        ib.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report current portfolio exposure by symbol and asset class.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python get_exposure.py
  python get_exposure.py --live
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
        result = asyncio.run(get_exposure(args.host, args.port))
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
