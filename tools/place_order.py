#!/usr/bin/env python3
"""
place_order.py — Place an order via Interactive Brokers TWS with a structured risk pre-check gate.

Before any order is submitted, a full pre-trade risk gate (pretrade_risk_check.run_check) is
executed against portfolio state and configured risk limits.  The order is rejected with
machine-readable reason codes unless ALL checks pass (or --simulation mode is set).

Returns the placed order details as JSON to stdout, or an error JSON to stderr.

Usage:
    python place_order.py --symbol AAPL --action BUY --quantity 10 --order-type MKT [options]
    python place_order.py --symbol AAPL --action BUY --quantity 10 --order-type LMT --limit-price 170.00 [options]
    python place_order.py --symbol AAPL --action BUY --quantity 10 --order-type MKT --simulation

Connection ID: 1004 (see tools/connection_ids.json)
"""

import argparse
import asyncio
import contextlib
import io
import json
import pathlib
import sys
import uuid
from datetime import datetime, timezone

try:
    from ib_async import IB, Contract, LimitOrder, MarketOrder, Stock, Option, Future
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

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from tools.pretrade_risk_check import run_check as _run_risk_check, load_limits as _load_limits

DEFAULT_HOST = "192.168.2.187"
PORT_PAPER = 7497
PORT_LIVE = 7496
CLIENT_ID = 1004
CONNECT_TIMEOUT = 10
ACCOUNT_DATA_TIMEOUT = 5
DEFAULT_MAX_NOTIONAL = 100_000.0
DEFAULT_MAX_PCT_NLV = 10.0
IB_UNSET_PRICE = 1.7976931348623157e308

_REJECTED_STATUSES = frozenset({"Inactive", "ApiCancelled", "ApiRejected"})
_MARGIN_TAGS = frozenset({"NetLiquidation", "ExcessLiquidity", "BuyingPower", "InitMarginReq", "MaintMarginReq"})


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fill_status(filled: float, total: float, ib_status: str) -> str:
    if ib_status in _REJECTED_STATUSES:
        return "rejected"
    if filled >= total > 0:
        return "filled"
    if filled > 0:
        return "partial_fill"
    return "open"


def _build_contract(args: argparse.Namespace) -> Contract:
    sec_type = args.sec_type.upper()
    if sec_type == "STK":
        contract = Stock(args.symbol, args.exchange or "SMART", args.currency)
    elif sec_type in ("OPT", "FOP"):
        contract = Option(
            args.symbol,
            args.expiry or "",
            args.strike or 0.0,
            args.right or "C",
            args.exchange or "SMART",
            currency=args.currency,
        )
        contract.secType = sec_type
    elif sec_type == "FUT":
        contract = Future(
            args.symbol,
            args.expiry or "",
            args.exchange or "SMART",
            currency=args.currency,
        )
    else:
        contract = Contract()
        contract.symbol = args.symbol
        contract.secType = sec_type
        contract.currency = args.currency
        contract.exchange = args.exchange or "SMART"

    return contract


def _build_order(args: argparse.Namespace):
    order_type = args.order_type.upper()
    if order_type == "MKT":
        return MarketOrder(args.action.upper(), args.quantity, tif=args.tif)
    elif order_type == "LMT":
        if args.limit_price is None:
            raise ValueError("--limit-price is required for LMT orders")
        return LimitOrder(args.action.upper(), args.quantity, args.limit_price, tif=args.tif)
    else:
        raise ValueError(f"Unsupported order type: {order_type}. Use MKT or LMT.")


def _trade_to_dict(
    trade,
    risk_check: dict,
    audit_id: str,
    submission_ts: str,
    position_snapshot: dict,
    margin_snapshot: dict,
) -> dict:
    contract = trade.contract
    order = trade.order
    status = trade.orderStatus
    fills = getattr(trade, "fills", [])

    ib_status = status.status
    accepted = ib_status not in _REJECTED_STATUSES
    rejection_reason = (
        getattr(status, "whyHeld", None) or ib_status if not accepted else None
    )

    filled = float(getattr(status, "filled", 0.0) or 0.0)
    _remaining = getattr(status, "remaining", None)
    remaining = float(order.totalQuantity) if _remaining is None else float(_remaining)
    avg_fill_price_raw = float(getattr(status, "avgFillPrice", 0.0) or 0.0)
    avg_fill_price = avg_fill_price_raw if avg_fill_price_raw > 0 else None

    total_commission = None
    if fills:
        try:
            commissions = [
                f.commissionReport.commission
                for f in fills
                if getattr(f, "commissionReport", None) is not None
            ]
            if commissions:
                total_commission = sum(commissions)
        except Exception:
            pass

    return {
        "audit_id": audit_id,
        "submission_timestamp": submission_ts,
        "broker_ack_timestamp": utc_timestamp(),
        "client_order_id": order.orderId,
        "broker_order_id": order.permId,
        "symbol": contract.symbol,
        "sec_type": contract.secType,
        "currency": contract.currency,
        "exchange": contract.exchange,
        "action": order.action,
        "order_type": order.orderType,
        "quantity": order.totalQuantity,
        "lmt_price": order.lmtPrice if order.lmtPrice != IB_UNSET_PRICE else None,
        "tif": order.tif,
        "accepted": accepted,
        "rejection_reason": rejection_reason,
        "ib_order_status": ib_status,
        "fill_status": _fill_status(filled, float(order.totalQuantity), ib_status),
        "filled_quantity": filled,
        "remaining_quantity": remaining,
        "average_fill_price": avg_fill_price,
        "commission": total_commission,
        "commission_note": (
            None if total_commission is not None
            else "unavailable at placement time; query fills after settlement"
        ),
        "position_snapshot": position_snapshot,
        "margin_snapshot": margin_snapshot,
        "risk_check": risk_check,
    }


