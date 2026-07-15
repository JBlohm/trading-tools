# USO Volatility-Contraction Breakout Backtest

Public-data-only research backtest for `USO_VolContraction_Breakout_v1`.

## Scope

- Long/cash only.
- One position at a time.
- Uses public daily OHLCV data from Yahoo Finance / Yahoo chart API.
- No live trading or paper trading code.

## Files

- `backtest_uso_vol_contraction_breakout.py` — download data, run the backtest, and write artifacts.
- `test_backtest_uso_vol_contraction_breakout.py` — unit tests for indicators, signals, and artifact writing.
- `results/` — generated CSV and Markdown artifacts.

## Generated artifacts

- `trade_ledger.csv`
- `decision_log.csv`
- `equity_curve.csv`
- `drawdown_series.csv`
- `benchmark_comparison.csv`
- `performance_summary.md`

## How to run

From the repository root:

```bash
python3 backtesting/uso_vol_contraction_breakout/backtest_uso_vol_contraction_breakout.py
pytest backtesting/uso_vol_contraction_breakout/test_backtest_uso_vol_contraction_breakout.py
```

## Strategy guardrails

- Skip new entries when USO volatility is shocked or the regime is risk-off.
- Keep the setup simple: pullback to EMA20, RSI(3) oversold, breakout above the prior day high.
- ATR percentile filter uses a trailing 120-day lookback in this v1.
- Keep execution conservative: next open entry, stop-through-gap modeling, no averaging down.
