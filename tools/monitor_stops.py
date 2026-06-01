#!/usr/bin/env python3
"""
monitor_stops.py — Stop/target order monitoring for open positions.

For each open position, shows:
  - Whether a protective stop order exists (STP, STP LMT, TRAIL, TRAIL LIMIT)
  - Whether stops are stale (older than --max-stop-age-hours, default 48 h)
  - Missing stops (positions with no stop order)
  - Target/exit orders (profit-taking LMT orders in the closing direction)
  - Whether a target price has been reached based on current market price

Usage:
  python monitor_stops.py [--host HOST] [--port PORT] [--paper] [--live]
  python monitor_stops.py [--max-stop-age-hours N]

Connection ID: 1007 (see tools/connection_ids.json)
"""

import argparse
import asyncio
import contextlib
import io
import json
import sys
from datetime import datetime, timezone

try:
    from ib_async import IB
except ImportError:
    print(
        json.dumps(
            {
                "error": "ib_async not installed. Run: pip install ib_async",
                "status": "dependency_missing",
            }
        ),
        file=sys.stderr,
    )
    sys.exit(1)

DEFAULT_HOST = "192.168.2.187"
PORT_PAPER = 7497
PORT_LIVE = 7496
CLIENT_ID = 1007
CONNECT_TIMEOUT = 10
DEFAULT_MAX_STOP_AGE_HOURS = 48.0

STOP_ORDER_TYPES = {"STP", "STP LMT", "TRAIL", "TRAIL LIMIT"}
TARGET_ORDER_TYPES = {"LMT"}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _closing_action_for(position: float) -> str:
    return "SELL" if position > 0 else "BUY"


def _contracts_match(pos_contract, order_contract) -> bool:
    if pos_contract.symbol != order_contract.symbol:
        return False
    if pos_contract.secType != order_contract.secType:
        return False
    if pos_contract.secType in ("OPT", "FOP"):
        pe = getattr(pos_contract, "lastTradeDateOrContractMonth", None)
        oe = getattr(order_contract, "lastTradeDateOrContractMonth", None)
        if pe != oe:
            return False
        if getattr(pos_contract, "strike", None) != getattr(order_contract, "strike", None):
            return False
        if getattr(pos_contract, "right", None) != getattr(order_contract, "right", None):
            return False
    return True


def _get_order_age_hours(trade) -> float | None:
    log = getattr(trade, "log", None)
    if log:
        first = log[0]
        t = getattr(first, "time", None)
        if t is not None:
            if isinstance(t, datetime):
                order_time = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
                return (datetime.now(timezone.utc) - order_time).total_seconds() / 3600
    return None


def _order_to_stop_dict(trade, age_hours: float | None, max_age: float) -> dict:
    o = trade.order
    age_status = "unknown"
    if age_hours is not None:
        age_status = "stale" if age_hours > max_age else "fresh"
    lmt = o.lmtPrice if o.lmtPrice != 1.7976931348623157e308 else None
    aux = o.auxPrice if o.auxPrice != 1.7976931348623157e308 else None
    return {
        "order_id": o.orderId,
        "perm_id": o.permId,
        "order_type": o.orderType,
        "action": o.action,
        "quantity": o.totalQuantity,
        "aux_price": aux,
        "lmt_price": lmt,
        "tif": o.tif,
        "status": trade.orderStatus.status,
        "age_hours": round(age_hours, 2) if age_hours is not None else None,
        "age_status": age_status,
    }


def _order_to_target_dict(trade, pos_market_price: float) -> dict:
    o = trade.order
    lmt = o.lmtPrice if o.lmtPrice != 1.7976931348623157e308 else None
    # Target reached: SELL target exceeded by current price (long pos), or
    # BUY target undercut by current price (short pos)
    target_reached = False
    if lmt is not None and pos_market_price and pos_market_price > 0:
        if o.action == "SELL" and pos_market_price >= lmt:
            target_reached = True
        elif o.action == "BUY" and pos_market_price <= lmt:
            target_reached = True
    return {
        "order_id": o.orderId,
        "perm_id": o.permId,
        "action": o.action,
        "quantity": o.totalQuantity,
        "lmt_price": lmt,
        "tif": o.tif,
        "status": trade.orderStatus.status,
        "target_reached": target_reached,
    }


