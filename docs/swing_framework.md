# Swing Strategy Framework — Operator Manual

> Daily loop for the PTJ Macro Breakout With Asymmetric Risk strategy across a
> 6-symbol universe, running on a $25,000 paper/live account via TWS API.

---

## Why This Should Work

The PTJ macro-breakout approach profits from the following market dynamic: liquid markets
periodically compress into consolidation ranges when directional conviction is low. When a macro
catalyst (monetary policy shift, fiscal shock, credit stress) resolves, prices break decisively
from the range. The first breakout bar captures the initial move; the failed-retest hold is the
clean entry — the market has "shown its hand" and price structure provides a nearby invalidation
point for the stop.

**Edge source:** Asymmetric setup (nearby stop, extended target), triggered only after macro
confirmation from at least 3 independent sources (rates, dollar, credit, trend). The system
waits for the two-step confirmation (close outside range → hold retest) before committing,
reducing exposure to single-bar false breakouts.

**Limitation acknowledged:** The existing SPY-only backtest shows Sharpe –5.4 combined. This is
driven almost entirely by the short side (Sharpe –8.5), which suffers in the persistent post-2020
low-rate bull market. The long side has Sharpe +0.13 over 12 years. The framework enforces
SKIP on out-of-regime signals and uses the multi-symbol backtest results to gate live trading.

---

## Symbol Universe

| Symbol | Asset | Macro role |
|--------|-------|-----------|
| SPY | S&P 500 ETF | Broad equity benchmark |
| QQQ | Nasdaq-100 ETF | Tech-led growth / liquidity beta |
| IWM | Russell 2000 ETF | Domestic risk appetite |
| GLD | Gold ETF | Inflation / dollar hedge |
| XLF | Financials Select ETF | Credit / rate-spread proxy |
| XLE | Energy Select ETF | Supply/demand / commodity cycle |

**Macro confirmation bars:** TLT (rates), UUP (dollar), HYG (credit) — not traded.

---

## Strategy Rules

### Entry
- Signal state must be `entry_trigger_long` or `entry_trigger_short`.
- Two-step confirmation required: close outside range + retest hold.
- At least 3 macro confirmations (rates, dollar, credit, trend, structure).
- Volume expansion on breakout or retest bar (1.2× 20-day average).
- No gap against the trade direction at entry.

### Desk constraints gate
- Account must have fewer than 6 open positions.
- Symbol must not be in the equity-broad cluster (SPY/QQQ/IWM) if 2 such positions already open.
- No new trade in the same symbol within 7 calendar days of the last trade.
- Average daily volume ≥ 500,000 (illiquidity filter; all default symbols pass this easily).

### Position sizing
- Risk per trade: 1% of account equity (= $250 on a $25k account).
- Stop distance: derived from `features["long_stop"]` or `features["short_stop"]` = range_high/low ± 0.5 × ATR(20).
- Shares: `floor(risk_budget / stop_distance)`, capped at `floor(20% × equity / entry_price)`.
- If shares < 1: emit SKIP ("position_too_small").

### Trade management
- `HOLD`: position on with no new signal.
- `ADD`: signal `add_unit` — add to winning position (still subject to risk budget).
- `REDUCE`: signal `trail_stop` (profit > 2R) — take 1/3 off; trail stop to breakeven.
- `EXIT`: signal `exit_signal` (stop hit or failed breakout) — close full remaining position.

---

## Decisions JSON Schema

```json
{
  "timestamp": "2026-06-23T09:00:00Z",
  "status": "ok",
  "tws_status": "paper | live | dry_run | offline",
  "account_nlv": 25000.00,
  "mode": "dry_run | paper | live",
  "decisions": [
    {
      "symbol": "SPY",
      "decision": "ENTER | HOLD | ADD | REDUCE | EXIT | SKIP",
      "signal_state": "entry_trigger_long | ...",
      "confidence": 0.75,
      "direction": "long | short | null",
      "entry_price": 450.00,
      "stop_level": 447.50,
      "position_size": 11,
      "risk_amount": 27.50,
      "proposal_payload": { ... },
      "proposal_error": null,
      "skip_reason": null
    }
  ]
}
```

### Possible values for `decision`

| Decision | Meaning |
|----------|---------|
| ENTER | New position signal; proposal created and validated |
| HOLD | Existing position; no action needed |
| ADD | Add units to profitable existing position |
| REDUCE | Take partial profit (1/3 off); trail stop |
| EXIT | Close the position (stop hit or failed breakout) |
| SKIP | No action; `skip_reason` explains why |

### Common `skip_reason` values

| Reason | Explanation |
|--------|-------------|
| `signal_no_setup` | No compression or macro driver |
| `signal_range_forming` | Compression forming, not yet a breakout |
| `signal_breakout_candidate` | Watching; retest hold not yet confirmed |
| `max_positions_reached` | Already 6 open positions |
| `equity_cluster_limit` | 2 positions from SPY/QQQ/IWM cluster already open |
| `weekly_trade_limit_Xd_since_last` | Last trade in this symbol was within 7 days |
| `already_in_position` | Position already open for this symbol |
| `position_too_small_shares_0` | Stop too tight / price too high for sizing |
| `proposal_invalid` | TradeProposal schema validation failed |
| `tws_offline` | TWS not reachable; no trades allowed |
| `insufficient_bars` | Not enough history for indicators |
| `low_liquidity_adv_N` | Average daily volume below 500,000 |

---

## TWS-Offline Behavior

