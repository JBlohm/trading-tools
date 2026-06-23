# PTJ Macro Breakout: Short Signals — Alternative Instruments Comparison

**Run date:** 2026-06-23 06:22 UTC
**Backtest period:** 2015-01-01 – 2025-06-01 (walk-forward, entry at next bar open)
**Signal source:** `detect_macro_breakout.py` on SPY (same as TRA-61 baseline)
**Execution:** Each instrument is traded in the direction noted below for the same holding periods as the SPY short signal would dictate.
**Slippage:** 0.05% each way | **Position size:** 0.33 unit | **Max hold:** 20 bars

---

## Baseline: SPY Short Performance (TRA-61)

- **Short trades:** 14
- **Win rate:** 35.7%
- **Avg return:** -1.188%
- **Conclusion from TRA-61:** SPY shorts structurally disadvantaged by long-term bull bias.

---

## Instrument Comparison Results

*(Ranked by Sharpe ratio)*

| # | Instrument | Type | N | Win Rate | Avg Return | Sharpe | Max DD | Avg Hold |
|---|------------|------|---|----------|------------|--------|--------|----------|
| 1 | `XLV` | sector_defensive | 14 | 57.1% | 0.734% | 6.366 | -0.944% | 5.3 |
| 2 | `XLU` | sector_defensive | 14 | 78.6% | 0.54% | 4.816 | -3.009% | 5.3 |
| 3 | `TLT_short` | rates | 14 | 50.0% | -0.036% | 2.847 | -1.277% | 5.3 |
| 4 | `Sector_Rotation` | spread | 14 | 42.9% | -0.245% | -1.130 | -2.54% | 5.3 |
| 5 | `XLY_short` | sector_cyclical | 14 | 42.9% | -1.128% | -5.817 | -6.901% | 5.3 |
| 6 | `SH` | inverse_etf | 14 | 35.7% | -1.116% | -8.257 | -6.064% | 5.3 |
| 7 | `HYG_short` | credit | 14 | 42.9% | -0.52% | -9.146 | -2.421% | 5.3 |
| 8 | `VXX` | volatility | 13 | 30.8% | -4.977% | -13.036 | -22.976% | 5.6 |

---

## Instrument Detail

### SH
*ProShares Short S&P500 (long SH = short SPY exposure, no decay)*  
Direction: **long**

- Total trades: 14
- Win rate: 35.7%
- Avg return: -1.116%
- Median return: -0.376%
- Best / Worst: 2.305% / -5.717%
- Sharpe (annualised): -8.257
- Max drawdown: -6.064%
- Avg hold: 5.3 bars

### TLT_short
*20Y Treasury ETF short (profits when yields rise / risk-off rate move)*  
Direction: **short**

- Total trades: 14
- Win rate: 50.0%
- Avg return: -0.036%
- Median return: 0.058%
- Best / Worst: 2.439% / -3.186%
- Sharpe (annualised): 2.847
- Max drawdown: -1.277%
- Avg hold: 5.3 bars

### HYG_short
*High-Yield Bond ETF short (profits from credit spread widening)*  
Direction: **short**

- Total trades: 14
- Win rate: 42.9%
- Avg return: -0.52%
- Median return: -0.147%
- Best / Worst: 0.513% / -2.621%
- Sharpe (annualised): -9.146
- Max drawdown: -2.421%
- Avg hold: 5.3 bars

### VXX
*VIX Short-Term Futures ETP long (decay-adjusted; see module docstring)*  
Direction: **long**

- Total trades: 13
- Win rate: 30.8%
- Avg return: -4.977%
- Median return: -4.962%
- Best / Worst: 12.432% / -22.429%
- Sharpe (annualised): -13.036
- Max drawdown: -22.976%
- Avg hold: 5.6 bars

**VXX Decay Adjustment (informational):**
- Decay-adj avg return: -7.224%
- Decay-adj win rate: 15.4%
- Note: Informational: subtracts estimated roll decay from raw VXX returns

### XLU
*Utilities ETF long (defensive sector outperforms in risk-off)*  
Direction: **long**

