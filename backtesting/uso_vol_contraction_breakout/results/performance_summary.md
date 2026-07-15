# USO Volatility-Contraction Breakout Backtest Summary

## Scope
- Public-data-only daily backtest
- Long/cash only
- No live or paper execution code
- Date range: 2015-01-02 to 2025-05-30
- Split date (70/30): 2022-04-13
- Final recommendation: RESEARCH-ONLY/REJECTED (fewer than 30 trades)

## Core metrics
- trade_count: 2
- win_rate: 50.00%
- profit_factor: 0.69
- expectancy: -34.70
- average_r: -0.19
- median_r: -0.19
- max_drawdown: -1.11%
- ending_equity: 24,930.60
- cagr: -0.03%
- avg_hold_days: 3.00
- gross_profit: 154.50
- gross_loss: 223.90

## In-sample metrics
- trade_count: 2
- win_rate: 50.00%
- profit_factor: 0.69
- expectancy: -34.70
- average_r: -0.19
- median_r: -0.19
- max_drawdown: -0.90%
- ending_equity: 24,930.60
- cagr: -8.82%
- avg_hold_days: 3.00
- gross_profit: 154.50
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
- trend_filter: ending_equity=17,172.44, cagr=-3.54%, max_drawdown=-44.81%

## Notes
- The strategy requires a constructive long regime in USO and SPY.
- EIA Wednesdays and public macro event dates are blocked for fresh entries. ATR percentile uses a 120-day trailing lookback in this v1.
- If a required public input is missing, the engine stays flat and records the skip.
- This v1 deliberately avoids any live, paper, or broker-execution path.
