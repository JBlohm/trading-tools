# Backtest Results: PTJ Macro Breakout With Asymmetric Risk

**Run date:** 2026-06-21 13:18 UTC
**Backtest period:** 2015-01-01 – 2025-06-01  (walk-forward, entry at next bar open)
**Instruments:** SPY (primary), TLT (rates), UUP (dollar), HYG (credit)
**Slippage:** 0.05% each way | **Position size:** 0.33 unit | **Max hold:** 20 bars | **Partial profit at:** 2.5R

---

## Signal Frequency

| State | Count | % of Bars |
|-------|-------|-----------|
| `breakout_candidate` | 1827 | 76.2% |
| `no_setup` | 293 | 12.2% |
| `range_forming` | 250 | 10.4% |
| `entry_trigger_short` | 14 | 0.6% |
| `exit_signal` | 7 | 0.3% |
| `add_unit` | 4 | 0.2% |
| `entry_trigger_long` | 2 | 0.1% |
| `trail_stop` | 1 | 0.0% |

*Total bars evaluated: 2398*

---

## Forward Returns After Entry Triggers

*(Returns adjusted for direction: positive = profitable outcome)*

### Long (Bullish Breakout)

| Horizon | N | Mean | Median | Std | Win >3% | Loss >3% | Best | Worst |
|---------|---|------|--------|-----|---------|----------|------|-------|
| 5d | 2 | 0.559% | 0.559% | 0.007% | 0.0% | 0.0% | 0.567% | 0.552% |
| 10d | 2 | 0.97% | 0.97% | 0.137% | 0.0% | 0.0% | 1.107% | 0.832% |
| 20d | 2 | 1.951% | 1.951% | 0.924% | 0.0% | 0.0% | 2.876% | 1.027% |

### Short (Bearish Breakdown)

| Horizon | N | Mean | Median | Std | Win >3% | Loss >3% | Best | Worst |
|---------|---|------|--------|-----|---------|----------|------|-------|
| 5d | 5 | 0.114% | -0.906% | 1.975% | 20.0% | 0.0% | 3.935% | -1.554% |
| 10d | 5 | -0.972% | -2.448% | 2.703% | 20.0% | 20.0% | 3.472% | -3.826% |
| 20d | 5 | 2.995% | 0.856% | 4.791% | 40.0% | 20.0% | 10.424% | -3.061% |

---

## Trade Statistics

### Long Trades

- **Total trades:** 2
- **Win rate:** 50.0%
- **Average return:** -0.169%
- **Median return:** -0.169%
- **Best trade:** 0.023%
- **Worst trade:** -0.362%
- **Average hold:** 3.5 bars
- **Sharpe ratio (annualised):** -13.403
- **Max drawdown:** -0.119%

### Short Trades

- **Total trades:** 5
- **Win rate:** 20.0%
- **Average return:** -1.522%
- **Median return:** -0.262%
- **Best trade:** 2.32%
- **Worst trade:** -5.967%
- **Average hold:** 4.8 bars
- **Sharpe ratio (annualised):** -11.914
- **Max drawdown:** -3.166%

### All Full Trades (Combined)

- **Total trades:** 7
- **Win rate:** 28.6%
- **Average return:** -1.136%
- **Median return:** -0.262%
- **Best trade:** 2.32%
- **Worst trade:** -5.967%
- **Average hold:** 4.4 bars
- **Sharpe ratio (annualised):** -9.74
- **Max drawdown:** -3.166%

---

## In-Sample (2015–2019) vs Out-of-Sample (2020–2025) Split

| Period | Side | Trades | Win Rate | Avg Return | Sharpe |
|--------|------|--------|----------|------------|--------|
| 2015–2019 (IS) | long | 0 | — | — | — |
| 2015–2019 (IS) | short | 2 | 50.0% | 1.029% | -0.951 |
| 2020–2025 (OOS) | long | 2 | 50.0% | -0.169% | -13.403 |
| 2020–2025 (OOS) | short | 3 | 0.0% | -3.223% | -19.722 |

---

## Known Episode Coverage

### Long Breakout Episodes (Bullish)

| Episode | Period | Entry Triggers | Candidates | Stress? | Entry Triggered? |
|---------|--------|----------------|-----------|---------|-----------------|
| 2016-Q4 Trump Rally | 2016-11-01 – 2016-12-31 | 0 | 41 | YES | NO |
| 2019-Q1 Fed Pivot Breakout | 2019-01-01 – 2019-03-31 | 0 | 40 | YES | NO |
| 2023-Q1 Bear Recovery | 2023-01-01 – 2023-03-31 | 0 | 50 | YES | NO |

### Short Breakdown Episodes (Bearish)

| Episode | Period | Entry Triggers | Candidates | Stress? | Entry Triggered? |
|---------|--------|----------------|-----------|---------|-----------------|
| 2018-Q4 Selloff | 2018-10-01 – 2018-12-24 | 2 | 32 | YES | YES |
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
