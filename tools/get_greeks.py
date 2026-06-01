#!/usr/bin/env python3
"""
get_greeks.py — Retrieve portfolio-level option Greeks from Interactive Brokers TWS.

Iterates over all option positions in the portfolio, requests model Greeks for
each, and returns aggregate portfolio delta/gamma/theta/vega as JSON to stdout.
Non-option positions are included in the output with zero Greeks.

Also returns bucketed Greeks grouped by underlying symbol and by expiry date,
giving a breakdown of where risk is concentrated.

Usage:
    python get_greeks.py [--host HOST] [--port PORT] [--paper] [--live]

Connection ID: 1001 (see tools/connection_ids.json)
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
CLIENT_ID = 1001
CONNECT_TIMEOUT = 10
MARKET_DATA_TIMEOUT = 5  # seconds to wait for Greeks snapshot


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _greeks_to_dict(greeks) -> dict:
    """Extract Greek values from a TickOptionComputation object (or None)."""
    if greeks is None:
        return {"delta": None, "gamma": None, "theta": None, "vega": None, "iv": None}
    return {
        "delta": greeks.delta,
        "gamma": greeks.gamma,
        "theta": greeks.theta,
        "vega": greeks.vega,
        "iv": greeks.impliedVol,
    }


def _scale_greeks(greeks_dict: dict, position: float) -> dict:
    """Multiply each Greek by position size to get position-level exposure."""
    scaled = {}
    for key, val in greeks_dict.items():
        scaled[key] = (val * position) if val is not None else None
    return scaled


def _empty_bucket() -> dict:
    return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "position_count": 0}


def _add_to_bucket(bucket: dict, scaled: dict) -> None:
    for key in ("delta", "gamma", "theta", "vega"):
        if scaled.get(key) is not None:
            bucket[key] += scaled[key]
    bucket["position_count"] += 1


def _build_bucketed_greeks(positions_out: list, scaled_map: dict) -> dict:
    """Return greeks bucketed by underlying symbol and by expiry."""
    by_underlying: dict = {}
    by_expiry: dict = {}

    for pos in positions_out:
        key = (pos["symbol"], pos["expiry"], pos["strike"], pos["right"])
        scaled = scaled_map.get(key)
        if scaled is None:
            continue

        sym = pos["symbol"]
        expiry = pos["expiry"] or "no_expiry"

        if sym not in by_underlying:
            by_underlying[sym] = _empty_bucket()
        _add_to_bucket(by_underlying[sym], scaled)

        if expiry not in by_expiry:
            by_expiry[expiry] = _empty_bucket()
        _add_to_bucket(by_expiry[expiry], scaled)

    return {"by_underlying": by_underlying, "by_expiry": by_expiry}


async def fetch_greeks(host: str, port: int, client_id: int) -> dict:
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
        portfolio: list = ib.portfolio()
        positions_out = []
        portfolio_delta = 0.0
        portfolio_gamma = 0.0
        portfolio_theta = 0.0
        portfolio_vega = 0.0
        # Keyed by (symbol, expiry, strike, right) for bucketing lookup
        scaled_map: dict = {}

        for item in portfolio:
            contract = item.contract
            is_option = contract.secType in ("OPT", "FOP")

            position_greeks = {"delta": None, "gamma": None, "theta": None, "vega": None, "iv": None}

            if is_option:
                ticker = ib.reqMktData(contract, genericTickList="", snapshot=True, regulatorySnapshot=False)
                try:
                    await asyncio.sleep(MARKET_DATA_TIMEOUT)
                except asyncio.CancelledError:
                    raise
                greeks_raw = ticker.modelGreeks or ticker.lastGreeks
                position_greeks = _greeks_to_dict(greeks_raw)
                ib.cancelMktData(contract)

                scaled = _scale_greeks(position_greeks, item.position)
                if scaled["delta"] is not None:
                    portfolio_delta += scaled["delta"]
                if scaled["gamma"] is not None:
                    portfolio_gamma += scaled["gamma"]
                if scaled["theta"] is not None:
                    portfolio_theta += scaled["theta"]
                if scaled["vega"] is not None:
                    portfolio_vega += scaled["vega"]

                expiry = getattr(contract, "lastTradeDateOrContractMonth", None)
                strike = getattr(contract, "strike", None)
                right = getattr(contract, "right", None)
                bucket_key = (contract.symbol, expiry, strike, right)
                scaled_map[bucket_key] = scaled

            positions_out.append(
                {
                    "symbol": contract.symbol,
                    "sec_type": contract.secType,
                    "expiry": getattr(contract, "lastTradeDateOrContractMonth", None),
                    "strike": getattr(contract, "strike", None),
                    "right": getattr(contract, "right", None),
                    "currency": contract.currency,
                    "position": item.position,
                    "greeks": position_greeks,
                    "account": item.account,
                }
            )

        return {
            "timestamp": utc_timestamp(),
            "portfolio_greeks": {
                "delta": portfolio_delta,
                "gamma": portfolio_gamma,
                "theta": portfolio_theta,
                "vega": portfolio_vega,
            },
            "bucketed_greeks": _build_bucketed_greeks(positions_out, scaled_map),
            "positions": positions_out,
        }
    finally:
        ib.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch portfolio-level option Greeks from Interactive Brokers TWS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python get_greeks.py                     # paper trading (default)
  python get_greeks.py --live              # live trading account
  python get_greeks.py --host 10.0.0.1    # custom TWS host
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
        result = asyncio.run(fetch_greeks(args.host, args.port, CLIENT_ID))
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
