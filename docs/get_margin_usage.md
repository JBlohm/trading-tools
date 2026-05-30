# get_margin_usage — Margin Usage Tool

Retrieves account margin and liquidity metrics from Interactive Brokers TWS and prints them as JSON. Returns Net Liquidation Value (NLV), excess liquidity, buying power, and related margin fields. Supports multi-account TWS sessions and optional per-account filtering.

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
python tools/get_margin_usage.py [--host HOST] [--paper | --live | --port PORT] [--account ACCOUNT]
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--host HOST` | `192.168.2.187` | IP address or hostname of the machine running TWS |
| `--paper` | ✓ (default) | Connect to paper trading account (port 7497) |
| `--live` | | Connect to live trading account (port 7496) |
| `--port PORT` | | Custom port (overrides `--paper`/`--live`) |
| `--account ACCOUNT` | *(all)* | Filter output to a specific account ID |

### Examples

```bash
# Paper trading (default)
python tools/get_margin_usage.py

# Live trading
python tools/get_margin_usage.py --live

# Filter to a specific account
python tools/get_margin_usage.py --account DU123456

# TWS on a different machine
python tools/get_margin_usage.py --host 10.0.0.5
```

## Output

Prints JSON to **stdout**. The `accounts` object is keyed by account ID:

```json
{
  "timestamp": "2026-05-30T14:00:00Z",
  "accounts": {
    "DU123456": {
      "nlv": 500000.0,
      "excess_liquidity": 200000.0,
      "buying_power": 1000000.0,
      "init_margin_req": 50000.0,
      "maint_margin_req": 40000.0,
      "available_funds": 200000.0,
      "gross_position_value": 300000.0,
      "total_cash": 150000.0,
      "unrealized_pnl": 5000.0,
      "realized_pnl": 2500.0
    }
  }
}
```

Fields are `null` when TWS does not supply a value for that tag.

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

- **Connection ID:** 1002 (read-only; see `tools/connection_ids.json` for the full ID register)
- Each tool uses a unique connection ID so multiple tools can run in parallel without conflict

## TWS API Settings

Ensure API connections are allowed from your host:
1. Open TWS → Edit → Global Configuration → API → Settings
2. Check **Enable ActiveX and Socket Clients**
3. Add the calling machine's IP to **Trusted IPs**