- Total trades: 14
- Win rate: 78.6%
- Avg return: 0.54%
- Median return: 0.883%
- Best / Worst: 4.407% / -6.298%
- Sharpe (annualised): 4.816
- Max drawdown: -3.009%
- Avg hold: 5.3 bars

### XLV
*Health Care ETF long (defensive sector outperforms in risk-off)*  
Direction: **long**

- Total trades: 14
- Win rate: 57.1%
- Avg return: 0.734%
- Median return: 0.328%
- Best / Worst: 6.952% / -2.686%
- Sharpe (annualised): 6.366
- Max drawdown: -0.944%
- Avg hold: 5.3 bars

### XLY_short
*Consumer Discretionary ETF short (cyclical underperforms in risk-off)*  
Direction: **short**

- Total trades: 14
- Win rate: 42.9%
- Avg return: -1.128%
- Median return: -0.728%
- Best / Worst: 3.481% / -6.359%
- Sharpe (annualised): -5.817
- Max drawdown: -6.901%
- Avg hold: 5.3 bars

### Sector_Rotation
*Sector spread*  
Direction: **spread**

- Total trades: 14
- Win rate: 42.9%
- Avg return: -0.245%
- Median return: -0.346%
- Best / Worst: 2.031% / -2.64%
- Sharpe (annualised): -1.13
- Max drawdown: -2.54%
- Avg hold: 5.3 bars

---

## Episode Coverage

Signal detection on SPY — did the signal fire during each macro breakdown?

| Episode | Period | Entry Triggers | Candidates | Triggered? | Trades Executed |
|---------|--------|----------------|-----------|------------|----------------|
| 2018-Q4 Selloff | 2018-10-01 – 2018-12-24 | 3 | 32 | YES | 1 |
| 2020-Q1 COVID | 2020-02-19 – 2020-03-23 | 2 | 6 | YES | 0 |
| 2022-Q1 Bear Onset | 2022-01-04 – 2022-04-30 | 2 | 40 | YES | 1 |

---

## VIX / Volatility ETP Decay Adjustment

VIX ETP products (VXX, UVXY) suffer structural decay from the continuous roll of
front-month VIX futures into next-month contracts. When the VIX term structure is
in contango (normal market conditions), rolling short positions forward incurs a
negative carry cost, reducing the ETP's NAV regardless of VIX spot level.

**Estimated decay rate used:** 0.4% per trading day (≈ 8% over the 20-bar hold period)

**Important:** The raw VXX return figures in this report are sourced from adjusted
historical closing prices and **already embed the realised roll cost**. The
decay-adjusted column subtracts an *additional* theoretical decay amount to show
what alpha (if any) remains above the carry cost. A positive decay-adjusted return
means the trade generated real macro signal alpha beyond the roll yield drag.

**UVXY (2x):** Excluded. The 2x leverage doubles both the volatility spike and the
decay rate, making the payoff profile unsuitable for a directional macro trade where
timing precision is limited.

---

## Recommendations

Based on risk-adjusted returns across the backtest period, the top instruments for executing bearish macro breakout signals are:

1. **XLV** — Health Care ETF long (defensive sector outperforms in risk-off)  Win rate: 57.1%, Avg return: 0.734%, Sharpe: 6.366
2. **XLU** — Utilities ETF long (defensive sector outperforms in risk-off)  Win rate: 78.6%, Avg return: 0.54%, Sharpe: 4.816

---

## Pitfalls Checklist

- [x] No look-ahead bias (entry at next bar open; SPY signals closed-bar only)
- [x] Adjusted close prices for all instruments (dividends + splits via Yahoo Finance)
- [x] Slippage included (0.05% each way on entry and exit)
- [x] VXX decay documented; raw prices embed actual realised roll cost
- [x] Time-stop at 20 bars caps VXX decay exposure per trade
- [x] Episode coverage verified: 2018-Q4, 2020-Q1, 2022-Q1
- [x] Sector rotation spread computed from same-date entry/exit pairs only

