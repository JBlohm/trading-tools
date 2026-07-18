# Politician Trade-Following Backtest

Research-only framework for testing whether public U.S. politician trade disclosures create excess return **when executed only after the public filing date**. No live trading or paper-trading integration is included.

## Scope

- House primary metadata source: yearly ZIPs from `https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip`.
- House PTR PDFs: `https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{DocID}.pdf`.
- House metadata parser filters `FilingType=P` only.
- Senate is isolated behind `SenateDisclosureSource`; this runtime observed HTTP 403 against EFD search, so the adapter requires a manual/vendor CSV fallback until authenticated access is available.
- Phase 1 is liquid U.S. listed common stocks and ETFs only. Options are deliberately excluded from equity signals.

## One-command reproducible run

From the repository root:

```bash
python3 backtesting/politician_trade_following/backtest_politician_trade_following.py   --prices-csv path/to/prices.csv   --transactions-csv path/to/normalized_transactions_input.csv   --variant naive_long_buys   --max-lag-days 45   --output-dir backtesting/politician_trade_following/results
```

For a smoke-test demo dataset, omit the CSV flags:

```bash
python3 backtesting/politician_trade_following/backtest_politician_trade_following.py
```

## Required normalized transaction schema

`PoliticianTransaction` rows normalize every data source into:

- `politician`, `chamber`, `filing_date`, `transaction_date`, `owner`
- `ticker`, `security`, `asset_type`, `transaction_type`, `amount_range`
- `source_doc_id`, `source_url`, optional `party`

## No-lookahead rules

- Signal date is always `filing_date`, never `transaction_date`.
- Entry is the next tradable open strictly after `filing_date`.
- Lag analysis supports `<=7d`, `<=14d`, `<=30d`, `<=45d`, and all filings.

## Strategy variants

- `naive_long_buys`: long reported buys, ignore sells.
- `long_buys_short_sells`: long buys and short sells for a borrowable/liquid universe.
- `sells_exit_only`: buy signals can enter; sale signals are exit-only risk controls.

The module also exposes building blocks for politician-scored and aggregate-flow extensions without changing ingestion logic.

## Generated artifacts

- `normalized_transactions.csv`
- `trade_ledger.csv`
- `equity_curve.csv`
- `metrics.csv`
- `random_shuffled_filing_date_placebo.csv`
- `performance_summary.md`

## Risk note

The test is not whether politicians bought winners months ago. The test is whether buying after public filing survives costs, slippage, drawdowns, and a random shuffled filing-date placebo. If it does not, cut the idea.
