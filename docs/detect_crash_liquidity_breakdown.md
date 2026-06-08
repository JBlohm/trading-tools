# detect_crash_liquidity_breakdown — PTJ Crash Playbook / Liquidity Breakdown Detector

Reads historical daily market data from TWS via `ib_async` and evaluates the PTJ-inspired *Crash Playbook / Liquidity Breakdown* strategy. Outputs a structured, human-readable summary followed by a fenced JSON block that an LLM can act on directly.

**Read-only**: never places, modifies, or cancels orders.

Connection ID: **1018** (read-only, see `tools/connection_ids.json`)

## Signal States

| State | Meaning |
|-------|---------|
| `no_setup` | No deterioration cluster — market is not in crash pattern |
| `watchlist_deterioration` | Early signals visible; not enough for a full setup |
| `setup_armed` | Deterioration cluster forming (price below 200dma/shelf + ≥2 confirmations) |
| `entry_trigger_short` | Break + failed retest + ≥2 confirmations; short entry valid |
| `manage_open_short` | Open short still intact; trail stop above most recent lower high |
| `de_risk_exit` | Exit conditions met (stop hit, policy backstop, or volatility compression) |
| `blocked_missing_data` | Primary series too short for 200dma/shelf calculations |

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Signal computed successfully (including `no_setup`) |
| 2 | Blocked — primary daily data missing or too short (< 260 bars) |
| 3 | TWS unavailable / connection failure |

## Prerequisites

- Python 3.11+
- Interactive Brokers TWS or IB Gateway running and accepting API connections
- TWS API enabled: **File → Global Configuration → API → Settings → Enable ActiveX and Socket Clients**
- Historical data subscription for the relevant instruments

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
python tools/detect_crash_liquidity_breakdown.py [options]
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--symbol SYMBOL` | `SPY` | Primary index symbol |
| `--sec-type TYPE` | `STK` | Security type: `STK` or `FUT` |
| `--currency CUR` | `USD` | Currency |
| `--exchange EXCH` | `SMART` | Exchange |
| `--expiry YYYYMM` | | Contract expiry (FUT only) |
| `--vix-symbol SYMBOL` | | Volatility index symbol, e.g. `VIX` (IB index contract) |
| `--credit-symbol SYMBOL` | | Credit proxy ETF, e.g. `HYG` or `JNK` |
| `--breadth-symbol SYMBOL` | | Breadth proxy ETF, e.g. `RSP` |
| `--shelf-lookback N` | `63` | Session lookback for support shelf |
| `--break-threshold PCT` | `0.005` | Shelf break fraction (0.005 = 0.5%) |
| `--position-side {short,flat}` | | Current position side |
| `--entry-price PRICE` | | Short entry price |
| `--risk-high PRICE` | | Stop level — close above triggers `de_risk_exit` |
| `--breakdown-level PRICE` | | Key breakdown level for management context |
| `--policy-stop {true,false}` | `false` | Emergency policy stop — forces `de_risk_exit` |
| `--host HOST` | `192.168.2.187` | TWS hostname or IP |
| `--paper` | ✓ (default) | Paper trading (port 7497) |
| `--live` | | Live trading (port 7496) |
| `--port PORT` | | Custom port |

### Examples

```bash
# Basic SPY check (paper TWS, no optional data)
python tools/detect_crash_liquidity_breakdown.py

# Full confirmation suite
python tools/detect_crash_liquidity_breakdown.py \
    --vix-symbol VIX --credit-symbol HYG --breadth-symbol RSP --live

# ES futures
python tools/detect_crash_liquidity_breakdown.py \
    --symbol ES --sec-type FUT --expiry 202509 --live

# With open short position context
python tools/detect_crash_liquidity_breakdown.py \
    --position-side short --entry-price 540 --risk-high 555 --breakdown-level 530

