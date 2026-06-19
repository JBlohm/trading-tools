# Backtest Results: PTJ Crash Playbook / Liquidity Breakdown

**Run date:** 2026-06-19 09:54 UTC
**Backtest period:** 2015-01-01 – 2025-06-01  (walk-forward, entry at next bar open)
**Instruments:** SPY (primary), ^VIX, HYG (credit), RSP (breadth)
**Parameters:** strategy defaults — shelf_lookback=63, break_threshold=0.005
**Slippage:** 0.05% each way | **Position size:** 0.33 unit | **Max hold:** 20 bars

---

## Signal Frequency

| State | Count | % of Bars |
|-------|-------|-----------|
| `watchlist_deterioration` | 1470 | 62.3% |
| `no_setup` | 568 | 24.1% |
| `setup_armed` | 255 | 10.8% |
| `manage_open_short` | 47 | 2.0% |
| `entry_trigger_short` | 10 | 0.4% |
| `de_risk_exit` | 8 | 0.3% |

*Total bars evaluated: 2358*

---

## Forward Returns After `entry_trigger_short`

*(Returns are positive when price falls — short profits)*

| Horizon | N | Mean | Median | Std | Win >5% | Loss >5% | Best | Worst |
|---------|---|------|--------|-----|---------|----------|------|-------|
| 5d | 10 | 1.041% | 1.867% | 2.61% | 0.0% | 10.0% | 4.066% | -5.168% |
| 10d | 10 | -0.41% | -0.468% | 3.542% | 0.0% | 10.0% | 4.664% | -9.187% |
| 20d | 10 | -1.305% | -1.044% | 5.3% | 10.0% | 30.0% | 8.546% | -11.021% |

---

## Trade-Level Statistics

- **Total trades:** 10
- **Win rate:** 40.0% (positive P&L)
- **Average return:** 0.005%
- **Median return:** -0.902%
- **Best trade:** 7.942%
- **Worst trade:** -2.999%
- **Average hold:** 9.6 bars
- **Sharpe ratio (annualised):** -6.76
- **Max drawdown:** -2.417%

---

## Known Crash Episode Coverage

| Episode | Period | Entry Trigger Days | Setup Armed Days | Stress Detected? | Entry Triggered? |
|---------|--------|-------------------|-----------------|-----------------|-----------------|
| 2018-Q4 Selloff | 2018-10-01 – 2018-12-24 | 2 | 32 | YES | YES |
| 2020 COVID Crash | 2020-02-19 – 2020-03-23 | 0 | 19 | YES | NO |
| 2022 Bear Market | 2022-01-04 – 2022-10-13 | 4 | 69 | YES | YES |

> **Note on stress-only episodes:** A `setup_armed` reading means liquidity stress was detected, but it is not a tradable entry by itself. The strategy requires a failed retest and an `entry_trigger_short`; stress-only episodes should be treated as watchlist/triage signals, not completed short setups.

---

## Pitfalls Checklist

- [x] No look-ahead bias (entry at next bar open, no future features)
- [x] Adjusted close prices (dividends + splits)
- [x] No parameter optimisation (defaults only)
- [x] Slippage included (0.05% each way)
- [x] Gap-down open exclusion enforced
- [x] Time-stop prevents indefinite drawdown
- [x] Stop at support shelf level (per strategy rules)

## Files in This Folder

| File | Description |
|------|-------------|
| `backtest_ptj_crash.py` | Backtest engine |
| `SMART_GOAL.md` | SMART goal definition |
| `results/signal_history.csv` | Daily signal state |
| `results/trades.csv` | Trade-level results |
| `results/forward_returns.csv` | Forward returns per entry trigger |
| `results/performance_summary.md` | This file |