async def _get_nlv_and_excess(ib: IB) -> tuple[float, float]:
    """Return (nlv, excess_liquidity) from account summary."""
    try:
        summary = await asyncio.wait_for(
            ib.accountSummaryAsync(),
            timeout=ACCOUNT_DATA_TIMEOUT,
        )
        nlv = 0.0
        excess = 0.0
        for item in summary:
            if item.tag == "NetLiquidation":
                try:
                    nlv += float(item.value)
                except (ValueError, TypeError):
                    pass
            elif item.tag == "ExcessLiquidity":
                try:
                    excess += float(item.value)
                except (ValueError, TypeError):
                    pass
        return nlv, excess
    except asyncio.TimeoutError:
        return 0.0, 0.0


async def _fetch_position_snapshot(ib: IB, symbol: str) -> dict:
    """Return post-trade portfolio positions, filtered to the traded symbol when available."""
    try:
        portfolio = ib.portfolio()
        positions = []
        for item in portfolio:
            c = item.contract
            positions.append({
                "symbol": c.symbol,
                "sec_type": c.secType,
                "con_id": c.conId,
                "position": item.position,
                "market_price": item.marketPrice,
                "market_value": item.marketValue,
                "average_cost": item.averageCost,
                "unrealized_pnl": item.unrealizedPNL,
                "realized_pnl": item.realizedPNL,
                "account": item.account,
            })
        symbol_positions = [p for p in positions if p["symbol"] == symbol]
        return {
            "available": True,
            "positions": symbol_positions,
            "timestamp": utc_timestamp(),
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


async def _fetch_margin_snapshot(ib: IB) -> dict:
    """Return post-trade margin and buying power snapshot."""
    try:
        summary = await asyncio.wait_for(
            ib.accountSummaryAsync(),
            timeout=ACCOUNT_DATA_TIMEOUT,
        )
        accounts: dict[str, dict] = {}
        for item in summary:
            if item.tag not in _MARGIN_TAGS:
                continue
            if item.account not in accounts:
                accounts[item.account] = {}
            try:
                accounts[item.account][item.tag] = float(item.value)
            except (ValueError, TypeError):
                pass
        result = {
            acct: {
                "nlv": vals.get("NetLiquidation"),
                "excess_liquidity": vals.get("ExcessLiquidity"),
                "buying_power": vals.get("BuyingPower"),
                "init_margin_req": vals.get("InitMarginReq"),
                "maint_margin_req": vals.get("MaintMarginReq"),
            }
            for acct, vals in accounts.items()
        }
        return {"available": True, "accounts": result, "timestamp": utc_timestamp()}
    except (asyncio.TimeoutError, Exception) as exc:
        return {"available": False, "reason": str(exc)}


async def place_order(
    host: str,
    port: int,
    client_id: int,
    args: argparse.Namespace,
    simulation: bool = False,
) -> dict:
    audit_id = str(uuid.uuid4())

    ib = IB()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            await asyncio.wait_for(
                ib.connectAsync(host, port, clientId=client_id, readonly=False),
                timeout=CONNECT_TIMEOUT,
            )
    except asyncio.TimeoutError:
        raise ConnectionError(f"Timed out connecting to TWS at {host}:{port}")
    except Exception as exc:
        raise ConnectionError(f"Cannot reach TWS at {host}:{port} — {exc}") from exc

    try:
        with contextlib.redirect_stdout(sys.stderr):
            contract = _build_contract(args)
            await ib.qualifyContractsAsync(contract)
            order = _build_order(args)

            # --- Structured pre-trade risk gate ---
            order_dict = {
                "symbol": args.symbol,
                "sec_type": args.sec_type,
                "action": args.action,
                "quantity": args.quantity,
                "order_type": args.order_type,
                "limit_price": args.limit_price,
                "tif": args.tif,
                "currency": args.currency,
                "exchange": args.exchange or "SMART",
                "expiry": getattr(args, "expiry", None),
                "strike": getattr(args, "strike", None),
                "right": getattr(args, "right", None),
                "stop_price": getattr(args, "stop_price", None),
            }

            try:
                limits = _load_limits()
            except (FileNotFoundError, json.JSONDecodeError):
                limits = {}

            risk_result = await _run_risk_check(ib, order_dict, limits)

            if risk_result["verdict"] != "pass" and not simulation:
                return {
                    "audit_id": audit_id,
                    "timestamp": utc_timestamp(),
                    "status": "risk_check_failed",
                    "verdict": risk_result["verdict"],
                    "failures": risk_result["failures"],
                    "risk_gate": risk_result,
                }

            submission_ts = utc_timestamp()
            try:
                trade = ib.placeOrder(contract, order)
                # Give TWS a moment to acknowledge
                await asyncio.sleep(0.5)

                position_snapshot = await _fetch_position_snapshot(ib, args.symbol)
                margin_snapshot = await _fetch_margin_snapshot(ib)

                return _trade_to_dict(
                    trade, risk_result, audit_id, submission_ts,
                    position_snapshot, margin_snapshot,
                )
            except (ConnectionError, ValueError):
                raise
            except Exception as exc:
                return {
                    "audit_id": audit_id,
                    "submission_timestamp": submission_ts,
                    "timestamp": utc_timestamp(),
                    "status": "broker_error",
                    "error": str(exc),
                    "risk_check": risk_result,
                }
    finally:
        ib.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Place an order via Interactive Brokers TWS with structured pre-trade risk gate.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python place_order.py --symbol AAPL --action BUY --quantity 10 --order-type MKT
  python place_order.py --symbol AAPL --action SELL --quantity 5 --order-type LMT --limit-price 180.00
  python place_order.py --symbol AAPL --action BUY --quantity 10 --order-type MKT --live
  python place_order.py --symbol SPY --action BUY --quantity 100 --order-type MKT --simulation
        """,
    )
    # Connection
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"TWS hostname or IP (default: {DEFAULT_HOST})")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--paper", dest="port", action="store_const", const=PORT_PAPER,
                      help=f"Paper trading (port {PORT_PAPER}, default)")
    mode.add_argument("--live", dest="port", action="store_const", const=PORT_LIVE,
                      help=f"Live trading (port {PORT_LIVE})")
    mode.add_argument("--port", dest="port", type=int, help="Custom port number")
    parser.set_defaults(port=PORT_PAPER)

    # Contract
    parser.add_argument("--symbol", required=True, help="Ticker symbol (e.g. AAPL)")
    parser.add_argument("--sec-type", default="STK",
                        help="Security type: STK, OPT, FUT, FOP (default: STK)")
    parser.add_argument("--currency", default="USD", help="Currency (default: USD)")
    parser.add_argument("--exchange", default="SMART", help="Exchange (default: SMART)")
    parser.add_argument("--expiry", help="Contract expiry YYYYMMDD (for OPT/FUT)")
    parser.add_argument("--strike", type=float, help="Strike price (for OPT)")
    parser.add_argument("--right", choices=["C", "P"], help="Option right: C or P (for OPT)")

    # Order
    parser.add_argument("--action", required=True, choices=["BUY", "SELL"],
                        help="Order action: BUY or SELL")
    parser.add_argument("--quantity", required=True, type=float, help="Number of shares/contracts")
    parser.add_argument("--order-type", required=True, choices=["MKT", "LMT"],
                        help="Order type: MKT or LMT")
    parser.add_argument("--limit-price", type=float, help="Limit price (required for LMT)")
    parser.add_argument("--tif", default="DAY",
                        choices=["DAY", "GTC", "IOC", "FOK"],
                        help="Time in force (default: DAY)")
    parser.add_argument("--stop-price", type=float, dest="stop_price",
                        help="Stop-loss price for max-loss estimation")

    # Risk gate
    parser.add_argument("--simulation", action="store_true",
                        help="Simulation/test mode: bypass risk gate failures and place the order anyway")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        result = asyncio.run(place_order(args.host, args.port, CLIENT_ID, args,
                                         simulation=args.simulation))
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

    if result.get("status") in ("risk_check_failed", "broker_error"):
        print(json.dumps(result, indent=2), file=sys.stderr)
        sys.exit(2)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
