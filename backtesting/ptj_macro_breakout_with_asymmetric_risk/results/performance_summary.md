# Backtest Results: PTJ Macro Breakout With Asymmetric Risk

**Run date:** 2026-06-21 18:09 UTC
**Backtest period:** 2015-01-01 – 2025-06-01  (walk-forward, entry at next bar open)
**Instruments:** SPY (primary), TLT (rates), UUP (dollar), HYG (credit)
**Slippage:** 0.05% each way | **Position size:** 0.33 unit | **Max hold:** 20 bars | **Partial profit at:** 2.5R

---

## Signal Frequency

| State | Count | % of Bars |
|-------|-------|-----------|
| `breakout_candidate` | 1750 | 73.0% |
| `no_setup` | 284 | 11.8% |
| `range_forming` | 248 | 10.3% |
| `add_unit` | 41 | 1.7% |
| `entry_trigger_short` | 28 | 1.2% |
| `exit_signal` | 24 | 1.0% |
| `entry_trigger_long` | 13 | 0.5% |
| `trail_stop` | 10 | 0.4% |

*Total bars evaluated: 2398*

---

## Forward Returns After Entry Triggers

*(Returns adjusted for direction: positive = profitable outcome)*

### Long (Bullish Breakout)

| Horizon | N | Mean | Median | Std | Win >3% | Loss >3% | Best | Worst |
|---------|---|------|--------|-----|---------|----------|------|-------|
| 5d | 12 | -0.313% | 0.072% | 1.751% | 0.0% | 8.3% | 1.572% | -4.997% |
| 10d | 12 | -0.222% | 0.368% | 1.996% | 0.0% | 16.7% | 2.454% | -4.357% |
| 20d | 11 | 0.228% | 1.724% | 3.103% | 18.2% | 18.2% | 3.7% | -5.693% |

### Short (Bearish Breakdown)

| Horizon | N | Mean | Median | Std | Win >3% | Loss >3% | Best | Worst |
|---------|---|------|--------|-----|---------|----------|------|-------|
| 5d | 14 | -0.083% | -0.25% | 1.881% | 14.3% | 7.1% | 3.935% | -4.414% |
| 10d | 14 | -1.223% | -1.577% | 2.234% | 7.1% | 21.4% | 3.472% | -5.161% |
| 20d | 14 | 0.57% | 0.615% | 4.975% | 21.4% | 21.4% | 10.424% | -10.362% |

---

## Trade Statistics

### Long Trades

- **Total trades:** 12
- **Win rate:** 83.3%
- **Average return:** 0.572%
- **Median return:** 0.282%
- **Best trade:** 3.247%
- **Worst trade:** -2.191%
- **Average hold:** 8.2 bars
- **Sharpe ratio (annualised):** 0.128
- **Max drawdown:** -0.723%

### Short Trades

- **Total trades:** 14
- **Win rate:** 35.7%
- **Average return:** -1.188%
- **Median return:** -0.35%
- **Best trade:** 2.32%
- **Worst trade:** -5.967%
- **Average hold:** 5.3 bars
- **Sharpe ratio (annualised):** -8.455
- **Max drawdown:** -6.318%

### All Full Trades (Combined)

- **Total trades:** 26
- **Win rate:** 57.7%
- **Average return:** -0.376%
- **Median return:** 0.073%
- **Best trade:** 3.247%
- **Worst trade:** -5.967%
- **Average hold:** 6.7 bars
- **Sharpe ratio (annualised):** -5.429
- **Max drawdown:** -6.318%

---

## In-Sample (2015–2019) vs Out-of-Sample (2020–2025) Split

| Period | Side | Trades | Win Rate | Avg Return | Sharpe |
|--------|------|--------|----------|------------|--------|
| 2015–2019 (IS) | long | 3 | 100.0% | 0.739% | 28.179 |
| 2015–2019 (IS) | short | 2 | 50.0% | 1.029% | -0.948 |
| 2020–2025 (OOS) | long | 9 | 77.8% | 0.516% | -1.674 |
| 2020–2025 (OOS) | short | 12 | 33.3% | -1.558% | -9.412 |

---

## Known Episode Coverage

### Long Breakout Episodes (Bullish)

| Episode | Period | Entry Triggers | Candidates | Stress? | Entry Triggered? |
|---------|--------|----------------|-----------|---------|-----------------|
| 2016-Q4 Trump Rally | 2016-11-01 – 2016-12-31 | 1 | 36 | YES | YES |
| 2019-Q1 Fed Pivot Breakout | 2019-01-01 – 2019-03-31 | 0 | 40 | YES | NO |
| 2023-Q1 Bear Recovery | 2023-01-01 – 2023-03-31 | 1 | 48 | YES | YES |

### Short Breakdown Episodes (Bearish)

| Episode | Period | Entry Triggers | Candidates | Stress? | Entry Triggered? |
|---------|--------|----------------|-----------|---------|-----------------|
| 2018-Q4 Selloff | 2018-10-01 – 2018-12-24 | 3 | 32 | YES | YES |
| 2020-Q1 COVID | 2020-02-19 – 2020-03-23 | 2 | 6 | YES | YES |
| 2022-Q1 Bear Onset | 2022-01-04 – 2022-04-30 | 2 | 40 | YES | YES |

---

## Pitfalls Checklist

- [x] No look-ahead bias (entry at next bar open; indicators on bars[0..d] only)
- [x] Adjusted close prices (dividends + splits via Yahoo Finance)
- [x] No parameter optimisation (strategy defaults only)
- [x] Slippage included (0.05% each way on both entry and exit)
- [x] Gap exclusion enforced (no long on gap-up; no short on gap-down)
- [x] Partial profit at 2.5R reduces position, trails stop to breakeven
- [x] Fixed time-stop at 20 bars prevents indefinite drawdown
- [x] Both long and short directions evaluated separately
- [x] In-sample / out-of-sample split reported
- [x] Known macro episodes checked for signal coverage

## Files in This Folder

| File | Description |
|------|-------------|
| `backtest_ptj_macro_breakout.py` | Backtest engine |
| `SMART_GOAL.md` | SMART goal definition |
| `results/signal_history.csv` | Daily signal state |
| `results/trades.csv` | Trade-level results |
| `results/forward_returns.csv` | Forward returns per entry trigger |
| `results/performance_summary.md` | This file |