async def monitor_stops(
    host: str, port: int, client_id: int, max_stop_age_hours: float
) -> dict:
    ib = IB()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            await asyncio.wait_for(
                ib.connectAsync(host, port, clientId=client_id, readonly=True),
                timeout=CONNECT_TIMEOUT,
            )
    except asyncio.TimeoutError:
        raise ConnectionError(f"Timed out connecting to TWS at {host}:{port}")
    except Exception as exc:
        raise ConnectionError(f"Cannot reach TWS at {host}:{port} — {exc}") from exc

    try:
        portfolio = ib.portfolio()
        all_trades = await ib.reqAllOpenOrdersAsync()

        positions_out = []
        summary_covered = 0
        summary_missing = 0
        summary_stale = 0
        summary_targets = 0
        summary_targets_reached = 0

        for item in portfolio:
            if item.position == 0:
                continue

            closing_action = _closing_action_for(item.position)
            pc = item.contract

            # Collect matching stop and target orders for this position
            stop_orders = []
            target_orders = []
            is_stale = False

            for trade in all_trades:
                oc = trade.contract
                order = trade.order

                if not _contracts_match(pc, oc):
                    continue
                if order.action != closing_action:
                    continue

                ot = order.orderType.upper()
                if ot in STOP_ORDER_TYPES:
                    age_hours = _get_order_age_hours(trade)
                    stop_dict = _order_to_stop_dict(trade, age_hours, max_stop_age_hours)
                    stop_orders.append(stop_dict)
                    if stop_dict["age_status"] == "stale":
                        is_stale = True
                elif ot in TARGET_ORDER_TYPES:
                    target_dict = _order_to_target_dict(trade, item.marketPrice)
                    target_orders.append(target_dict)

            if stop_orders:
                stop_status = "stale" if is_stale else "covered"
            else:
                stop_status = "missing"

            if stop_status == "covered":
                summary_covered += 1
            elif stop_status == "missing":
                summary_missing += 1
            elif stop_status == "stale":
                summary_stale += 1

            reached_count = sum(1 for t in target_orders if t["target_reached"])
            summary_targets += len(target_orders)
            summary_targets_reached += reached_count

            positions_out.append(
                {
                    "symbol": pc.symbol,
                    "sec_type": pc.secType,
                    "expiry": getattr(pc, "lastTradeDateOrContractMonth", None),
                    "strike": getattr(pc, "strike", None),
                    "right": getattr(pc, "right", None),
                    "position": item.position,
                    "market_price": item.marketPrice,
                    "account": item.account,
                    "stop_status": stop_status,
                    "stop_orders": stop_orders,
                    "target_orders": target_orders,
                }
            )

        return {
            "timestamp": utc_timestamp(),
            "max_stop_age_hours": max_stop_age_hours,
            "positions": positions_out,
            "summary": {
                "total_positions": len(positions_out),
                "covered": summary_covered,
                "missing": summary_missing,
                "stale": summary_stale,
                "targets": summary_targets,
                "targets_reached": summary_targets_reached,
            },
        }
    finally:
        ib.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor stop and target orders for open positions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python monitor_stops.py                              # paper trading (default)
  python monitor_stops.py --live                       # live trading account
  python monitor_stops.py --max-stop-age-hours 24     # flag stops older than 24 h
        """,
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST,
        help=f"TWS hostname or IP (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--max-stop-age-hours", type=float, default=DEFAULT_MAX_STOP_AGE_HOURS,
        help=f"Hours after which a stop order is considered stale (default: {DEFAULT_MAX_STOP_AGE_HOURS})",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--paper", dest="port", action="store_const", const=PORT_PAPER,
        help=f"Connect to paper trading account (port {PORT_PAPER}, default)",
    )
    mode.add_argument(
        "--live", dest="port", action="store_const", const=PORT_LIVE,
        help=f"Connect to live trading account (port {PORT_LIVE})",
    )
    mode.add_argument("--port", dest="port", type=int, help="Custom TWS port number")

    parser.set_defaults(port=PORT_PAPER)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        result = asyncio.run(
            monitor_stops(args.host, args.port, CLIENT_ID, args.max_stop_age_hours)
        )
    except ConnectionError as exc:
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "status": "tws_unavailable",
                    "hint": "The TWS API is temporarily not available. Please try again later.",
                    "timestamp": utc_timestamp(),
                }
            ),
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error": f"Unexpected error: {exc}",
                    "status": "error",
                    "timestamp": utc_timestamp(),
                }
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
