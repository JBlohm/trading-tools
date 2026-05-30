# place_order — Place Order Tool

Places an equity or derivative order via Interactive Brokers TWS after running a configurable risk pre-check. Prints the placed order details as JSON to stdout. Any IB API validation warnings are redirected to stderr so stdout remains clean JSON for agent consumers.

## Prerequisites

- Python 3.11+
- Interactive Brokers TWS or IB Gateway running and accepting API connections
- TWS API enabled: **File → Global Configuration → API → Settings → Enable ActiveX and Socket Clients**

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
python tools/place_order.py --symbol SYMBOL --action BUY|SELL --quantity N --order-type MKT|LMT \
    [--limit-price PRICE] [--tif DAY|GTC|IOC|FOK] \
    [--max-notional USD] [--max-pct-nlv PCT] \
    [--host HOST] [--paper | --live | --port PORT]
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--symbol SYMBOL` | *(required)* | Ticker symbol (e.g. `AAPL`) |
| `--action BUY\|SELL` | *(required)* | Order direction |
| `--quantity N` | *(required)* | Number of shares/contracts |
| `--order-type MKT\|LMT` | *(required)* | Order type |
| `--limit-price PRICE` | | Limit price (required for LMT orders) |
| `--tif` | `DAY` | Time in force: `DAY`, `GTC`, `IOC`, `FOK` |
| `--sec-type` | `STK` | Security type: `STK`, `OPT`, `FUT`, `FOP` |
| `--currency` | `USD` | Currency |
| `--exchange` | `SMART` | Exchange |
| `--expiry YYYYMMDD` | | Contract expiry (for OPT/FUT) |
| `--strike PRICE` | | Strike price (for OPT) |
| `--right C\|P` | | Option right: `C` (call) or `P` (put) |
| `--max-notional USD` | `100000` | Risk gate: max estimated notional in USD |
| `--max-pct-nlv PCT` | `10` | Risk gate: max order size as % of Net Liquidation Value |
| `--host HOST` | `192.168.2.187` | IP address or hostname of TWS |
| `--paper` | ✓ (default) | Connect to paper trading account (port 7497) |
| `--live` | | Connect to live trading account (port 7496) |
| `--port PORT` | | Custom port |

### Examples

```bash
# Market order, paper account
python tools/place_order.py --symbol AAPL --action BUY --quantity 10 --order-type MKT

# Limit order
python tools/place_order.py --symbol AAPL --action SELL --quantity 5 --order-type LMT --limit-price 180.00

# Live account with tighter risk limits
python tools/place_order.py --symbol SPY --action BUY --quantity 100 --order-type MKT \
    --live --max-notional 50000 --max-pct-nlv 5
```

## Risk Pre-check

Before submitting, the tool evaluates three conditions. All must pass:

1. **Notional limit** — estimated notional value ≤ `--max-notional`
2. **NLV %** — estimated notional ≤ `--max-pct-nlv` % of Net Liquidation Value
3. **Excess liquidity** — account excess liquidity > 0 (not margin-called)

## Output

On success, prints JSON to **stdout** and exits with code `0`:

```json
{
  "timestamp": "2026-05-30T14:00:00Z",
  "order_id": 5,
  "perm_id": 123456789,
  "symbol": "AAPL",
  "sec_type": "STK",
  "currency": "USD",
  "exchange": "SMART",
  "action": "BUY",
  "order_type": "LMT",
  "quantity": 1.0,
  "lmt_price": 1.0,
  "tif": "DAY",
  "status": "PreSubmitted",
  "risk_check": {
    "nlv": 500000.0,
    "excess_liquidity": 200000.0,
    "estimated_notional": 1.0,
    "max_notional_limit": 100000.0,
    "max_pct_nlv": 10.0,
    "passed": true,
    "failures": []
  }
}
```

## Error Handling

| Condition | Exit code | Output stream |
|-----------|-----------|---------------|
| Risk check failed | 2 | stderr JSON `status: risk_check_failed` |
| TWS unreachable | 1 | stderr JSON `status: tws_unavailable` |
| Invalid input | 1 | stderr JSON `status: invalid_input` |
| Unexpected error | 1 | stderr JSON `status: error` |

IB API validation warnings (e.g. code 399 — order held outside market hours) are written to **stderr** only; they never appear on stdout.

## Connection Details

- **Connection ID:** 1004 (read-write; see `tools/connection_ids.json` for the full ID register)
- Each tool uses a unique connection ID so multiple tools can run in parallel without conflict
- Orders placed here can be monitored with `get_open_orders.py` and cancelled with `cancel_order.py`

## TWS API Settings

Ensure API connections are allowed from your host:
1. Open TWS → Edit → Global Configuration → API → Settings
2. Check **Enable ActiveX and Socket Clients**
3. Add the calling machine's IP to **Trusted IPs**
