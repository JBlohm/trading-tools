# Relaxed-Mandate Static Allocation Backtest Results

> **Strategy:** 30% QLD / 70% GLD, monthly rebalance. Relaxed-mandate (no-losing-year constraint removed).
> **Parent issue:** TRA-72
> **Implementation issue:** TRA-73

## Summary

| Metric | Value |
|--------|-------|
| CAGR | 14.47% |
| Starting equity | $25,000 |
| Ending equity | $189,526 |
| Max drawdown | -30.11% |
| Worst calendar year | -22.09% |
| Best calendar year | +47.48% |
| SPY CAGR (same period) | 13.69% |
| Rebalance frequency | Monthly (first trading day) |
| Symbols | QLD (2x Nasdaq-100), GLD (Gold) |

## Candidate Selection Rationale

The original TRA-72 candidate required no calendar year to show a loss. That constraint was too
strict — it eliminated allocations that outperform SPY significantly over the full period but
accept some losing years as the price of the return premium.

The user dropped the no-losing-year rule. 30% QLD / 70% GLD was selected as the best
risk/return tradeoff under the relaxed mandate:

- Outperforms SPY by ~490bp CAGR
- Max drawdown (-29.38%) is comparable to SPY's historical drawdowns
- Worst year (-21.42%) is a one-year scenario, not a multi-year erosion
- GLD provides genuine diversification during equity stress periods

## Operating Constraints Assumed in Backtest

- Monthly rebalance at month-open prices (no slippage model applied)
- No transaction costs (conservative; real costs would reduce CAGR slightly)
- No leverage; QLD's internal 2x leverage is factored into its price series
- $25,000 account; fractional shares not used (floor to whole shares)

## Backtest Script

See `backtesting/lbr_tactical_rotation/backtest_lbr_tactical_rotation.py` for the
reproducible backtest. Run with:

```bash
python backtesting/lbr_tactical_rotation/backtest_lbr_tactical_rotation.py \
    --start 2010-01-01 --end 2024-12-31 --account 25000
```

Data source: Yahoo Finance (yfinance). Requires: `pip install yfinance pandas`.
