# Backtest Results: PTJ Swing Framework (6-Symbol Portfolio)

**Run date:** 2026-06-23 13:18 UTC
**Backtest period:** 2015-01-01 – 2025-06-01  (walk-forward, entry at next bar open)
**Universe:** SPY, QQQ, IWM, GLD, XLF, XLE + macro bars (TLT, UUP, HYG)
**Account:** $25,000 starting equity | Risk per trade: 1% ($250)
**Slippage:** 0.05% each way | **Max hold:** 20 bars | **Partial profit at:** 2.5R
**Portfolio constraints:** Max 6 positions | Max 2 equity-cluster | 7-day trade cooldown

---

## Portfolio Performance

- **Starting equity:** $25,000
- **Ending equity:** $21,190.91
- **Total return:** -15.2%
- **Max portfolio drawdown:** 20.6%

## All Trades Combined

- **Total closed trades:** 381
- **Win rate:** 33.1%
- **Average return:** -0.208%
- **Median return:** -0.420%
- **Best trade:** 43.500%
- **Worst trade:** -15.122%
- **Sharpe ratio (annualised):** -0.040

## By Direction

| Direction | Trades | Win Rate | Avg Return | Sharpe |
|-----------|--------|----------|------------|--------|
| Long  | 142 | 38.0% | -0.332% | -0.132 |
| Short | 239 | 30.1% | -0.134% | -0.029 |

## In-Sample (2015–2019) vs Out-of-Sample (2020–2025)

| Period | Trades | Win Rate | Avg Return | Sharpe |
|--------|--------|----------|------------|--------|
| 2015–2019 (IS) | 150 | 34.0% | -0.239% | -0.110 |
| 2020–2025 (OOS) | 231 | 32.5% | -0.187% | -0.040 |

## Per-Symbol Statistics

| Symbol | Trades | Win Rate | Avg Return | Sharpe |
|--------|--------|----------|------------|--------|
| SPY | 19 | 36.8% | 0.757% | 0.734 |
| QQQ | 36 | 27.8% | -0.133% | -0.097 |
| IWM | 50 | 26.0% | -0.598% | -0.240 |
| GLD | 138 | 36.2% | -0.245% | -0.145 |
| XLF | 66 | 30.3% | -0.331% | -0.168 |
| XLE | 72 | 36.1% | -0.045% | -0.013 |

## Backtest Pitfalls Checklist

- [x] No look-ahead bias (entry at next bar open; indicators on bars[0..d] only)
- [x] Adjusted close prices (dividends + splits via Yahoo Finance)
- [x] No parameter optimisation (strategy defaults only from detect_macro_breakout.py)
- [x] Slippage included (0.05% each way on both entry and exit)
- [x] Gap exclusion enforced (built into evaluate_breakout_signal)
- [x] Partial profit at 2.5R reduces position, trails stop to breakeven
- [x] Fixed time-stop at 20 bars prevents indefinite drawdown
- [x] Portfolio constraints: max 6 positions, max 2 equity-cluster, 7-day cooldown
- [x] In-sample / out-of-sample split reported
- [x] $25k account sizing with 1% risk per trade (max $250 risk, max 20% notional)
- [x] Correlation-aware: max 2 positions from SPY/QQQ/IWM cluster

## Decision Rule

If backtest does not demonstrate net positive expectancy on the OOS period, the
daily loop emits SKIP/RESEARCH for all symbols rather than forcing trades.
The strategy must earn its risk allocation; edge cannot be assumed.
