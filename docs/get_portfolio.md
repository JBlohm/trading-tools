# get_portfolio — Portfolio Position Tool

Retrieves the current portfolio positions from Interactive Brokers TWS and prints them as JSON.

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
python tools/get_portfolio.py [--host HOST] [--paper | --live | --port PORT]
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
python tools/get_portfolio.py

# Live trading
python tools/get_portfolio.py --live

# TWS on a different machine
python tools/get_portfolio.py --host 10.0.0.5

# IB Gateway (paper)
python tools/get_portfolio.py --host 127.0.0.1 --port 4002
```

## Output

Prints a JSON array to **stdout**. Each element represents one open position:

```json
[
  {
    "symbol": "AAPL",
    "sec_type": "STK",
    "currency": "USD",
    "exchange": "NASDAQ",
    "con_id": 265598,
    "position": 100.0,
    "market_price": 175.50,
    "market_value": 17550.0,
    "average_cost": 150.00,
    "unrealized_pnl": 2550.0,
    "realized_pnl": 0.0,
    "account": "DU123456"
  }
]
```

An empty portfolio returns `[]`.

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

Redirect stderr to suppress error output:
```bash
python tools/get_portfolio.py 2>/dev/null
```

## Connection Details

- **Connection ID:** 1000 (read-only; see `tools/connection_ids.json` for the full ID register)
- Each tool uses a unique connection ID so multiple tools can run in parallel without conflict.

## TWS API Settings

Ensure API connections are allowed from your host:
1. Open TWS → Edit → Global Configuration → API → Settings
2. Check **Enable ActiveX and Socket Clients**
3. Add the calling machine's IP to **Trusted IPs** (or allow all: `127.0.0.1` for local, or the actual IP)
4. Confirm **Read-Only API** mode is acceptable (this tool uses it)
