# USO Volatility-Contraction Breakout Backtest Summary

## Scope
- Public-data-only daily backtest
- Long/cash only
- No live or paper execution code
- Date range: 2015-01-02 to 2025-05-30
- Split date (70/30): 2022-04-13
- Final recommendation: RESEARCH-ONLY/REJECTED (fewer than 30 trades)

## Core metrics
- trade_count: 3
- win_rate: 66.67%
- profit_factor: 0.89
- expectancy: -8.23
- average_r: -0.01
- median_r: 0.37
- max_drawdown: -1.11%
- ending_equity: 24,975.32
- cagr: -0.01%
- avg_hold_days: 2.00
- gross_profit: 199.22
- gross_loss: 223.90

## In-sample metrics
- trade_count: 3
- win_rate: 66.67%
- profit_factor: 0.89
- expectancy: -8.23
- average_r: -0.01
- median_r: 0.37
- max_drawdown: 0.00%
- ending_equity: 25,000.00
- cagr: 0.00%
- avg_hold_days: 2.00
- gross_profit: 199.22
- gross_loss: 223.90

## Out-of-sample metrics
- trade_count: 0
- win_rate: 0.00%
- profit_factor: 0.00
- expectancy: 0.00
- max_drawdown: 0.00%
- ending_equity: 25,000.00
- cagr: 0.00%

## Benchmark comparison
- buy_hold_uso: ending_equity=10,550.21, cagr=-7.96%, max_drawdown=-89.77%
- cash: ending_equity=25,000.00, cagr=0.00%, max_drawdown=0.00%
- trend_filter: ending_equity=84,553.08, cagr=12.42%, max_drawdown=-20.07%

## Notes
- The strategy requires a constructive long regime in USO and SPY.
- EIA Wednesdays and public macro event dates are blocked for fresh entries.
- ATR percentile uses a trailing 120-day lookback in this v1.
- If a required public input is missing, the engine stays flat and records the skip.
- This v1 deliberately avoids any live, paper, or broker-execution path.