The framework never guesses TWS is available. Offline detection path:

1. `tws_historical_data.py` attempts connection with a 10-second timeout.
2. If `asyncio.TimeoutError`, `ConnectionRefusedError`, or any `OSError` is raised: returns `{"status": "tws_offline", ...}`.
3. `swing_strategy.py` detects `status != "ok"` and immediately returns:
   ```json
   {"status": "tws_offline", "decisions": []}
   ```
4. The daily loop aborts. No orders are placed. Exit code 0 (clean exit, not an error).

**Operator action on offline result:** Check that TWS is running and the API is enabled. Then re-run the loop.

---

## Daily Loop Procedure (Operator Checklist)

Run once per trading day, **after the 4pm ET close** or **before the 9:30am ET open**.

```bash
# 1. Dry-run first (no TWS required, uses yfinance)
python tools/swing_strategy.py --dry-run

# 2. Paper run (requires TWS paper account on port 7497)
python tools/swing_strategy.py --paper

# 3. Review output — confirm all ENTER decisions before paper order fires
#    Orders only fire in --paper or --live mode.

# 4. For live trading (confirm manually)
python tools/swing_strategy.py --live
```

### Pre-run checklist

- [ ] TWS (or IB Gateway) is running and connected.
- [ ] Paper trading account is selected in TWS (if running `--paper`).
- [ ] API connections are enabled in TWS Global Configuration → API → Settings.
- [ ] `swing_state.json` reflects actual open positions (update manually after fills if needed).
- [ ] No economic events (FOMC, CPI, NFP) that require sizing down or skipping.

---

## State File (swing_state.json)

Located at `tools/swing_state.json` by default (override with `--state-file`).

```json
{
  "account_nlv": 25000.0,
  "positions": {
    "SPY": {
      "side": "long",
      "entry_price": 450.00,
      "stop_level": 447.50,
      "entry_date": "2026-06-20",
      "bars_held": 3
    }
  },
  "last_trade_dates": {
    "SPY": "2026-06-20"
  }
}
```

Update `account_nlv` after every weekly reconciliation. The file is written only in paper/live
mode after order placement. In dry-run mode the file is read but not modified.

---

## Risk Rules

| Rule | Value | Rationale |
|------|-------|-----------|
| Max risk per trade | 1% of equity | $250 on $25k; survives 20 consecutive losses |
| Max notional per position | 20% of equity | $5,000; limits leverage on liquid ETFs |
| Max open positions | 6 | Account size ceiling |
| Max equity-cluster positions | 2 | SPY+QQQ+IWM are 0.9+ correlated |
| Weekly trade cooldown | 7 days | Prevents overtrading after a loss |
| Min ADV | 500,000 | All default symbols clear this by a factor of 100× |

---

## Correlation Clusters

The strategy limits concentration in the equity-broad cluster (SPY, QQQ, IWM). These three
instruments typically have > 0.9 daily return correlation. Holding all three simultaneously
is functionally equivalent to holding one position three times the size.

**Cluster rules:**
- `equity_broad` (SPY, QQQ, IWM): max 2 simultaneous positions.
- Other symbols (GLD, XLF, XLE): no cluster limit beyond the global 6-position max.

---

## TWS Historical Data

`tools/tws_historical_data.py` fetches daily OHLCV bars from TWS using `ADJUSTED_LAST` to
include dividend and split adjustments (same as the backtest's yfinance adjusted close).

```bash
# Fetch 300 calendar days for SPY on paper TWS
python tools/tws_historical_data.py --symbol SPY --days 300

# Check output — must be {"status": "ok", ...}
```

If TWS returns fewer bars than requested (thin recent history), the strategy silently proceeds
with whatever is available, subject to the 220-bar warmup requirement for the indicators.

---

## Backtest Results and Go/No-Go Threshold

Run `python backtesting/ptj_swing_framework/backtest_swing_framework.py` to regenerate.

**Go/no-go rule:** If the out-of-sample (2020–2025) combined mean trade return is ≤ 0% (negative
expectancy), the live loop defaults to SKIP for all entry signals until the strategy is
re-evaluated. The threshold is checked each time the operator reviews `performance_summary.md`.

The existing single-symbol backtest shows strong short-side losses that pull the combined
Sharpe negative. The multi-symbol framework may improve this by:
1. Diversifying into non-equity symbols with distinct macro drivers (GLD, XLF, XLE).
2. Filtering the short side more aggressively with cluster-level limits.
3. Giving the long side in QQQ/IWM additional distinct backtest trades.

---

## Files

| File | Purpose |
|------|---------|
| `tools/swing_strategy.py` | Daily loop CLI: evaluate all symbols, emit decisions |
| `tools/tws_historical_data.py` | Historical bars from TWS paper/live |
| `tools/swing_state.json` | Persistent state: open positions, last trade dates |
| `tools/detect_macro_breakout.py` | Signal detection (unchanged) |
| `tools/trade_proposal.py` | TradeProposal schema and validation |
| `tools/pretrade_risk_check.py` | Pre-trade risk gate |
| `tools/place_order.py` | Order execution |
| `backtesting/ptj_swing_framework/backtest_swing_framework.py` | 6-symbol backtest |
| `backtesting/ptj_swing_framework/results/performance_summary.md` | Backtest results |
| `tests/test_swing_strategy.py` | Unit tests for swing_strategy.py |
| `tests/test_tws_historical_data.py` | Unit tests for tws_historical_data.py |
