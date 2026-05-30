# get_open_orders — Open Orders Tool

Retrieves all open (pending, not yet filled or cancelled) orders from Interactive Brokers TWS and prints them as JSON. Uses `reqAllOpenOrders` so orders placed by any API client — including `place_order.py` — are always visible.

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
python tools/get_open_orders.py [--host HOST] [--paper | --live | --port PORT]
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--host HOST` | `192.168.2.187` | IP address or hostname of the machine running TWS |
| `--paper` | ✓ (default) | Connect to paper trading account (port 7497) |
| `--live` | | Connect to live trading account (port 7496) |
| `--port PORT` | | Custom port (overrides `--paper`/`--live`) |

### Examples

```bash
# Paper trading (default)
python tools/get_open_orders.py

# Live trading
python tools/get_open_orders.py --live

# TWS on a different machine
python tools/get_open_orders.py --host 10.0.0.5
```

## Output

Prints a JSON array to **stdout**. Each element represents one open order:

```json
[
  {
    "order_id": 5,
    "perm_id": 123456789,
    "client_id": 1004,
    "symbol": "AAPL",
    "sec_type": "STK",
    "expiry": null,
    "strike": null,
    "right": null,
    "currency": "USD",
    "exchange": "SMART",
    "action": "BUY",
    "order_type": "LMT",
    "total_quantity": 1.0,
    "lmt_price": 1.0,
    "aux_price": null,
    "tif": "DAY",
    "account": "DU123456",
    "status": "PreSubmitted",
    "filled": 0.0,
    "remaining": 1.0,
    "avg_fill_price": null,
    "why_held": null,
    "fills": []
  }
]
```

An empty order book returns `[]`.

## Error Handling

If TWS is unreachable, the tool exits with code `1` and prints a JSON error to **stderr**:

```json
{
  "error": "Cannot reach TWS at 192.168.2.187:7497 — ...",
  "status": "tws_unavailable",
  "hint": "The TWS API is temporarily not available. Please try again later.",
  "timestamp": "2026-05-30T12:00:00Z"
}
```

## Connection Details

- **Connection ID:** 1003 (read-only; see `tools/connection_ids.json` for the full ID register)
- Uses `reqAllOpenOrders` — returns orders from **all** client IDs, not just this connection
- Each tool uses a unique connection ID so multiple tools can run in parallel without conflict

## TWS API Settings

Ensure API connections are allowed from your host:
1. Open TWS → Edit → Global Configuration → API → Settings
2. Check **Enable ActiveX and Socket Clients**
3. Add the calling machine's IP to **Trusted IPs**
