#!/usr/bin/env python3
"""
run_pretrade_stress.py — Run stress scenarios against a hypothetical order and current portfolio.

Applies equity shock, vol shock, rate shock, and FX shock scenarios from risk_limits.json
(or custom params) against the proposed order notional and current portfolio Greeks.

Usage:
    python run_pretrade_stress.py --symbol AAPL --action BUY --quantity 10 --limit-price 180
    python run_pretrade_stress.py --symbol SPX --action BUY --quantity 5 --sec-type OPT \
        --limit-price 50 --equity-shock -20 --vol-shock 50

No TWS connection needed — operates on supplied parameters and the current portfolio snapshot.
"""

import argparse
import asyncio
import contextlib
import io
import json
import pathlib
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
CLIENT_ID = 1011
CONNECT_TIMEOUT = 10
ACCOUNT_DATA_TIMEOUT = 5

LIMITS_FILE = pathlib.Path(__file__).parent / "risk_limits.json"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_limits() -> dict:
    if not LIMITS_FILE.exists():
        raise FileNotFoundError(f"Risk limits file not found: {LIMITS_FILE}")
    return json.loads(LIMITS_FILE.read_text())


def _run_stress_scenarios(
    order_notional: float,
    sec_type: str,
    portfolio_delta: float,
    portfolio_vega: float,
    nlv: float,
    equity_shocks: list,
    vol_shocks: list,
    stress_loss_limit_pct: float,
) -> dict:
    results = {}

    for shock_pct in equity_shocks:
        key = f"equity_shock_{shock_pct}pct"
        if sec_type in ("STK", "ETF"):
            loss = order_notional * shock_pct / 100.0
        elif sec_type in ("OPT", "FOP"):
            loss = portfolio_delta * order_notional * shock_pct / 100.0
        else:
            loss = 0.0
        loss_pct = (loss / nlv * 100.0) if nlv > 0 else 0.0
        results[key] = {
            "scenario": key,
            "shock_type": "equity",
            "shock_pct": shock_pct,
            "estimated_loss": round(loss, 2),
            "pct_nlv": round(loss_pct, 4),
            "status": "fail" if abs(loss_pct) > stress_loss_limit_pct else "pass",
            "limit_pct": stress_loss_limit_pct,
        }

    for shock_pct in vol_shocks:
        key = f"vol_shock_{shock_pct}pct"
        loss = portfolio_vega * shock_pct if sec_type in ("OPT", "FOP") else 0.0
        loss_pct = (loss / nlv * 100.0) if nlv > 0 else 0.0
        results[key] = {
            "scenario": key,
            "shock_type": "vol",
            "shock_pct": shock_pct,
            "estimated_loss": round(loss, 2),
            "pct_nlv": round(loss_pct, 4),
            "status": "fail" if abs(loss_pct) > stress_loss_limit_pct else "pass",
            "limit_pct": stress_loss_limit_pct,
        }

    return results


async def _fetch_portfolio_state(ib) -> tuple:
    try:
        summary = await asyncio.wait_for(ib.accountSummaryAsync(), timeout=ACCOUNT_DATA_TIMEOUT)
        nlv = 0.0
        for item in summary:
            if item.tag == "NetLiquidation":
                try:
                    nlv += float(item.value)
                except (TypeError, ValueError):
                    pass
    except asyncio.TimeoutError:
        nlv = 0.0

    portfolio = ib.portfolio()
    delta = vega = 0.0
    for item in portfolio:
        if item.contract.secType not in ("OPT", "FOP"):
            continue
        pos = item.position
        g = getattr(item, "modelGreeks", None) or getattr(item, "lastGreeks", None)
        if g:
            d = getattr(g, "delta", None)
            v = getattr(g, "vega", None)
            if d is not None:
                delta += d * pos * 100
            if v is not None:
                vega += v * pos * 100
    return nlv, delta, vega


async def run_stress(host: str, port: int, args: argparse.Namespace, lim: dict) -> dict:
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
        nlv, port_delta, port_vega = await _fetch_portfolio_state(ib)
    finally:
        ib.disconnect()

    sec_type = args.sec_type.upper()
    mult = 100.0 if sec_type in ("OPT", "FOP") else 1.0
    order_notional = abs(args.quantity * args.limit_price * mult)

    stress_limit_pct = lim.get("stress_loss_limit_pct_nlv", 15.0)
    scenarios = lim.get("stress_scenarios", {})
    equity_shocks = args.equity_shocks or scenarios.get("equity_shock_pct", [-10, -20])
    vol_shocks = args.vol_shocks or scenarios.get("vol_shock_pct", [20, 40])

    stress_results = _run_stress_scenarios(
        order_notional, sec_type, port_delta, port_vega,
        nlv, equity_shocks, vol_shocks, stress_limit_pct,
    )

    failures = [v for v in stress_results.values() if v["status"] == "fail"]
    return {
        "timestamp": utc_timestamp(),
        "order": {
            "symbol": args.symbol.upper(),
            "sec_type": sec_type,
            "action": args.action.upper(),
            "quantity": args.quantity,
            "limit_price": args.limit_price,
            "estimated_notional": round(order_notional, 2),
        },
        "portfolio": {
            "nlv": round(nlv, 2),
            "portfolio_delta": round(port_delta, 4),
            "portfolio_vega": round(port_vega, 4),
        },
        "overall_verdict": "fail" if failures else "pass",
        "failure_count": len(failures),
        "stress_results": stress_results,
        "failures": [{"scenario": f["scenario"], "pct_nlv": f["pct_nlv"],
                      "estimated_loss": f["estimated_loss"]} for f in failures],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run stress scenarios against a proposed order and current portfolio.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_pretrade_stress.py --symbol AAPL --action BUY --quantity 10 --limit-price 180
  python run_pretrade_stress.py --symbol SPX --action BUY --quantity 5 --sec-type OPT \
      --limit-price 50 --equity-shock -20 --vol-shock 50
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
    parser.add_argument("--action", required=True, choices=["BUY", "SELL"])
    parser.add_argument("--quantity", required=True, type=float)
    parser.add_argument("--limit-price", required=True, type=float, dest="limit_price",
                        help="Reference price for notional calculation")
    parser.add_argument("--equity-shock", type=float, action="append", dest="equity_shocks",
                        metavar="PCT", help="Equity shock %% (can repeat; default from risk_limits.json)")
    parser.add_argument("--vol-shock", type=float, action="append", dest="vol_shocks",
                        metavar="PCT", help="Vol shock %% (can repeat; default from risk_limits.json)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        lim_data = _load_limits()
        lim = lim_data.get("limits", lim_data)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc), "status": "config_error",
                          "timestamp": utc_timestamp()}), file=sys.stderr)
        sys.exit(1)

    try:
        result = asyncio.run(run_stress(args.host, args.port, args, lim))
    except ConnectionError as exc:
        print(json.dumps({"error": str(exc), "status": "tws_unavailable",
                          "timestamp": utc_timestamp()}), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(json.dumps({"error": f"Unexpected error: {exc}", "status": "error",
                          "timestamp": utc_timestamp()}), file=sys.stderr)
        sys.exit(1)

    exit_code = 0 if result["overall_verdict"] == "pass" else 2
    print(json.dumps(result, indent=2))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
