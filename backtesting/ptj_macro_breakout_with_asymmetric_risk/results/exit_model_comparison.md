# Exit Model Comparison: PTJ Macro Breakout

**Run date:** 2026-06-22 16:16 UTC
**Backtest period:** 2015-01-01 – 2025-06-01  (walk-forward, entry at next bar open)
**Signal engine:** identical across all models (same entries, different exits)

## Exit Models

| Model | Description |
|-------|-------------|
| **Baseline** | Fixed 20-bar time-stop, structural stop, 2.5R partial profit (1/3 off → stop to breakeven) |
| **Trailing** | Trail stop to lowest low of last 3 bars (for longs) once +1R in profit; 40-bar safety stop if trailing never activates |
| **Event-Risk** | Same as baseline + halve size 1 bar before FOMC/CPI/NFP; exit if >1.5R adverse on event day |

---

## Side-by-Side: All Trades (Combined Long + Short)

| Metric | Baseline | Trailing | Event-Risk |
|--------|----------|----------|------------|
| Total trades | 26 | 26 | 26 |
| Win rate | 57.7 | 53.8 | 57.7 |
| Avg return % | -0.376 | -0.415 | -0.376 |
| Median return % | +0.073 | +0.038 | +0.073 |
| Best trade % | +3.247 | +2.320 | +3.247 |
| Worst trade % | -5.967 | -5.967 | -5.967 |
| Avg bars held | 6.7 | 6.1 | 6.7 |
| Sharpe (annualised) | -5.429 | -5.332 | -5.429 |
| Max drawdown % | -6.318 | -5.315 | -5.772 |

---

## Long Trades

| Metric | Baseline | Trailing | Event-Risk |
|--------|----------|----------|------------|
| Total trades | 12 | 12 | 12 |
| Win rate | 83.3 | 75.0 | 83.3 |
| Avg return % | +0.572 | +0.221 | +0.572 |
| Avg bars held | 8.2 | 7.2 | 8.2 |
| Sharpe (annualised) | 0.128 | -1.415 | 0.128 |
| Max drawdown % | -0.723 | -1.064 | -0.723 |

---

## Short Trades

| Metric | Baseline | Trailing | Event-Risk |
|--------|----------|----------|------------|
| Total trades | 14 | 14 | 14 |
| Win rate | 35.7 | 35.7 | 35.7 |
| Avg return % | -1.188 | -0.959 | -1.188 |
| Avg bars held | 5.3 | 5.2 | 5.3 |
| Sharpe (annualised) | -8.455 | -7.588 | -8.455 |
| Max drawdown % | -6.318 | -5.315 | -5.772 |

---

## In-Sample (2015–2019) vs Out-of-Sample (2020–2025)

### 2015–2019 (IS) — Long

| Metric | Baseline | Trailing | Event-Risk |
|--------|----------|----------|------------|
| Total trades | 3 | 3 | 3 |
| Win rate | 100.0 | 100.0 | 100.0 |
| Avg return % | +0.739 | +0.739 | +0.739 |
| Avg bars held | 8.3 | 8.3 | 8.3 |
| Sharpe | 28.187 | 28.187 | 28.187 |
| Max drawdown % | 0.000 | 0.000 | 0.000 |

### 2015–2019 (IS) — Short

| Metric | Baseline | Trailing | Event-Risk |
|--------|----------|----------|------------|
| Total trades | 2 | 2 | 2 |
| Win rate | 50.0 | 50.0 | 50.0 |
| Avg return % | +1.029 | +1.029 | +1.029 |
| Avg bars held | 5.5 | 5.5 | 5.5 |
| Sharpe | -0.948 | -0.948 | -0.948 |
| Max drawdown % | -0.086 | -0.086 | -0.086 |

### 2020–2025 (OOS) — Long

| Metric | Baseline | Trailing | Event-Risk |
|--------|----------|----------|------------|
| Total trades | 9 | 9 | 9 |
| Win rate | 77.8 | 66.7 | 77.8 |
| Avg return % | +0.516 | +0.048 | +0.516 |
| Avg bars held | 8.2 | 6.8 | 8.2 |
| Sharpe | -1.674 | -3.615 | -1.674 |
| Max drawdown % | -0.723 | -1.064 | -0.723 |

### 2020–2025 (OOS) — Short

| Metric | Baseline | Trailing | Event-Risk |
|--------|----------|----------|------------|
| Total trades | 12 | 12 | 12 |
| Win rate | 33.3 | 33.3 | 33.3 |
| Avg return % | -1.558 | -1.291 | -1.558 |
| Avg bars held | 5.2 | 5.2 | 5.2 |
| Sharpe | -9.412 | -8.402 | -9.412 |
| Max drawdown % | -6.318 | -5.315 | -5.772 |

---

## Exit Reason Breakdown

### Baseline (20-bar stop + 2.5R partial)

| Exit Reason | Count |
|-------------|-------|
| `failed_breakout_long` | 9 |
| `failed_breakout_short` | 8 |
| `stop_hit_short` | 6 |
| `partial_profit_long` | 2 |
| `time_stop` | 2 |
| `partial_profit_short` | 1 |
| `stop_hit_long` | 1 |

### Trailing (3-bar low trail from +1R)

| Exit Reason | Count |
|-------------|-------|
| `failed_breakout_long` | 8 |
| `stop_hit_short` | 7 |
| `failed_breakout_short` | 7 |
| `stop_hit_long` | 4 |

### Event-Risk (halve before FOMC/CPI/NFP)

| Exit Reason | Count |
|-------------|-------|
| `failed_breakout_long` | 9 |
| `failed_breakout_short` | 8 |
| `stop_hit_short` | 6 |
| `partial_profit_long` | 2 |
| `time_stop` | 2 |
| `partial_profit_short` | 1 |
| `stop_hit_long` | 1 |

---

## Methodology Notes

- **Signal engine:** same `calculate_indicators` / `evaluate_breakout_signal` across all models
- **Entry logic:** identical — entry at next bar open after trigger; gap exclusion applied
- **Slippage:** 0.05% each way on all entries and exits
- **Trailing model:** 3-bar structural trail activates once position reaches +1R profit; 
  stop never trails below entry (breakeven floor); 40-bar safety stop if trailing never activates
- **Event-risk model:** FOMC/CPI/NFP dates are known in advance (pre-announced); 
  position halved the bar before the event, restored after; exits if >1.5R adverse on event day
- **IS/OOS cutoff:** 2019-12-31

