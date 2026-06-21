# SMART Goal: PTJ Macro Breakout With Asymmetric Risk Backtest

## Goal Statement

Validate the PTJ Macro Breakout with Asymmetric Risk signal detector across 10 years of
SPY daily data (2015-01-01 through 2025-06-01) to measure signal quality, trade performance,
and correct identification of known macro breakout episodes for both long and short
directions, publishing all results to GitHub by end of this session.

## SMART Breakdown

**Specific**
Run a walk-forward backtest on SPY (primary), TLT (rates proxy), UUP (dollar proxy),
and HYG (credit proxy) daily bars using the `detect_macro_breakout.py` pure functions
(`calculate_indicators` + `evaluate_breakout_signal`). Simulate both long (bullish
breakout) and short (bearish breakdown) trades at 0.33-unit position size, applying
the strategy's own stop, time-stop, and partial-profit exit rules. Report results in
structured CSVs and a summary Markdown.

**Measurable** — The backtest must deliver all of the following:
1. Signal state frequency distribution (how often each state fires)
2. Forward returns at 5, 10, and 20 bars following each entry trigger (long and short)
3. Trade-level results: entry date, entry price, exit date, exit price, exit reason,
   return (%), bars held, direction (long/short)
4. Aggregate metrics separately for long and short trades: win rate, average trade
   return, Sharpe ratio (annualised), max drawdown
5. Episode hit-check — must detect tradable signals (entry_trigger) in at least 5 of
   these 7 known macro breakout periods:
   - Long: 2016-Q4 Trump rally, 2019-Q1 Fed pivot, 2023-Q1 bear-market recovery
   - Short: 2018-Q4 selloff, 2020-Q1 COVID crash, 2022-Q1 bear market onset
   - Bonus: any other strong macro breakout period confirmed by the model

**Achievable**
- Signal detection logic built from strategy document; no prior tool required.
- Historical daily data sourced from Yahoo Finance (SPY, TLT, UUP, HYG), ~10 years.
- No TWS connection required for offline historical backtest.
- pandas + numpy are available for data handling.
- HTTP fallback provided; yfinance optional.

**Relevant**
Validates whether the macro breakout signal logic produces actionable alpha in real
market conditions for BOTH trend directions. Results directly inform whether the
strategy is ready for live paper-trade monitoring and serve as a template for other
PTJ-inspired strategy backtests.

**Time-bound**
Completed and results published to the
`backtesting/ptj_macro_breakout_with_asymmetric_risk/` folder in the GitHub repository
in this session (2026-06-21).

## Common Pitfalls Addressed

| Pitfall | Mitigation |
|---------|-----------|
| Look-ahead bias | Indicators computed only on bars[0..d]; entry at next bar's open |
| Survivorship bias | Adjusted close prices include dividends/splits |
| Overfitting | All parameters are defaults from the strategy spec (no optimisation) |
| Transaction costs | 0.05% slippage each side included in every trade |
| Data snooping | No parameter tuning; in-sample (2015-2019) / OOS (2020-2025) split reported |
| Unrealistic sizing | 0.33 unit per the strategy spec (probe sizing) |
| Gap risk | Gap-up/gap-down exclusion enforced for respective entry direction |
| Direction bias | Both long and short trades evaluated on equal footing |
| Hidden correlation | Long SPY breakout + short dollar tracked (portfolio beta overlap noted) |
| Early breakout failure | "False breakout" captured by failed-retest exit rule |
