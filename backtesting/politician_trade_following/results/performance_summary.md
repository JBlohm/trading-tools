# Politician Trade-Following Backtest

Research-only. No live trading integration. Signals are timestamped at the public filing date and execute no earlier than the next tradable open.

## Core result
- Trades: 1
- Total return after costs/slippage: 0.18%
- Max drawdown: 0.00%
- Hit rate: 100.00%
- Average filing lag: 3.0 days

## Methodology guardrails
- Public House source: yearly ZIP metadata; PTRs are FilingType=P; PTR PDFs use the documented DocID URL pattern.
- Senate is isolated behind an adapter; this runtime observed HTTP 403, so use manual/vendor CSV fallback until authenticated access is available.
- Variants supported: naive long buys, long buys/short sells, sells-as-exit-only, lag filters <=7/14/30/45/all, and a random shuffled filing-date placebo.
- Phase 1 is liquid U.S. listed common stocks/ETFs only; options are intentionally excluded from equity signals.

## Placebo
The file `random_shuffled_filing_date_placebo.csv` is the random shuffled filing-date placebo benchmark. If the strategy cannot beat that after costs and drawdown, the edge is not real enough to risk capital.
