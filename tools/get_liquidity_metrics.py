#!/usr/bin/env python3
"""
get_liquidity_metrics.py — Fetch market liquidity metrics for a symbol.

Retrieves bid/ask spread, volume, and quote data from TWS to assess
execution quality before trading.

Usage:
    python get_liquidity_metrics.py --symbol AAPL
    python get_liquidity_metrics.py --symbol SPY --live

Connection ID: 1015 (see tools/connection_ids.json)
"""

import argparse
import asyncio
import contextlib
import io
import json
import sys
from datetime import datetime, timezone

try:
    from ib_async import IB, Stock, Option, Future, Contract
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
CLIENT_ID = 1015
CONNECT_TIMEOUT = 10
MARKET_DATA_TIMEOUT = 5
IB_UNSET = 1.7976931348623157e308


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean(val):
    if val is None or val == IB_UNSET:
        return None
    return val


def _build_contract(args: argparse.Namespace):
    sec_type = args.sec_type.upper()
    if sec_type == "STK":
        return Stock(args.symbol, args.exchange or "SMART", args.currency)
    if sec_type in ("OPT", "FOP"):
        c = Option(args.symbol, args.expiry or "", args.strike or 0.0,
                   args.right or "C", args.exchange or "SMART", currency=args.currency)
        c.secType = sec_type
        return c
    if sec_type == "FUT":
        return Future(args.symbol, args.expiry or "", args.exchange or "SMART",
                      currency=args.currency)
    c = Contract()
    c.symbol = args.symbol
    c.secType = sec_type
    c.currency = args.currency
    c.exchange = args.exchange or "SMART"
    return c


async def get_liquidity_metrics(host: str, port: int, args: argparse.Namespace) -> dict:
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
        contract = _build_contract(args)
        with contextlib.redirect_stdout(sys.stderr):
            try:
                await ib.qualifyContractsAsync(contract)
            except Exception:
                pass

        ticker = ib.reqMktData(contract, genericTickList="", snapshot=True,
                               regulatorySnapshot=False)
        try:
            await asyncio.sleep(MARKET_DATA_TIMEOUT)
        except asyncio.CancelledError:
            raise
        ib.cancelMktData(contract)

        bid = _clean(getattr(ticker, "bid", None))
        ask = _clean(getattr(ticker, "ask", None))
        last = _clean(getattr(ticker, "last", None))
        close = _clean(getattr(ticker, "close", None))
        volume = _clean(getattr(ticker, "volume", None))
        quote_time = getattr(ticker, "time", None)

        mid = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        spread = (ask - bid) if bid is not None and ask is not None else None
        spread_pct = (spread / mid * 100.0) if spread is not None and mid and mid > 0 else None

        ref_price = mid or last or close or 0.0
        adv_est = (volume * ref_price) if volume is not None and ref_price > 0 else None

        return {
            "timestamp": utc_timestamp(),
            "symbol": args.symbol.upper(),
            "sec_type": args.sec_type.upper(),
            "bid": bid,
            "ask": ask,
            "mid": round(mid, 4) if mid is not None else None,
            "last": last,
            "close": close,
            "spread": round(spread, 4) if spread is not None else None,
            "spread_pct": round(spread_pct, 4) if spread_pct is not None else None,
            "volume": volume,
            "estimated_adv_notional": round(adv_est, 2) if adv_est is not None else None,
            "quote_time": str(quote_time) if quote_time else None,
            "liquidity_assessment": _assess_liquidity(spread_pct, volume),
        }
    finally:
        ib.disconnect()


def _assess_liquidity(spread_pct, volume) -> str:
    if spread_pct is None:
        return "unknown"
    if spread_pct < 0.1 and (volume is None or volume > 1_000_000):
        return "excellent"
    if spread_pct < 0.5:
        return "good"
    if spread_pct < 2.0:
        return "fair"
    if spread_pct < 5.0:
        return "poor"
    return "illiquid"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch market liquidity metrics for a symbol.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python get_liquidity_metrics.py --symbol AAPL
  python get_liquidity_metrics.py --symbol SPY --live
        """,
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--paper", dest="port", action="store_const", const=PORT_PAPER)
    mode.add_argument("--live", dest="port", action="store_const", const=PORT_LIVE)
    mode.add_argument("--port", dest="port", type=int)
    parser.set_defaults(port=PORT_PAPER)

    parser.add_argument("--symbol", required=True)
    parser.add_argument("--sec-type", default="STK", dest="sec_type")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--exchange", default="SMART")
    parser.add_argument("--expiry")
    parser.add_argument("--strike", type=float)
    parser.add_argument("--right", choices=["C", "P"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        result = asyncio.run(get_liquidity_metrics(args.host, args.port, args))
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
