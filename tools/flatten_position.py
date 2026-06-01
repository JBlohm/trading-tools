#!/usr/bin/env python3
"""
flatten_position.py — Emergency de-risking: close all positions in a given scope.

Scope types:
  symbol   — close all contracts where the underlying symbol matches
  strategy — close all option/futures contracts on this underlying (treated as symbol match)
  account  — close ALL positions in this account (requires --confirm)

In --simulate mode, proposes the closing orders without placing them.
Without --simulate, orders are placed immediately at market.

Every run produces a structured audit record with a unique audit_id.
Closing orders are strictly opposite to existing positions — this tool never introduces new risk.

Usage:
  python flatten_position.py --scope symbol --scope-value AAPL --simulate [--live]
  python flatten_position.py --scope strategy --scope-value SPY [--simulate] [--live]
  python flatten_position.py --scope account --scope-value DU123456 --confirm --simulate [--live]

Connection ID: 1006 (see tools/connection_ids.json)
"""

import argparse
import asyncio
import contextlib
import io
import json
import sys
import uuid
from datetime import datetime, timezone

try:
    from ib_async import IB, Contract, MarketOrder, Option, Future, Stock
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
CLIENT_ID = 1006
CONNECT_TIMEOUT = 10

SCOPE_SYMBOL = "symbol"
SCOPE_STRATEGY = "strategy"
SCOPE_ACCOUNT = "account"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _closing_action(position: float) -> str:
    return "SELL" if position > 0 else "BUY"


def _position_matches_scope(item, scope_type: str, scope_value: str) -> bool:
    if scope_type == SCOPE_ACCOUNT:
        return item.account == scope_value
    # symbol and strategy both match on the underlying contract symbol
    return item.contract.symbol.upper() == scope_value.upper()


def _position_to_summary(item) -> dict:
    c = item.contract
    return {
        "symbol": c.symbol,
        "sec_type": c.secType,
        "expiry": getattr(c, "lastTradeDateOrContractMonth", None),
        "strike": getattr(c, "strike", None),
        "right": getattr(c, "right", None),
        "position": item.position,
        "market_price": item.marketPrice,
        "market_value": item.marketValue,
        "unrealized_pnl": item.unrealizedPNL,
        "account": item.account,
    }


def _closing_order_spec(item) -> dict:
    c = item.contract
    return {
        "symbol": c.symbol,
        "sec_type": c.secType,
        "expiry": getattr(c, "lastTradeDateOrContractMonth", None),
        "strike": getattr(c, "strike", None),
        "right": getattr(c, "right", None),
        "currency": c.currency,
        "exchange": getattr(c, "primaryExch", None) or getattr(c, "exchange", "SMART"),
        "action": _closing_action(item.position),
        "quantity": abs(item.position),
        "order_type": "MKT",
        "account": item.account,
    }


def _build_contract_from_item(item) -> Contract:
    c = item.contract
    sec_type = c.secType
    if sec_type == "STK":
        contract = Stock(c.symbol, getattr(c, "primaryExch", None) or "SMART", c.currency)
    elif sec_type in ("OPT", "FOP"):
        contract = Option(
            c.symbol,
            getattr(c, "lastTradeDateOrContractMonth", ""),
            getattr(c, "strike", 0.0),
            getattr(c, "right", "C"),
            "SMART",
            currency=c.currency,
        )
        contract.secType = sec_type
        contract.conId = getattr(c, "conId", 0)
    elif sec_type == "FUT":
        contract = Future(
            c.symbol,
            getattr(c, "lastTradeDateOrContractMonth", ""),
            "SMART",
            currency=c.currency,
        )
        contract.conId = getattr(c, "conId", 0)
    else:
        contract = Contract()
        contract.symbol = c.symbol
        contract.secType = sec_type
        contract.currency = c.currency
        contract.exchange = "SMART"
        contract.conId = getattr(c, "conId", 0)
    return contract


async def flatten_position(
    host: str, port: int, client_id: int, args: argparse.Namespace
) -> dict:
    audit_id = str(uuid.uuid4())
    timestamp = utc_timestamp()

    if args.scope == SCOPE_ACCOUNT and not args.confirm:
        raise ValueError(
            "Account-scope flatten requires --confirm. "
            "This will close ALL positions in the account."
        )

    ib = IB()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            await asyncio.wait_for(
                ib.connectAsync(host, port, clientId=client_id, readonly=args.simulate),
                timeout=CONNECT_TIMEOUT,
            )
    except asyncio.TimeoutError:
        raise ConnectionError(f"Timed out connecting to TWS at {host}:{port}")
    except Exception as exc:
        raise ConnectionError(f"Cannot reach TWS at {host}:{port} — {exc}") from exc

    try:
        portfolio = ib.portfolio()

        matching = [
            item for item in portfolio
            if _position_matches_scope(item, args.scope, args.scope_value) and item.position != 0
        ]

        positions_found = [_position_to_summary(item) for item in matching]
        orders_out = []

        if not matching:
            return {
                "audit_id": audit_id,
                "timestamp": timestamp,
                "scope": {"type": args.scope, "value": args.scope_value},
                "simulate": args.simulate,
                "status": "no_positions",
                "positions_found": [],
                "orders": [],
            }

        for item in matching:
            spec = _closing_order_spec(item)
            if args.simulate:
                spec["simulated"] = True
                spec["order_id"] = None
                spec["perm_id"] = None
            else:
                contract = _build_contract_from_item(item)
                order = MarketOrder(spec["action"], spec["quantity"])
                order.account = item.account
                trade = ib.placeOrder(contract, order)
                await asyncio.sleep(0.3)
                spec["simulated"] = False
                spec["order_id"] = trade.order.orderId
                spec["perm_id"] = trade.order.permId
                spec["order_status"] = trade.orderStatus.status
            orders_out.append(spec)

        return {
            "audit_id": audit_id,
            "timestamp": timestamp,
            "scope": {"type": args.scope, "value": args.scope_value},
            "simulate": args.simulate,
            "status": "simulated" if args.simulate else "executed",
            "positions_found": positions_found,
            "orders": orders_out,
        }
    finally:
        ib.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emergency de-risking: close all positions in a given scope.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python flatten_position.py --scope symbol --scope-value AAPL --simulate
  python flatten_position.py --scope symbol --scope-value AAPL --live
  python flatten_position.py --scope strategy --scope-value SPY --simulate --live
  python flatten_position.py --scope account --scope-value DU123456 --confirm --simulate
        """,
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST,
        help=f"TWS hostname or IP (default: {DEFAULT_HOST})",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--paper", dest="port", action="store_const", const=PORT_PAPER,
        help=f"Paper trading (port {PORT_PAPER}, default)",
    )
    mode.add_argument(
        "--live", dest="port", action="store_const", const=PORT_LIVE,
        help=f"Live trading (port {PORT_LIVE})",
    )
    mode.add_argument("--port", dest="port", type=int, help="Custom port")
    parser.set_defaults(port=PORT_PAPER)

    parser.add_argument(
        "--scope", required=True,
        choices=[SCOPE_SYMBOL, SCOPE_STRATEGY, SCOPE_ACCOUNT],
        help="Scope for position selection: symbol, strategy, or account",
    )
    parser.add_argument(
        "--scope-value", required=True,
        help="Value for scope (ticker symbol, strategy name, or account ID)",
    )
    parser.add_argument(
        "--simulate", action="store_true",
        help="Propose closing orders without placing them (safe mode)",
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="Required when using --scope account (explicit confirmation to close all)",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        result = asyncio.run(flatten_position(args.host, args.port, CLIENT_ID, args))
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
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "status": "invalid_input",
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
