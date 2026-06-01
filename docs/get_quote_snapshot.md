# get_quote_snapshot — Pre-Trade Quote, Session, and Liquidity Snapshot

Retrieves a real-time market data snapshot for a given instrument from Interactive Brokers TWS. Returns bid/ask/last, spread metrics, session state, halt flag, stale-data detection, volume/ADV liquidity metrics, and (for options) Greeks and open interest as JSON. Unavailable fields are returned as `null` with a machine-readable warning code — never a silent blank.

Hard rejects (halted instrument, stale quote, order too large relative to ADV) are written to **stderr** with exit code 2 and `"rejected": true` in the payload.

Connection ID: **1009** (read-only)

## Prerequisites

- Python 3.11+
- Interactive Brokers TWS or IB Gateway running and accepting API connections
- TWS API enabled: **File → Global Configuration → API → Settings → Enable ActiveX and Socket Clients**
- Market data subscription for the relevant instrument

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
python tools/get_quote_snapshot.py --symbol SYMBOL [options]
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--symbol SYMBOL` | *(required)* | Ticker symbol, e.g. `AAPL` |
| `--sec-type TYPE` | `STK` | Security type: `STK`, `OPT`, `FUT`, `FOP` |
| `--currency CUR` | `USD` | Currency |
| `--exchange EXCH` | `SMART` | Exchange |
| `--expiry YYYYMMDD` | | Expiry date (required for OPT/FUT) |
| `--strike PRICE` | | Strike price (required for OPT) |
| `--right {C,P}` | | Option right: `C` (call) or `P` (put) |
| `--quantity N` | | Proposed order size — enables order-size-as-%-ADV liquidity check |
| `--stale-threshold SECS` | `60` | Seconds before a quote is considered stale |
| `--max-spread-pct PCT` | `0.5` | Spread % warning threshold |
| `--max-order-pct-adv PCT` | `10.0` | Order size as % of ADV — warning threshold |
| `--hard-reject-order-pct-adv PCT` | `25.0` | Order size as % of ADV — hard-reject threshold |
| `--host HOST` | `192.168.2.187` | TWS hostname or IP |
| `--paper` | ✓ (default) | Paper trading (port 7497) |
| `--live` | | Live trading (port 7496) |
| `--port PORT` | | Custom port |

### Examples

```bash
# Standard equity quote
python tools/get_quote_snapshot.py --symbol AAPL

# Quote with liquidity check for a 500-share order
python tools/get_quote_snapshot.py --symbol SPY --quantity 500

# Options chain snapshot
python tools/get_quote_snapshot.py --symbol AAPL --sec-type OPT \
    --expiry 20260619 --strike 180 --right C --quantity 10

# Tighter thresholds
python tools/get_quote_snapshot.py --symbol NVDA \
    --max-spread-pct 0.3 --stale-threshold 30
```

## Output

Prints JSON to **stdout** on success. On hard reject, prints JSON to **stderr** and exits with code 2.

```json
{
  "timestamp": "2026-06-01T10:00:00Z",
  "symbol": "AAPL",
  "sec_type": "STK",
  "currency": "USD",
  "exchange": "SMART",
  "quote": {
    "bid": 173.50,
    "ask": 173.52,
    "last": 173.51,
    "bid_size": 100,
    "ask_size": 200,
    "spread": 0.02,
    "spread_pct": 0.0115,
    "close": 172.00,
    "quote_time": "2026-06-01T09:59:58Z",
    "data_source": "live"
  },
  "session": {
    "state": "regular",
    "is_halted": false,
    "halt_flag": 0
  },
  "staleness": {
    "is_stale": false,
    "age_seconds": 2.0,
    "stale_threshold_seconds": 60
  },
  "liquidity": {
    "volume": 45000000,
    "adv": 78000000,
    "order_size": 500,
    "order_pct_adv": 0.0006,
    "bid_size": 100,
    "ask_size": 200,
    "shortable_shares": 1500000
  },
  "options": null,
  "warnings": [],
  "rejected": false,
  "rejection_reason": null
}
```

### Warning codes

| Code | Trigger | Causes hard reject? |
|------|---------|-------------------|
| `HALTED` | IB halt flag 1 or 2 | Yes |
| `STALE_QUOTE` | Quote age exceeds `--stale-threshold` | Yes (when prices present) |
| `INSUFFICIENT_LIQUIDITY` | Order ≥ `--hard-reject-order-pct-adv`% of ADV | Yes |
| `WIDE_SPREAD` | Spread % exceeds `--max-spread-pct` | No |
| `LOW_LIQUIDITY` | Order ≥ `--max-order-pct-adv`% but below hard-reject | No |
| `CLOSED_SESSION` | Clock-based: outside all US sessions | No |
| `EXTENDED_HOURS` | Clock-based: premarket or after-hours session | No |
| `NO_BID_ASK` | No bid or ask received from TWS | No |

### Session states

`regular` · `premarket` (4:00–9:30 ET) · `after_hours` (16:00–20:00 ET) · `closed`

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success — JSON on stdout |
| `1` | Connection error or unexpected exception — JSON error on stderr |
| `2` | Hard rejection — full snapshot JSON on stderr |

## Running tests

```bash
pytest tests/test_get_quote_snapshot.py -v
```

All 56 tests run without a live TWS connection (fully mocked).