# Emergency policy stop override
python tools/detect_crash_liquidity_breakdown.py --policy-stop true
```

## Desk Logic

### 1. Price Structure

- **200-day SMA**: close below → price_below_200dma
- **Support shelf**: 20th-percentile close over the `--shelf-lookback` window (default 63 sessions). Represents the floor that held ~80% of recent sessions.
- **Shelf break**: close more than `--break-threshold` below the shelf
- **Failed retest**: price bounced above shelf at some point in the last 20 bars but is now back below
- **Lower low / lower high**: 5-bar proxy for the downtrend continuation

### 2. Volatility Expansion (any one counts as a confirmation)

- VIX ≥ 25 → warning; VIX ≥ 30 → confirmation
- VIX 5-day rate of change ≥ 20% → confirmation
- Index true range > 1.5× 20-day ATR → confirmation

### 3. Breadth Proxy (RSP vs SPY)

- RSP 10-day or 20-day return underperforms SPY by more than 1% → breadth_weak
- If RSP bars are not supplied, the breadth dimension is marked *degraded* (not blocked)

### 4. Credit / Liquidity Proxy (HYG/JNK)

- Credit ETF below its 50-day or 200-day SMA → credit_stressed
- Credit ETF 20-day return lags SPY by more than 2% → credit_underperforming
- If credit bars are not supplied, the credit dimension is marked *degraded* (not blocked)

### 5. Gap-Down Behavior

When the primary index opens below the support shelf, the tool returns `setup_armed` (not `entry_trigger_short`) and sets `gap_down_open: true` in the scorecard. The recommended action is to **wait for a failed VWAP reclaim or failed retest** rather than shorting the open print.

## Setup / Entry / Exit Thresholds

### `setup_armed`
- Price below 200-day SMA **or** support shelf **AND**
- At least 2 of 3 confirmation sources (volatility · breadth · credit)

### `entry_trigger_short`
- Support shelf break **and** failed retest from below **and** ≥ 2 confirmations  
  — **or** — close below support with decisive range expansion (TR > 1.5× ATR) and ≥ 2 confirmations
- Must **not** be a gap-down open (use options or wait for retest in that case)

### `de_risk_exit` triggers
1. Close above `--risk-high` (stop hit)
2. `--policy-stop true` (emergency policy stop)
3. VIX below 20 while holding a short (volatility compression proxy)

### `blocked_missing_data`
- Fewer than 260 primary daily bars (insufficient for 200-day SMA and shelf calculation)
- Missing VIX, credit, or breadth bars alone does **not** block — they reduce confidence

## Output Format

The tool always prints to stdout:

```
================================================================
PTJ CRASH PLAYBOOK / LIQUIDITY BREAKDOWN DETECTOR
================================================================
State:      ENTRY TRIGGER — failed retest confirmed; short entry valid
Confidence: 82%

── Desk Read ──
  Close:          274.00
  200-day SMA:    295.00 ⚠ BELOW
  Support shelf:  290.00 ⚠ BROKEN
  VIX:            55.0 🔴 CONFIRMED
  Confirmations:  3/3  (volatility · breadth · credit)

── Trade Posture ──
  initiate_short_0.33_unit

── Risk Points ──
  • invalidation: close above support shelf 290.00
  • policy stop: central-bank / fiscal intervention ...

── Next Actions ──
  → short_0.33_to_0.50_risk_unit_on_failed_retest
  → set_stop_above_retest_high
  → add_only_on_lower_high_after_lower_low

```json
{
  "tool": "detect_crash_liquidity_breakdown",
  "version": "1.0.0",
  "timestamp": "2020-03-09T16:00:00Z",
  "strategy": "ptj_crash_playbook_liquidity_breakdown",
  "signal_state": "entry_trigger_short",
  "confidence": 0.82,
  "trade_posture": "initiate_short_0.33_unit",
  "market_snapshot": { ... },
  "scorecard": { ... },
  "risk_points": [ ... ],
  "actions": [ ... ],
  "data_quality": { ... }
}
```
```

## JSON Schema

| Key | Type | Description |
|-----|------|-------------|
| `tool` | string | `"detect_crash_liquidity_breakdown"` |
| `version` | string | Semantic version |
| `timestamp` | string | ISO-8601 UTC timestamp |
| `strategy` | string | `"ptj_crash_playbook_liquidity_breakdown"` |
| `signal_state` | string | One of the seven signal states above |
| `confidence` | float | 0–1 confidence in the signal |
| `trade_posture` | string | Human-readable posture label |
| `market_snapshot` | object | `close`, `sma_200`, `sma_50`, `support_shelf`, `vix_level`, `atr_20` |
| `scorecard` | object | Individual indicator booleans plus `confirmations` count |
| `risk_points` | array | Risk/invalidation statements |
| `actions` | array | Ordered list of recommended next actions |
| `data_quality` | object | `missing_optional`, `degraded`, `bar_count` |

## Data Quality and Degraded Mode

When optional data sources (VIX, credit, breadth) are unavailable, the tool:
1. Marks the missing source in `data_quality.missing_optional`
2. Sets `data_quality.degraded = true`
3. Reduces confidence proportionally (10% per missing source)
4. Evaluates with whatever confirmations remain

This ensures the tool remains usable without a full data subscription while
signalling to the caller that the evaluation is less reliable.

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| TWS not running | Stderr JSON with `status: tws_unavailable`, exit 3 |
| < 260 primary bars | `blocked_missing_data` state, exit 2 |
| Missing optional bars | `degraded: true`, reduced confidence, exit 0 |
| VIX/credit/breadth bars missing | Same as above |
| Connection timeout | Stderr JSON, exit 3 |
