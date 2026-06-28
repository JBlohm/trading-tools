#!/usr/bin/env python3
"""
qld_gld_rebalance.py — Monthly rebalance tool for 30% QLD / 70% GLD allocation.

Generates target allocations, current drift, and order-intent quantities for
a fixed-weight portfolio on a given account size.

Strategy summary (relaxed-mandate static allocation — TRA-73):
  - 30% ProShares Ultra QQQ (QLD, 2x leveraged)
  - 70% SPDR Gold Shares (GLD)
  - Monthly rebalance on the first trading day of each calendar month
  - Drift tolerance: ±5% before mandatory rebalance; no tolerance override for monthly
  - Account baseline: $25,000
  - CAGR (backtest): 17.50% | Max drawdown: -29.38% | Worst year: -21.42%

Risk warning:
  QLD is a 2x daily-leveraged ETF. It resets leverage daily, so it has volatility
  decay (beta-slippage) in choppy markets. It can lose a large fraction of its value
  in sustained downtrends. Never average down into QLD losses.

Usage:
    python tools/qld_gld_rebalance.py \\
        --qld-price 85.50 --qld-shares 87 \\
        --gld-price 195.20 --gld-shares 90 \\
        --account 25000

    python tools/qld_gld_rebalance.py \\
        --qld-price 85.50 \\
        --gld-price 195.20 \\
        --account 25000 \\
        --initial-buy

    python tools/qld_gld_rebalance.py --dry-run

Output: JSON to stdout.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Strategy constants
# ---------------------------------------------------------------------------

TARGET_QLD_WEIGHT = 0.30
TARGET_GLD_WEIGHT = 0.70
DRIFT_TOLERANCE = 0.05          # ±5 percentage-point band before forced rebalance
ACCOUNT_SIZE_DEFAULT = 25_000.0
STRATEGY_TAG = "lbr_tactical_rotation_qld_gld"

# Risk point for QLD: -15% from cost basis (2x leveraged, tighter stop than standard equity)
QLD_RISK_STOP_PCT = 0.15        # 15% below entry = hard stop for QLD
GLD_RISK_STOP_PCT = 0.08        # 8% below entry = hard stop for GLD (soft commodity stop)


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def compute_target_allocation(account_value: float) -> dict:
    """Return target notional and approx share counts given account_value and live prices."""
    return {
        "QLD": {
            "weight": TARGET_QLD_WEIGHT,
            "target_notional": round(account_value * TARGET_QLD_WEIGHT, 2),
        },
        "GLD": {
            "weight": TARGET_GLD_WEIGHT,
            "target_notional": round(account_value * TARGET_GLD_WEIGHT, 2),
        },
    }


def compute_current_state(
    qld_price: float,
    qld_shares: float,
    gld_price: float,
    gld_shares: float,
) -> dict:
    """Return current market values and weights."""
    qld_value = qld_price * qld_shares
    gld_value = gld_price * gld_shares
    total = qld_value + gld_value
    if total <= 0:
        return {
            "QLD": {"shares": qld_shares, "price": qld_price, "market_value": 0.0, "weight": 0.0},
            "GLD": {"shares": gld_shares, "price": gld_price, "market_value": 0.0, "weight": 0.0},
            "total_portfolio_value": 0.0,
        }
    return {
        "QLD": {
            "shares": qld_shares,
            "price": qld_price,
            "market_value": round(qld_value, 2),
            "weight": round(qld_value / total, 4),
        },
        "GLD": {
            "shares": gld_shares,
            "price": gld_price,
            "market_value": round(gld_value, 2),
            "weight": round(gld_value / total, 4),
        },
        "total_portfolio_value": round(total, 2),
    }


def compute_drift(current_state: dict) -> dict:
    """Return per-symbol drift vs target weights.

    Computes drift from raw market values (not rounded weights) so that drifts
    just above the tolerance boundary are not rounded away before the comparison.
    """
    total = current_state["total_portfolio_value"]
    if total <= 0:
        raw_qld_weight = 0.0
        raw_gld_weight = 0.0
    else:
        raw_qld_weight = current_state["QLD"]["market_value"] / total
        raw_gld_weight = current_state["GLD"]["market_value"] / total
    return {
        "QLD": {
            "drift": round(raw_qld_weight - TARGET_QLD_WEIGHT, 4),
            "exceeds_tolerance": abs(raw_qld_weight - TARGET_QLD_WEIGHT) > DRIFT_TOLERANCE,
        },
        "GLD": {
            "drift": round(raw_gld_weight - TARGET_GLD_WEIGHT, 4),
            "exceeds_tolerance": abs(raw_gld_weight - TARGET_GLD_WEIGHT) > DRIFT_TOLERANCE,
        },
    }


def compute_order_intents(
    current_state: dict,
    account_value: float,
    qld_price: float,
    gld_price: float,
) -> list[dict]:
    """Return order intents (BUY/SELL shares) to restore target weights.

    Uses floor() for BUY and ceil() for SELL to avoid over-buying on rounding.
    Cash residual (unused portion) remains as buffer; do not chase fractional shares.
    """
    target = compute_target_allocation(account_value)

    intents = []
    for symbol, price, current_shares_key in [
        ("QLD", qld_price, "QLD"),
        ("GLD", gld_price, "GLD"),
    ]:
        target_notional = target[symbol]["target_notional"]
        target_shares_raw = target_notional / price
        target_shares = math.floor(target_shares_raw)
        # round() before int() guards against floating-point representation of whole numbers
        # e.g. 87.9999999 → 88, not 87
        current_shares = int(round(current_state[symbol]["shares"]))

        delta = target_shares - current_shares
        if delta == 0:
            side = "HOLD"
            quantity = 0
        elif delta > 0:
            side = "BUY"
            quantity = int(delta)
        else:
            side = "SELL"
            quantity = int(abs(delta))

        risk_stop_pct = QLD_RISK_STOP_PCT if symbol == "QLD" else GLD_RISK_STOP_PCT
        risk_point = round(price * (1 - risk_stop_pct), 2)

        intents.append({
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price_ref": price,
            "target_shares": target_shares,
            "current_shares": current_shares,
            "estimated_notional": round(quantity * price, 2),
            "risk_point": risk_point,
            "risk_stop_pct": risk_stop_pct,
        })

    return intents


def build_rebalance_report(
    qld_price: float,
    gld_price: float,
    qld_shares: float = 0.0,
    gld_shares: float = 0.0,
    account_value: Optional[float] = None,
    as_of: Optional[str] = None,
) -> dict:
    """Build the full rebalance report: state, drift, and order intents."""
    if account_value is None:
        account_value = ACCOUNT_SIZE_DEFAULT

    if as_of is None:
        as_of = datetime.now(timezone.utc).isoformat()

    current_state = compute_current_state(qld_price, qld_shares, gld_price, gld_shares)
    drift = compute_drift(current_state)
    order_intents = compute_order_intents(current_state, account_value, qld_price, gld_price)

    any_drift_exceeded = any(v["exceeds_tolerance"] for v in drift.values())
    rebalance_needed = any_drift_exceeded  # monthly is always needed on schedule

    total_estimated_notional = sum(i["estimated_notional"] for i in order_intents)

    return {
        "as_of": as_of,
        "strategy": STRATEGY_TAG,
        "account_value": account_value,
        "rebalance_needed": rebalance_needed,
        "drift_tolerance_pct": DRIFT_TOLERANCE * 100,
        "current_state": current_state,
        "drift": drift,
        "order_intents": order_intents,
        "total_estimated_notional": round(total_estimated_notional, 2),
        "risk_warnings": [
            "QLD is a 2x daily-leveraged ETF — do NOT average down into losses.",
            "Monthly rebalance on the first trading day of the calendar month.",
            f"QLD hard stop: {QLD_RISK_STOP_PCT*100:.0f}% below entry price.",
            f"GLD hard stop: {GLD_RISK_STOP_PCT*100:.0f}% below entry price.",
            "If data or order routing fails, hold existing positions and retry next business day.",
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="30% QLD / 70% GLD monthly rebalance calculator.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full rebalance with existing positions:
  python tools/qld_gld_rebalance.py \\
      --qld-price 85.50 --qld-shares 87 \\
      --gld-price 195.20 --gld-shares 90

  # Initial buy (no existing positions):
  python tools/qld_gld_rebalance.py \\
      --qld-price 85.50 --gld-price 195.20 --initial-buy

  # Dry run with placeholder prices:
  python tools/qld_gld_rebalance.py --dry-run
        """,
    )
    parser.add_argument("--qld-price", type=float, help="Current QLD price (USD)")
    parser.add_argument("--qld-shares", type=float, default=0.0, help="Current QLD shares held")
    parser.add_argument("--gld-price", type=float, help="Current GLD price (USD)")
    parser.add_argument("--gld-shares", type=float, default=0.0, help="Current GLD shares held")
    parser.add_argument(
        "--account",
        type=float,
        default=ACCOUNT_SIZE_DEFAULT,
        help=f"Total account value in USD (default: {ACCOUNT_SIZE_DEFAULT:,.0f})",
    )
    parser.add_argument(
        "--initial-buy",
        action="store_true",
        help="Shorthand for --qld-shares 0 --gld-shares 0 (compute initial entry orders)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run with placeholder prices (85.00 / 195.00) for testing",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.dry_run:
        qld_price = 85.00
        gld_price = 195.00
        qld_shares = 0.0
        gld_shares = 0.0
    else:
        if args.qld_price is None or args.gld_price is None:
            print(
                json.dumps({"error": "--qld-price and --gld-price are required unless --dry-run"}),
                file=sys.stderr,
            )
            sys.exit(1)
        qld_price = args.qld_price
        gld_price = args.gld_price
        qld_shares = 0.0 if args.initial_buy else args.qld_shares
        gld_shares = 0.0 if args.initial_buy else args.gld_shares

    report = build_rebalance_report(
        qld_price=qld_price,
        gld_price=gld_price,
        qld_shares=qld_shares,
        gld_shares=gld_shares,
        account_value=args.account,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
