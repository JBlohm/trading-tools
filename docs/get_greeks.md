# get_greeks — Portfolio Greeks Tool

Retrieves portfolio-level option Greeks (delta, gamma, theta, vega) from Interactive Brokers TWS. Iterates all option positions in the portfolio, requests model Greeks for each, and returns per-position and aggregate totals as JSON to stdout. Non-option positions are included with zero Greeks.

## Prerequisites

- Python 3.11+
- Interactive Brokers TWS or IB Gateway running and accepting API connections
- TWS API enabled: **File → Global Configuration → API → Settings → Enable ActiveX and Socket Clients**
- Market data subscription for the relevant option underlyings (required for model Greeks)

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
python tools/get_greeks.py [--host HOST] [--paper | --live | --port PORT]
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
python tools/get_greeks.py

# Live trading
python tools/get_greeks.py --live

# TWS on a different machine
python tools/get_greeks.py --host 10.0.0.5
```

## Output

Prints JSON to **stdout**. `portfolio_greeks` aggregates exposure across all option positions; `positions` lists each holding with its individual Greeks (null for non-option instruments):

```json
{
  "timestamp": "2026-05-30T14:00:00Z",
  "portfolio_greeks": {
    "delta": -12.5,
    "gamma": 0.034,
    "theta": -18.20,
    "vega": 45.60
  },
  "positions": [
    {
      "symbol": "AAPL",
      "sec_type": "OPT",
      "expiry": "20260117",
      "strike": 180.0,
      "right": "C",
      "currency": "USD",
      "position": -10.0,
      "greeks": {
        "delta": 0.45,
        "gamma": 0.03,
        "theta": -0.05,
        "vega": 0.20,
        "iv": 0.28
      },
      "account": "DU123456"
    }
  ]
}
```

An empty portfolio returns zero aggregate Greeks and an empty `positions` array.

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

Greek fields may be `null` when TWS cannot supply model data for a position (e.g. no market data subscription, or outside market hours).

## Connection Details

- **Connection ID:** 1001 (read-only; see `tools/connection_ids.json` for the full ID register)
- Each tool uses a unique connection ID so multiple tools can run in parallel without conflict

## TWS API Settings

Ensure API connections are allowed from your host:
1. Open TWS → Edit → Global Configuration → API → Settings
2. Check **Enable ActiveX and Socket Clients**
3. Add the calling machine's IP to **Trusted IPs**
