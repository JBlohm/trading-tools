#!/usr/bin/env python3
"""
backtest_lbr_tactical_rotation.py — Backtest for 30% QLD / 70% GLD monthly rebalance.

Downloads daily price data from Yahoo Finance and simulates a monthly-rebalanced
fixed-weight portfolio.

Usage:
    python backtest_lbr_tactical_rotation.py --start 2010-01-01 --end 2024-12-31
    python backtest_lbr_tactical_rotation.py --start 2010-01-01 --end 2024-12-31 --account 25000

Outputs: performance summary to stdout as JSON.
Requires: yfinance, pandas
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime
from typing import Optional

try:
    import pandas as pd
    import yfinance as yf
except ImportError:
    print(
        json.dumps({
            "error": "Missing dependencies. Run: pip install yfinance pandas",
            "status": "dependency_missing",
        }),
        file=sys.stderr,
    )
    sys.exit(1)

SYMBOLS = ["QLD", "GLD", "SPY"]
TARGET_WEIGHTS = {"QLD": 0.30, "GLD": 0.70}


def fetch_prices(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    raw = yf.download(symbols, start=start, end=end, auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"]
    else:
        close = raw[["Close"]].rename(columns={"Close": symbols[0]})
    close = close.dropna()
    return close


def run_backtest(
    prices: pd.DataFrame,
    account: float = 25_000.0,
) -> dict:
    monthly_starts = (
        prices.index.to_series()
        .groupby([prices.index.year, prices.index.month])
        .first()
        .values
    )

    portfolio_value = account
    shares: dict[str, float] = {s: 0.0 for s in TARGET_WEIGHTS}
    daily_values: list[dict] = []
    rebalances: list[dict] = []

    rebalance_dates = set(pd.Timestamp(d) for d in monthly_starts)

    for ts in prices.index:
        row = prices.loc[ts]
        current_value = sum(shares[s] * row[s] for s in TARGET_WEIGHTS)
        if daily_values:
            cash = portfolio_value - sum(shares[s] * prices.loc[daily_values[-1]["date"]][s] for s in TARGET_WEIGHTS)
        else:
            cash = account
        portfolio_value = current_value + cash

        if ts in rebalance_dates:
            new_shares = {
                s: math.floor(portfolio_value * TARGET_WEIGHTS[s] / row[s])
                for s in TARGET_WEIGHTS
            }
            rebalances.append({
                "date": ts.date().isoformat(),
                "portfolio_value": round(portfolio_value, 2),
                "shares": {s: new_shares[s] for s in TARGET_WEIGHTS},
            })
            shares = new_shares
            current_value = sum(shares[s] * row[s] for s in TARGET_WEIGHTS)

        daily_values.append({
            "date": ts,
            "portfolio_value": round(portfolio_value, 2),
        })

    equity_series = pd.Series(
        [d["portfolio_value"] for d in daily_values],
        index=[d["date"] for d in daily_values],
    )

    peak = equity_series.cummax()
    drawdown = (equity_series - peak) / peak
    max_drawdown = float(drawdown.min())

    years = equity_series.resample("YE").last()
    annual_returns = years.pct_change().dropna()
    worst_year = float(annual_returns.min()) if len(annual_returns) else 0.0
    best_year = float(annual_returns.max()) if len(annual_returns) else 0.0

    n_years = (prices.index[-1] - prices.index[0]).days / 365.25
    ending_equity = equity_series.iloc[-1]
    cagr = (ending_equity / account) ** (1 / n_years) - 1 if n_years > 0 else 0.0

    spy_start = prices["SPY"].iloc[0]
    spy_end = prices["SPY"].iloc[-1]
    spy_cagr = (spy_end / spy_start) ** (1 / n_years) - 1 if n_years > 0 else 0.0

    return {
        "strategy": "lbr_tactical_rotation_qld_gld",
        "weights": TARGET_WEIGHTS,
        "start_date": prices.index[0].date().isoformat(),
        "end_date": prices.index[-1].date().isoformat(),
        "starting_equity": account,
        "ending_equity": round(ending_equity, 2),
        "cagr_pct": round(cagr * 100, 2),
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "worst_year_pct": round(worst_year * 100, 2),
        "best_year_pct": round(best_year * 100, 2),
        "spy_cagr_pct": round(spy_cagr * 100, 2),
        "n_rebalances": len(rebalances),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest 30% QLD / 70% GLD monthly rebalance portfolio.",
    )
    parser.add_argument("--start", default="2010-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=date.today().isoformat(), help="End date YYYY-MM-DD")
    parser.add_argument("--account", type=float, default=25_000.0, help="Starting account value")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    prices = fetch_prices(SYMBOLS, args.start, args.end)
    result = run_backtest(prices, account=args.account)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
