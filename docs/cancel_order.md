# cancel_order — Cancel Order Tool

Cancels an open order by order ID via Interactive Brokers TWS. Looks up the order across all API clients (using `reqAllOpenOrders`) so orders placed by `place_order.py` or any other client can be cancelled. Prints the final order status as JSON.

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
python tools/cancel_order.py --order-id ORDER_ID [--host HOST] [--paper | --live | --port PORT]
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--order-id ORDER_ID` | *(required)* | TWS order ID to cancel |
| `--host HOST` | `192.168.2.187` | IP address or hostname of the machine running TWS |
| `--paper` | ✓ (default) | Connect to paper trading account (port 7497) |
| `--live` | | Connect to live trading account (port 7496) |
| `--port PORT` | | Custom port (overrides `--paper`/`--live`) |

### Examples

```bash
# Cancel order 5 on paper account
python tools/cancel_order.py --order-id 5

# Cancel on live account
python tools/cancel_order.py --order-id 12345 --live

# TWS on a different machine
python tools/cancel_order.py --order-id 5 --host 10.0.0.5
```

## Output

On success, prints JSON to **stdout** and exits with code `0`:

```json
{
  "timestamp": "2026-05-30T14:00:00Z",
  "status": "cancel_sent",
  "order_id": 5,
  "perm_id": 123456789,
  "symbol": "AAPL",
  "sec_type": "STK",
  "currency": "USD",
  "action": "BUY",
  "order_type": "LMT",
  "quantity": 1.0,
  "final_order_status": "Cancelled"
}
```

## Error Handling

| Condition | Exit code | Output stream |
|-----------|-----------|---------------|
| Order not found | 1 | stderr JSON `status: not_found` |
| TWS unreachable | 1 | stderr JSON `status: tws_unavailable` |
| Unexpected error | 1 | stderr JSON `status: error` |

Example not-found response (stderr):
```json
{
  "timestamp": "2026-05-30T14:00:00Z",
  "status": "not_found",
  "order_id": 5,
  "message": "No open order found with order_id 5"
}
```

## Connection Details

- **Connection ID:** 1005 (read-write; see `tools/connection_ids.json` for the full ID register)
- Uses `reqAllOpenOrders` to find the order, so orders placed by **any** client ID are visible
- Each tool uses a unique connection ID so multiple tools can run in parallel without conflict

## TWS API Settings

Ensure API connections are allowed from your host:
1. Open TWS → Edit → Global Configuration → API → Settings
2. Check **Enable ActiveX and Socket Clients**
3. Add the calling machine's IP to **Trusted IPs**
