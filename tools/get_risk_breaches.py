#!/usr/bin/env python3
"""
get_risk_breaches.py — Detect current and projected portfolio risk limit breaches.

Checks the following risk dimensions:
  - Portfolio delta vs. configured limit
  - Portfolio vega vs. configured limit
  - Single-position concentration vs. NLV
  - Margin cushion (excess liquidity / NLV)
  - Open order projected impact (estimated delta from pending orders)

Returns structured breach records with reason codes and severity levels.

Usage:
  python get_risk_breaches.py [--host HOST] [--port PORT] [--paper] [--live]
  python get_risk_breaches.py [--max-delta N] [--max-vega N] [--max-position-pct N]
                               [--min-cushion-pct N]

Connection ID: 1008 (see tools/connection_ids.json)
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
CLIENT_ID = 1008
CONNECT_TIMEOUT = 10
MARKET_DATA_TIMEOUT = 5

DEFAULT_MAX_DELTA = 500.0
DEFAULT_MAX_VEGA = 10_000.0
DEFAULT_MAX_POSITION_PCT = 20.0
DEFAULT_MIN_CUSHION_PCT = 10.0

IB_UNSET = 1.7976931348623157e308

SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _greeks_to_dict(greeks) -> dict:
    if greeks is None:
        return {"delta": None, "gamma": None, "theta": None, "vega": None}
    return {
        "delta": greeks.delta,
        "gamma": greeks.gamma,
        "theta": greeks.theta,
        "vega": greeks.vega,
    }


def _add_breach(breaches: list, reason_code: str, metric: str, current_value, threshold, severity: str, detail: str = "") -> None:
    breaches.append(
        {
            "reason_code": reason_code,
            "metric": metric,
            "current_value": current_value,
            "threshold": threshold,
            "severity": severity,
            "detail": detail,
        }
    )


async def fetch_risk_breaches(
    host: str,
    port: int,
    client_id: int,
    max_delta: float,
    max_vega: float,
    max_position_pct: float,
    min_cushion_pct: float,
) -> dict:
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
        portfolio = ib.portfolio()
        summary_items = await ib.accountSummaryAsync()
        open_trades = await ib.reqAllOpenOrdersAsync()

        # --- Build NLV and excess liquidity per account ---
        nlv_total = 0.0
        excess_total = 0.0
        for sv in summary_items:
            try:
                val = float(sv.value)
            except (TypeError, ValueError):
                continue
            if sv.tag == "NetLiquidation":
                nlv_total += val
            elif sv.tag == "ExcessLiquidity":
                excess_total += val

        # --- Collect portfolio greeks from positions ---
        portfolio_delta = 0.0
        portfolio_vega = 0.0
        position_risks = []

        for item in portfolio:
            if item.position == 0:
                continue
            c = item.contract
            is_option = c.secType in ("OPT", "FOP")
            greeks = {"delta": None, "gamma": None, "theta": None, "vega": None}

            if is_option:
                ticker = ib.reqMktData(c, genericTickList="", snapshot=True, regulatorySnapshot=False)
                await asyncio.sleep(MARKET_DATA_TIMEOUT)
                raw = ticker.modelGreeks or ticker.lastGreeks
                greeks = _greeks_to_dict(raw)
                ib.cancelMktData(c)

                if greeks["delta"] is not None:
                    portfolio_delta += greeks["delta"] * item.position
                if greeks["vega"] is not None:
                    portfolio_vega += greeks["vega"] * item.position

            position_pct_nlv = (abs(item.marketValue) / nlv_total * 100) if nlv_total > 0 else None

            position_risks.append(
                {
                    "symbol": c.symbol,
                    "sec_type": c.secType,
                    "position": item.position,
                    "market_value": item.marketValue,
                    "position_pct_nlv": round(position_pct_nlv, 2) if position_pct_nlv is not None else None,
                    "greeks": greeks,
                    "account": item.account,
                }
            )

        # --- Open orders projected delta impact ---
        projected_delta = portfolio_delta
        projected_vega = portfolio_vega
        open_order_impacts = []

        for trade in open_trades:
            c = trade.contract
            o = trade.order
            action_sign = 1.0 if o.action == "BUY" else -1.0
            qty = o.totalQuantity

            est_delta = None
            est_vega = None
            if c.secType == "STK":
                est_delta = action_sign * qty
            # For options, we can't estimate without greeks, so leave as None

            impact = {
                "order_id": o.orderId,
                "symbol": c.symbol,
                "sec_type": c.secType,
                "action": o.action,
                "quantity": qty,
                "order_type": o.orderType,
                "estimated_delta_impact": est_delta,
                "estimated_vega_impact": est_vega,
            }
            open_order_impacts.append(impact)

            if est_delta is not None:
                projected_delta += est_delta
            if est_vega is not None:
                projected_vega += est_vega

        # --- Evaluate current breaches ---
        current_breaches = []
        cushion_pct = (excess_total / nlv_total * 100) if nlv_total > 0 else None

        if abs(portfolio_delta) > max_delta:
            severity = SEVERITY_CRITICAL if abs(portfolio_delta) > max_delta * 1.5 else SEVERITY_WARNING
            _add_breach(
                current_breaches,
                "MAX_DELTA_EXCEEDED",
                "portfolio_delta",
                round(portfolio_delta, 4),
                max_delta,
                severity,
                f"Portfolio delta {portfolio_delta:.2f} exceeds limit ±{max_delta}",
            )

        if abs(portfolio_vega) > max_vega:
            severity = SEVERITY_CRITICAL if abs(portfolio_vega) > max_vega * 1.5 else SEVERITY_WARNING
            _add_breach(
                current_breaches,
                "MAX_VEGA_EXCEEDED",
                "portfolio_vega",
                round(portfolio_vega, 4),
                max_vega,
                severity,
                f"Portfolio vega {portfolio_vega:.2f} exceeds limit ±{max_vega}",
            )

        for pr in position_risks:
            if pr["position_pct_nlv"] is not None and pr["position_pct_nlv"] > max_position_pct:
                severity = SEVERITY_CRITICAL if pr["position_pct_nlv"] > max_position_pct * 1.5 else SEVERITY_WARNING
                _add_breach(
                    current_breaches,
                    "CONCENTRATION_LIMIT",
                    "position_pct_nlv",
                    pr["position_pct_nlv"],
                    max_position_pct,
                    severity,
                    f"{pr['symbol']} represents {pr['position_pct_nlv']:.1f}% of NLV (limit {max_position_pct}%)",
                )

        if cushion_pct is not None and cushion_pct < min_cushion_pct:
            severity = SEVERITY_CRITICAL if cushion_pct < min_cushion_pct / 2 else SEVERITY_WARNING
            _add_breach(
                current_breaches,
                "MARGIN_CUSHION_LOW",
                "cushion_pct_nlv",
                round(cushion_pct, 2),
                min_cushion_pct,
                severity,
                f"Margin cushion {cushion_pct:.1f}% of NLV is below minimum {min_cushion_pct}%",
            )

        # --- Evaluate projected breaches (if open orders were to fill) ---
        projected_breaches = []

        if abs(projected_delta) > max_delta and abs(projected_delta) > abs(portfolio_delta):
            _add_breach(
                projected_breaches,
                "PROJECTED_MAX_DELTA_EXCEEDED",
                "projected_portfolio_delta",
                round(projected_delta, 4),
                max_delta,
                SEVERITY_WARNING,
                f"If open orders fill, portfolio delta would be {projected_delta:.2f} (limit ±{max_delta})",
            )

        if abs(projected_vega) > max_vega and abs(projected_vega) > abs(portfolio_vega):
            _add_breach(
                projected_breaches,
                "PROJECTED_MAX_VEGA_EXCEEDED",
                "projected_portfolio_vega",
                round(projected_vega, 4),
                max_vega,
                SEVERITY_WARNING,
                f"If open orders fill, portfolio vega would be {projected_vega:.2f} (limit ±{max_vega})",
            )

        return {
            "timestamp": utc_timestamp(),
            "thresholds": {
                "max_delta": max_delta,
                "max_vega": max_vega,
                "max_position_pct_nlv": max_position_pct,
                "min_cushion_pct_nlv": min_cushion_pct,
            },
            "risk_metrics": {
                "nlv": nlv_total,
                "excess_liquidity": excess_total,
                "cushion_pct_nlv": round(cushion_pct, 2) if cushion_pct is not None else None,
                "portfolio_delta": round(portfolio_delta, 4),
                "portfolio_vega": round(portfolio_vega, 4),
                "projected_delta": round(projected_delta, 4),
                "projected_vega": round(projected_vega, 4),
            },
            "current_breaches": current_breaches,
            "projected_breaches": projected_breaches,
            "position_risks": position_risks,
            "open_order_impacts": open_order_impacts,
        }
    finally:
        ib.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect current and projected portfolio risk limit breaches.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python get_risk_breaches.py                          # paper trading, default thresholds
  python get_risk_breaches.py --live                   # live trading
  python get_risk_breaches.py --max-delta 300          # tighter delta limit
  python get_risk_breaches.py --min-cushion-pct 20     # require 20%% margin cushion
        """,
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST,
        help=f"TWS hostname or IP (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--max-delta", type=float, default=DEFAULT_MAX_DELTA,
        help=f"Max absolute portfolio delta (default: {DEFAULT_MAX_DELTA})",
    )
    parser.add_argument(
        "--max-vega", type=float, default=DEFAULT_MAX_VEGA,
        help=f"Max absolute portfolio vega (default: {DEFAULT_MAX_VEGA})",
    )
    parser.add_argument(
        "--max-position-pct", type=float, default=DEFAULT_MAX_POSITION_PCT,
        help=f"Max single-position size as %% of NLV (default: {DEFAULT_MAX_POSITION_PCT})",
    )
    parser.add_argument(
        "--min-cushion-pct", type=float, default=DEFAULT_MIN_CUSHION_PCT,
        help=f"Minimum margin cushion as %% of NLV (default: {DEFAULT_MIN_CUSHION_PCT})",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--paper", dest="port", action="store_const", const=PORT_PAPER,
        help=f"Connect to paper trading account (port {PORT_PAPER}, default)",
    )
    mode.add_argument(
        "--live", dest="port", action="store_const", const=PORT_LIVE,
        help=f"Connect to live trading account (port {PORT_LIVE})",
    )
    mode.add_argument("--port", dest="port", type=int, help="Custom TWS port number")

    parser.set_defaults(port=PORT_PAPER)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        result = asyncio.run(
            fetch_risk_breaches(
                args.host,
                args.port,
                CLIENT_ID,
                args.max_delta,
                args.max_vega,
                args.max_position_pct,
                args.min_cushion_pct,
            )
        )
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
