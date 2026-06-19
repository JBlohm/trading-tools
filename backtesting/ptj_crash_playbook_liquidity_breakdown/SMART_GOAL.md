# SMART Goal: PTJ Crash Playbook / Liquidity Breakdown Backtest

## Goal Statement

Validate the PTJ Crash Playbook signal detector across 10 years of SPY daily data
(2015-06-01 through 2025-06-01) to measure signal quality, trade performance, and
correct identification of known crash episodes, publishing all results to GitHub by
end of this session.

## SMART Breakdown

**Specific**
Run a walk-forward backtest on SPY (primary), VIX (volatility), HYG (credit proxy),
and RSP (breadth proxy) daily bars using the existing `detect_crash_liquidity_breakdown.py`
pure functions (`calculate_indicators` + `evaluate_crash_playbook`). Simulate trades at
0.33-unit position size on `entry_trigger_short` signals and apply the strategy's own
stop and exit rules. Report results in a structured CSV and summary Markdown.

**Measurable** — The backtest must deliver all of the following:
1. Signal state frequency distribution (how often each of the 7 states fires)
2. Forward returns at 5, 10, and 20 bars following each `entry_trigger_short`
3. Trade-level results: entry date, entry price, exit date, exit price, exit reason,
   return (%), bars held
4. Aggregate metrics: win rate (5%+ downside within 20 bars), average trade return,
   Sharpe ratio of simulated strategy, max drawdown
5. Episode hit-check: coverage of 2018-Q4 selloff, 2020 COVID crash, 2022 bear market —
   report stress detection and tradable entry triggers separately

**Achievable**
- All indicator logic already exists in the tool; backtest reuses it directly.
- Historical daily data sourced from Yahoo Finance (SPY, ^VIX, HYG, RSP), ~10 years.
- No TWS connection required for offline historical backtest.
- pandas + numpy are available for data handling.

**Relevant**
Validates whether the signal logic produces actionable alpha in real market conditions.
Results directly inform whether the strategy is ready for live paper-trade monitoring.

**Time-bound**
Completed and results published to the `backtesting/ptj_crash_playbook_liquidity_breakdown/`
folder in the GitHub repository in this session (2026-06-19).

## Common Pitfalls Addressed

| Pitfall | Mitigation |
|---------|-----------|
| Look-ahead bias | Indicators computed only on bars[0..d]; entry at next bar's open |
| Survivorship bias | Adjusted close prices include dividends/splits |
| Overfitting | All parameters are the tool's defaults (no optimisation) |
| Transaction costs | 0.05% slippage each side included in every trade |
| Data snooping | No parameter tuning; split into in-sample (2015-2019) and OOS (2020-2025) |
| Unrealistic sizing | Fractional unit (0.33) per the strategy spec |
| Gap risk | Gap-down open rule enforced: no entry when `gap_down_open=True` |
| Policy stop | Tested as exit trigger in unit tests; treated as emergency exit |
