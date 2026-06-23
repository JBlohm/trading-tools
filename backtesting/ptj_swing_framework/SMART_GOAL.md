# SMART Goal: PTJ Swing Framework — Multi-Symbol Portfolio Backtest

**Specific:** Walk-forward backtest of the PTJ Macro Breakout With Asymmetric Risk strategy across a
6-symbol universe (SPY, QQQ, IWM, GLD, XLF, XLE) with portfolio-level constraints, using existing
`detect_macro_breakout.py` signal logic without modification.

**Measurable:**
- Net positive expectancy (mean trade P&L > 0) on the out-of-sample period (2020–2025) — required for the daily loop to emit ENTER decisions rather than SKIP.
- Max portfolio drawdown ≤ 20% of starting equity ($5,000 on a $25k account).
- All 6 symbols trade at least once over the full backtest period (signal coverage check).

**Achievable:** The signal logic is already proven in the single-symbol SPY backtest (long side:
83.3% win rate, +0.572% avg return). Portfolio diversification across less-correlated symbols
(GLD, XLF, XLE) may improve aggregate statistics by adding uncorrelated trade sequences.

**Relevant:** The $25k swing desk needs a validated, realistic expectation of strategy performance
before committing paper-trading capital to the daily loop.

**Time-bound:** Complete with production-ready code, tests, and documentation in this sprint
(TRA-67). Results used immediately for go/no-go decision on paper trading.

---

## Parameters Fixed for Backtest

All parameters inherited from `detect_macro_breakout.py` defaults (no parameter sweep):

| Parameter | Value | Notes |
|-----------|-------|-------|
| RANGE_LOOKBACK | 20 bars | Consolidation range |
| RANGE_TIGHT_PCT | 6% | Tight compression threshold |
| ATR_PERIOD_SHORT | 20 | Short ATR |
| ATR_PERIOD_LONG | 60 | Long ATR for compression ratio |
| MIN_CONFIRMS_LONG | 3 | Min macro confirmations for long entry |
| MIN_CONFIRMS_SHORT | 3 | Min macro confirmations for short entry |
| SLIPPAGE | 0.05% | Applied each way |
| RISK_PCT | 1% | Risk per trade (= $250 on $25k account) |
| MAX_NOTIONAL_PCT | 20% | Max position notional per trade |
| PARTIAL_PROFIT_R | 2.5R | Partial profit target |
| MAX_HOLD_BARS | 20 | Time stop |
| MAX_POSITIONS | 6 | Portfolio position limit |
| MAX_EQUITY_CLUSTER | 2 | Max from SPY/QQQ/IWM cluster |
| WEEKLY_COOLDOWN | 7 days | One new trade per symbol per week |
