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

### Success — order accepted or submitted (exit code `0`)

Prints JSON to **stdout**. Every response includes a UUID `audit_id` that links the proposal, pre-check, risk check, order request, and execution response for later reconciliation.

```json
{
  "audit_id": "3f2a1b4c-...",
  "submission_timestamp": "2026-05-30T14:00:00Z",
  "broker_ack_timestamp": "2026-05-30T14:00:00Z",
  "client_order_id": 99,
  "broker_order_id": 888,
  "symbol": "AAPL",
  "sec_type": "STK",
  "currency": "USD",
  "exchange": "SMART",
  "action": "BUY",
  "order_type": "LMT",
  "quantity": 10.0,
  "lmt_price": 170.00,
  "tif": "DAY",
  "accepted": true,
  "rejection_reason": null,
  "ib_order_status": "PreSubmitted",
  "fill_status": "open",
  "filled_quantity": 0.0,
  "remaining_quantity": 10.0,
  "average_fill_price": null,
  "commission": null,
  "commission_note": "unavailable at placement time; query fills after settlement",
  "position_snapshot": {
    "available": true,
    "positions": [
      {
        "symbol": "AAPL",
        "sec_type": "STK",
        "con_id": 265598,
        "position": 10.0,
        "market_price": 170.00,
        "market_value": 1700.00,
        "average_cost": 165.00,
        "unrealized_pnl": 50.00,
        "realized_pnl": 0.0,
        "account": "DU123456"
      }
    ],
    "timestamp": "2026-05-30T14:00:01Z"
  },
  "margin_snapshot": {
    "available": true,
    "accounts": {
      "DU123456": {
        "nlv": 500000.0,
        "excess_liquidity": 200000.0,
        "buying_power": 1000000.0,
        "init_margin_req": 10000.0,
        "maint_margin_req": 8000.0
      }
    },
    "timestamp": "2026-05-30T14:00:01Z"
  },
  "risk_check": {
    "nlv": 500000.0,
    "excess_liquidity": 200000.0,
    "estimated_notional": 1700.0,
    "max_notional_limit": 100000.0,
    "max_pct_nlv": 10.0,
    "passed": true,
    "failures": []
  }
}
```

### Confirmation field reference

| Field | Type | Description |
|-------|------|-------------|
| `audit_id` | string (UUID) | Links proposal → pre-check → order request → execution response |
| `submission_timestamp` | ISO-8601 UTC | When the order was submitted to TWS |
| `broker_ack_timestamp` | ISO-8601 UTC | When TWS acknowledged (sampled after placement) |
| `client_order_id` | integer | IB-assigned order ID for this client session |
| `broker_order_id` | integer | IB permanent order ID (stable across sessions) |
| `accepted` | boolean | `true` = accepted/submitted; `false` = rejected or inactive |
| `rejection_reason` | string or null | IB `whyHeld` or status string when `accepted` is false |
| `ib_order_status` | string | Raw IB order status (`PreSubmitted`, `Submitted`, `Filled`, `Inactive`, etc.) |
| `fill_status` | string | `open`, `partial_fill`, `filled`, or `rejected` |
| `filled_quantity` | float | Shares/contracts filled so far |
| `remaining_quantity` | float | Shares/contracts still open |
| `average_fill_price` | float or null | Average execution price; null if no fill yet |
| `commission` | float or null | Total commission; null if fill reports not yet available |
| `commission_note` | string or null | Explains why commission is null (omitted when commission is known) |
| `position_snapshot` | object | Post-trade portfolio positions for the traded symbol (see below) |
| `margin_snapshot` | object | Post-trade margin and buying power (see below) |
| `risk_check` | object | Pre-check inputs and result |

#### `position_snapshot` schema

```json
{
  "available": true,
  "positions": [
    {
      "symbol": "AAPL",
      "sec_type": "STK",
      "con_id": 265598,
      "position": 10.0,
      "market_price": 170.00,
      "market_value": 1700.00,
      "average_cost": 165.00,
      "unrealized_pnl": 50.00,
      "realized_pnl": 0.0,
      "account": "DU123456"
    }
  ],
  "timestamp": "2026-05-30T14:00:01Z"
}
```

If position data is unavailable: `{"available": false, "reason": "<error message>"}`.

Positions are filtered to the traded symbol when matches exist; otherwise all portfolio positions are returned.

#### `margin_snapshot` schema

```json
{
  "available": true,
  "accounts": {
    "DU123456": {
      "nlv": 500000.0,
      "excess_liquidity": 200000.0,
      "buying_power": 1000000.0,
      "init_margin_req": 10000.0,
      "maint_margin_req": 8000.0
    }
  },
  "timestamp": "2026-05-30T14:00:01Z"
}
```

If margin data is unavailable: `{"available": false, "reason": "<error message>"}`.

### Fill status values

| `fill_status` | Meaning |
|---------------|---------|
| `open` | Order submitted, no fills yet |
| `partial_fill` | Partially filled, remainder still working |
| `filled` | Fully filled |
| `rejected` | Order rejected or made inactive by broker |

## Error Handling

All error responses are **structured JSON** — never bare exceptions.

| Condition | Exit code | Output stream | `status` field |
|-----------|-----------|---------------|----------------|
| Risk check failed | 2 | stderr | `risk_check_failed` |
| Broker/TWS error | 2 | stderr | `broker_error` |
| TWS unreachable | 1 | stderr | `tws_unavailable` |
| Invalid input | 1 | stderr | `invalid_input` |
| Unexpected error | 1 | stderr | `error` |

Risk check failure example (stderr, exit 2):
```json
{
  "audit_id": "3f2a1b4c-...",
  "timestamp": "2026-05-30T14:00:00Z",
  "status": "risk_check_failed",
  "risk_check": {
    "nlv": 500000.0,
    "excess_liquidity": 200000.0,
    "estimated_notional": 250000.0,
    "max_notional_limit": 100000.0,
    "max_pct_nlv": 10.0,
    "passed": false,
    "failures": [
      "Estimated notional 250,000.00 exceeds limit 100,000.00"
    ]
  }
}
```

Broker error example (stderr, exit 2):
```json
{
  "audit_id": "3f2a1b4c-...",
  "submission_timestamp": "2026-05-30T14:00:00Z",
  "timestamp": "2026-05-30T14:00:00Z",
  "status": "broker_error",
  "error": "TWS rejected: invalid contract",
  "risk_check": { "passed": true, ... }
}
```

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
