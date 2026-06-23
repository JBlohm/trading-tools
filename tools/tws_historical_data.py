#!/usr/bin/env python3
"""
tws_historical_data.py — Fetch daily OHLCV historical bars from TWS.

Returns bars as JSON: {"status": "ok", "symbol": ..., "bars": [...]}
Offline result:       {"status": "tws_offline", "symbol": ..., "message": ...}

When status != "ok", the calling loop must not place trades.
Default: paper TWS (port 7497).

Usage:
    python tws_historical_data.py --symbol SPY --days 300
    python tws_historical_data.py --symbol SPY --days 300 --live

Connection ID: 1019 (see tools/connection_ids.json)
"""

import argparse
import asyncio
import contextlib
import io
import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

try:
    from ib_async import IB, Stock
except ImportError:
    print(
        json.dumps({"error": "ib_async not installed. Run: pip install ib_async", "status": "dependency_missing"}),
        file=sys.stderr,
    )
    sys.exit(1)

DEFAULT_HOST = "192.168.2.187"
PORT_PAPER = 7497
PORT_LIVE = 7496
CLIENT_ID = 1019
CONNECT_TIMEOUT = 10


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def fetch_bars(
    symbol: str,
    days: int = 300,
    host: str = DEFAULT_HOST,
    port: int = PORT_PAPER,
    client_id: int = CLIENT_ID,
) -> dict:
    """
    Fetch daily adjusted OHLCV bars for symbol from TWS.

    Returns
    -------
    dict
        {"status": "ok", "symbol": ..., "bars": [...], "count": int, "timestamp": ...}
        or {"status": "tws_offline"|"no_data"|"error", ..., "message": ..., "timestamp": ...}

    The caller must check status == "ok" before using bars.
    When status != "ok", the strategy loop must not place trades.
    """
    ib = IB()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            await asyncio.wait_for(
                ib.connectAsync(host, port, clientId=client_id, readonly=True),
                timeout=CONNECT_TIMEOUT,
            )
    except (asyncio.TimeoutError, OSError, ConnectionRefusedError) as exc:
        return {
            "status": "tws_offline",
            "symbol": symbol,
            "message": f"TWS unavailable at {host}:{port}: {type(exc).__name__}",
            "bars": [],
            "timestamp": utc_timestamp(),
        }
    except Exception as exc:
        return {
            "status": "tws_offline",
            "symbol": symbol,
            "message": f"TWS connection failed: {exc}",
            "bars": [],
            "timestamp": utc_timestamp(),
        }

    try:
        contract = Stock(symbol, "SMART", "USD")
        await ib.qualifyContractsAsync(contract)

        bars_raw = await ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr=f"{days} D",
            barSizeSetting="1 day",
            whatToShow="ADJUSTED_LAST",
            useRTH=True,
            formatDate=1,
        )

        if not bars_raw:
            return {
                "status": "no_data",
                "symbol": symbol,
                "message": "TWS returned empty bar list",
                "bars": [],
                "timestamp": utc_timestamp(),
            }

        bar_list = []
        for b in bars_raw:
            try:
                bar_list.append({
                    "date": str(b.date),
                    "open": float(b.open),
                    "high": float(b.high),
                    "low": float(b.low),
                    "close": float(b.close),
                    "volume": int(b.volume) if b.volume is not None else 0,
                })
            except (TypeError, ValueError):
                continue

        if not bar_list:
            return {
                "status": "no_data",
                "symbol": symbol,
                "message": "All bars were malformed",
                "bars": [],
                "timestamp": utc_timestamp(),
            }

        return {
            "status": "ok",
            "symbol": symbol,
            "bars": bar_list,
            "count": len(bar_list),
            "timestamp": utc_timestamp(),
        }

    except Exception as exc:
        return {
            "status": "error",
            "symbol": symbol,
            "message": str(exc),
            "bars": [],
            "timestamp": utc_timestamp(),
        }
    finally:
        ib.disconnect()


def bars_to_namespace(bars: list[dict]) -> list:
    """Convert bar dicts from fetch_bars() to SimpleNamespace objects for calculate_indicators()."""
    result = []
    for b in bars:
        result.append(SimpleNamespace(
            date=b["date"],
            open=float(b["open"]),
            high=float(b["high"]),
            low=float(b["low"]),
            close=float(b["close"]),
            volume=int(b.get("volume", 0)),
        ))
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch daily OHLCV historical bars from TWS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tws_historical_data.py --symbol SPY --days 300
  python tws_historical_data.py --symbol QQQ --days 300 --live
        """,
    )
    parser.add_argument("--symbol", required=True, help="Ticker symbol (e.g. SPY)")
    parser.add_argument("--days", type=int, default=300,
                        help="Calendar days of history to fetch (default: 300)")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"TWS host (default: {DEFAULT_HOST})")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--paper", dest="port", action="store_const", const=PORT_PAPER,
                      help=f"Paper trading port {PORT_PAPER} (default)")
    mode.add_argument("--live", dest="port", action="store_const", const=PORT_LIVE,
                      help=f"Live trading port {PORT_LIVE}")
    mode.add_argument("--port", dest="port", type=int, help="Custom port")
    parser.set_defaults(port=PORT_PAPER)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = asyncio.run(fetch_bars(args.symbol, args.days, args.host, args.port))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
