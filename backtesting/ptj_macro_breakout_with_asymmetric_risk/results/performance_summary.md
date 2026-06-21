# Backtest Results: PTJ Macro Breakout With Asymmetric Risk

**Run date:** 2026-06-21 08:43 UTC
**Backtest period:** 2015-01-01 – 2025-06-01  (walk-forward, entry at next bar open)
**Instruments:** SPY (primary), TLT (rates), UUP (dollar), HYG (credit)
**Slippage:** 0.05% each way | **Position size:** 0.33 unit | **Max hold:** 20 bars | **Partial profit at:** 2.5R

---

## Signal Frequency

| State | Count | % of Bars |
|-------|-------|-----------|
| `breakout_candidate` | 1383 | 57.7% |
| `add_unit` | 300 | 12.5% |
| `no_setup` | 256 | 10.7% |
| `trail_stop` | 156 | 6.5% |
| `entry_trigger_long` | 126 | 5.3% |
| `exit_signal` | 124 | 5.2% |
| `entry_trigger_short` | 52 | 2.2% |
| `range_forming` | 1 | 0.0% |

*Total bars evaluated: 2398*

---

## Forward Returns After Entry Triggers

*(Returns adjusted for direction: positive = profitable outcome)*

### Long (Bullish Breakout)

| Horizon | N | Mean | Median | Std | Win >3% | Loss >3% | Best | Worst |
|---------|---|------|--------|-----|---------|----------|------|-------|
| 5d | 104 | 0.347% | 0.466% | 1.247% | 0.0% | 1.9% | 2.769% | -3.633% |
| 10d | 104 | 0.379% | 0.464% | 1.847% | 5.8% | 6.7% | 4.985% | -6.582% |
| 20d | 103 | 0.688% | 1.507% | 3.488% | 23.3% | 12.6% | 8.076% | -13.822% |

### Short (Bearish Breakdown)

| Horizon | N | Mean | Median | Std | Win >3% | Loss >3% | Best | Worst |
|---------|---|------|--------|-----|---------|----------|------|-------|
| 5d | 33 | -0.184% | -0.437% | 2.426% | 9.1% | 9.1% | 7.696% | -6.14% |
| 10d | 33 | -0.639% | -0.984% | 3.54% | 12.1% | 27.3% | 11.923% | -7.04% |
| 20d | 33 | -0.231% | -1.517% | 6.057% | 21.2% | 36.4% | 20.308% | -10.21% |

---

## Trade Statistics

### Long Trades

- **Total trades:** 104
- **Win rate:** 48.1%
- **Average return:** 0.293%
- **Median return:** -0.057%
- **Best trade:** 5.954%
- **Worst trade:** -3.356%
- **Average hold:** 8.0 bars
- **Sharpe ratio (annualised):** -3.764
- **Max drawdown:** -4.426%

### Short Trades

- **Total trades:** 33
- **Win rate:** 27.3%
- **Average return:** -0.593%
- **Median return:** -0.835%
- **Best trade:** 7.942%
- **Worst trade:** -3.921%
- **Average hold:** 4.6 bars
- **Sharpe ratio (annualised):** -11.336
- **Max drawdown:** -7.817%

### All Full Trades (Combined)

- **Total trades:** 137
- **Win rate:** 43.1%
- **Average return:** 0.08%
- **Median return:** -0.207%
- **Best trade:** 7.942%
- **Worst trade:** -3.921%
- **Average hold:** 7.2 bars
- **Sharpe ratio (annualised):** -5.906
- **Max drawdown:** -8.069%

---

## In-Sample (2015–2019) vs Out-of-Sample (2020–2025) Split

| Period | Side | Trades | Win Rate | Avg Return | Sharpe |
|--------|------|--------|----------|------------|--------|
| 2015–2019 (IS) | long | 42 | 45.2% | 0.289% | -2.806 |
| 2015–2019 (IS) | short | 12 | 25.0% | -0.323% | -10.282 |
| 2020–2025 (OOS) | long | 62 | 50.0% | 0.296% | -4.339 |
| 2020–2025 (OOS) | short | 21 | 28.6% | -0.748% | -12.082 |

---

## Known Episode Coverage

### Long Breakout Episodes (Bullish)

| Episode | Period | Entry Triggers | Candidates | Stress? | Entry Triggered? |
|---------|--------|----------------|-----------|---------|-----------------|
| 2016-Q4 Trump Rally | 2016-11-01 – 2016-12-31 | 3 | 22 | YES | YES |
| 2019-Q1 Fed Pivot Breakout | 2019-01-01 – 2019-03-31 | 4 | 27 | YES | YES |
| 2023-Q1 Bear Recovery | 2023-01-01 – 2023-03-31 | 4 | 49 | YES | YES |

### Short Breakdown Episodes (Bearish)

| Episode | Period | Entry Triggers | Candidates | Stress? | Entry Triggered? |
|---------|--------|----------------|-----------|---------|-----------------|
| 2018-Q4 Selloff | 2018-10-01 – 2018-12-24 | 3 | 39 | YES | YES |
| 2020-Q1 COVID | 2020-02-19 – 2020-03-23 | 1 | 3 | YES | YES |
| 2022-Q1 Bear Onset | 2022-01-04 – 2022-04-30 | 3 | 35 | YES | YES |

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
