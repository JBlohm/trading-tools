#!/usr/bin/env python3
"""
modify_order.py — Modify an open order via Interactive Brokers TWS.

Supports two modification modes:
  1. Native modify (default): updates the order in-place using the same order ID.
     Price, quantity, and TIF can be changed without cancelling.
  2. Cancel/replace (--cancel-replace): cancels the existing order and places a
     new one. Use when changing order type (e.g. LMT→MKT) or when native modify
     is not appropriate.

Risk pre-check is re-run whenever a risk-material field changes (quantity,
limit price, order type, or TIF). A failed risk check preserves the original
live order unchanged.

Returns modified order details as JSON to stdout, or an error JSON to stderr.

Usage:
    python modify_order.py --order-id 12345 --limit-price 175.00
    python modify_order.py --order-id 12345 --quantity 5 --limit-price 175.00
    python modify_order.py --order-id 12345 --order-type MKT --cancel-replace

Connection ID: 1010 (see tools/connection_ids.json)
"""

import argparse
import asyncio
import contextlib
import io
import json
import sys
from datetime import datetime, timezone

try:
    from ib_async import IB, LimitOrder, MarketOrder
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
CLIENT_ID = 1010
CONNECT_TIMEOUT = 10
ACCOUNT_DATA_TIMEOUT = 5
MODIFY_WAIT = 1.0
CANCEL_WAIT = 2.0
DEFAULT_MAX_NOTIONAL = 100_000.0
DEFAULT_MAX_PCT_NLV = 10.0
IB_UNSET_PRICE = 1.7976931348623157e308


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def _connect(host: str, port: int, client_id: int) -> IB:
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
    return ib


async def _find_open_trade(ib: IB, order_id: int):
    trades = await ib.reqAllOpenOrdersAsync()
    for trade in trades:
        if trade.order.orderId == order_id:
            return trade
    return None


async def _get_nlv_and_excess(ib: IB) -> tuple[float, float]:
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


def _risk_material_changed(original_order, args) -> bool:
    if args.quantity is not None and args.quantity != float(original_order.totalQuantity):
        return True
    orig_lmt = original_order.lmtPrice if original_order.lmtPrice != IB_UNSET_PRICE else None
    if args.limit_price is not None and args.limit_price != orig_lmt:
        return True
    if args.order_type is not None and args.order_type.upper() != original_order.orderType:
        return True
    if args.tif is not None and args.tif.upper() != original_order.tif:
        return True
    return False


async def _run_risk_check(ib: IB, new_quantity: float, ref_price: float, args) -> dict:
    nlv, excess = await _get_nlv_and_excess(ib)
    estimated_notional = ref_price * new_quantity

    risk_check = {
        "nlv": nlv,
        "excess_liquidity": excess,
        "estimated_notional": estimated_notional,
        "max_notional_limit": args.max_notional,
        "max_pct_nlv": args.max_pct_nlv,
        "passed": False,
        "failures": [],
    }

    if estimated_notional > args.max_notional:
        risk_check["failures"].append(
            f"Estimated notional {estimated_notional:,.2f} exceeds limit {args.max_notional:,.2f}"
        )
    if nlv > 0 and estimated_notional > (nlv * args.max_pct_nlv / 100.0):
        risk_check["failures"].append(
            f"Estimated notional {estimated_notional:,.2f} exceeds {args.max_pct_nlv}% of NLV ({nlv:,.2f})"
        )
    if excess <= 0:
        risk_check["failures"].append(
            f"Excess liquidity is {excess:,.2f} — account may be margin-called"
        )

    risk_check["passed"] = len(risk_check["failures"]) == 0
    return risk_check


def _trade_to_dict(trade, mode: str, risk_check: dict | None = None) -> dict:
    contract = trade.contract
    order = trade.order
    status = trade.orderStatus
    result = {
        "timestamp": utc_timestamp(),
        "modification_mode": mode,
        "order_id": order.orderId,
        "perm_id": order.permId,
        "symbol": contract.symbol,
        "sec_type": contract.secType,
        "currency": contract.currency,
        "exchange": contract.exchange,
        "action": order.action,
        "order_type": order.orderType,
        "quantity": order.totalQuantity,
        "lmt_price": order.lmtPrice if order.lmtPrice != IB_UNSET_PRICE else None,
        "tif": order.tif,
        "order_status": status.status,
    }
    if risk_check is not None:
        result["risk_check"] = risk_check
    return result


async def modify_order(host: str, port: int, client_id: int, args: argparse.Namespace) -> dict:
    ib = await _connect(host, port, client_id)

    try:
        with contextlib.redirect_stdout(sys.stderr):
            target_trade = await _find_open_trade(ib, args.order_id)

        if target_trade is None:
            return {
                "timestamp": utc_timestamp(),
                "status": "not_found",
                "order_id": args.order_id,
                "message": f"No open order found with order_id {args.order_id}",
            }

        original_order = target_trade.order
        contract = target_trade.contract

        # Reconnect to the order's owning client if different
        order_client_id = getattr(original_order, "clientId", client_id) or client_id
        if order_client_id != client_id:
            ib.disconnect()
            ib = await _connect(host, port, order_client_id)
            with contextlib.redirect_stdout(sys.stderr):
                target_trade = await _find_open_trade(ib, args.order_id)
            if target_trade is None:
                return {
                    "timestamp": utc_timestamp(),
                    "status": "not_found",
                    "order_id": args.order_id,
                    "message": (
                        f"Order {args.order_id} was found on client {order_client_id} "
                        "but is no longer open"
                    ),
                }
            original_order = target_trade.order
            contract = target_trade.contract

        # Determine effective new parameters (fall back to original if not specified)
        new_quantity = args.quantity if args.quantity is not None else float(original_order.totalQuantity)
        new_order_type = args.order_type.upper() if args.order_type else original_order.orderType
        new_tif = args.tif.upper() if args.tif else original_order.tif
        orig_lmt = original_order.lmtPrice if original_order.lmtPrice != IB_UNSET_PRICE else None
        new_lmt_price = args.limit_price if args.limit_price is not None else orig_lmt

        if new_order_type == "LMT" and new_lmt_price is None:
            return {
                "timestamp": utc_timestamp(),
                "status": "invalid_input",
                "order_id": args.order_id,
                "message": "--limit-price is required for LMT orders",
            }

        # Re-run risk pre-check if any risk-material field changed
        risk_check = None
        if _risk_material_changed(original_order, args):
            ref_price = new_lmt_price or 0.0
            risk_check = await _run_risk_check(ib, new_quantity, ref_price, args)
            if not risk_check["passed"]:
                return {
                    "timestamp": utc_timestamp(),
                    "status": "risk_check_failed",
                    "order_id": args.order_id,
                    "message": "Risk pre-check failed; original order left unchanged",
                    "risk_check": risk_check,
                }

        if args.cancel_replace:
            return await _do_cancel_replace(
                ib, target_trade, contract, new_quantity, new_order_type,
                new_lmt_price, new_tif, original_order, risk_check,
            )
        else:
            return await _do_native_modify(
                ib, target_trade, contract, new_quantity, new_order_type,
                new_lmt_price, new_tif, risk_check,
            )
    finally:
        ib.disconnect()


async def _do_native_modify(ib, trade, contract, new_quantity, new_order_type,
                            new_lmt_price, new_tif, risk_check):
    order = trade.order
    order.totalQuantity = new_quantity
    order.orderType = new_order_type
    order.tif = new_tif
    if new_order_type == "LMT":
        order.lmtPrice = new_lmt_price
    else:
        order.lmtPrice = IB_UNSET_PRICE

    with contextlib.redirect_stdout(sys.stderr):
        modified_trade = ib.placeOrder(contract, order)
        await asyncio.sleep(MODIFY_WAIT)

    result = _trade_to_dict(modified_trade, "native_modify", risk_check)
    result["status"] = "modified"
    return result


async def _do_cancel_replace(ib, trade, contract, new_quantity, new_order_type,
                             new_lmt_price, new_tif, original_order, risk_check):
    cancelled_order_id = original_order.orderId

    with contextlib.redirect_stdout(sys.stderr):
        ib.cancelOrder(trade.order)
        await asyncio.sleep(CANCEL_WAIT)

    if new_order_type == "LMT":
        replacement = LimitOrder(original_order.action, new_quantity, new_lmt_price, tif=new_tif)
    else:
        replacement = MarketOrder(original_order.action, new_quantity, tif=new_tif)

    with contextlib.redirect_stdout(sys.stderr):
        new_trade = ib.placeOrder(contract, replacement)
        await asyncio.sleep(MODIFY_WAIT)

    result = _trade_to_dict(new_trade, "cancel_replace", risk_check)
    result["status"] = "replaced"
    result["cancelled_order_id"] = cancelled_order_id
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Modify an open order via Interactive Brokers TWS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python modify_order.py --order-id 12345 --limit-price 175.00
  python modify_order.py --order-id 12345 --quantity 5 --limit-price 175.00
  python modify_order.py --order-id 12345 --limit-price 175.00 --cancel-replace
  python modify_order.py --order-id 12345 --order-type MKT --cancel-replace
        """,
    )
    parser.add_argument("--order-id", required=True, type=int, help="TWS order ID to modify")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"TWS hostname or IP (default: {DEFAULT_HOST})")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--paper", dest="port", action="store_const", const=PORT_PAPER,
                      help=f"Paper trading (port {PORT_PAPER}, default)")
    mode.add_argument("--live", dest="port", action="store_const", const=PORT_LIVE,
                      help=f"Live trading (port {PORT_LIVE})")
    mode.add_argument("--port", dest="port", type=int, help="Custom port number")
    parser.set_defaults(port=PORT_PAPER)

    # Modification parameters — all optional; omitted means keep original value
    parser.add_argument("--quantity", type=float, help="New quantity")
    parser.add_argument("--limit-price", type=float, help="New limit price")
    parser.add_argument("--order-type", choices=["MKT", "LMT"], help="New order type")
    parser.add_argument("--tif", choices=["DAY", "GTC", "IOC", "FOK"], help="New time-in-force")
    parser.add_argument("--cancel-replace", action="store_true",
                        help="Cancel existing order and place a new one instead of native modify")

    # Risk limits (re-checked when risk-material fields change)
    parser.add_argument("--max-notional", type=float, default=DEFAULT_MAX_NOTIONAL,
                        help=f"Max order notional in USD (default: {DEFAULT_MAX_NOTIONAL:,.0f})")
    parser.add_argument("--max-pct-nlv", type=float, default=DEFAULT_MAX_PCT_NLV,
                        help=f"Max order size as %% of NLV (default: {DEFAULT_MAX_PCT_NLV})")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        result = asyncio.run(modify_order(args.host, args.port, CLIENT_ID, args))
    except ConnectionError as exc:
        print(
            json.dumps({
                "error": str(exc),
                "status": "tws_unavailable",
                "hint": "The TWS API is temporarily not available. Please try again later.",
                "timestamp": utc_timestamp(),
            }),
            file=sys.stderr,
        )
        sys.exit(1)
    except ValueError as exc:
        print(
            json.dumps({"error": str(exc), "status": "invalid_input", "timestamp": utc_timestamp()}),
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as exc:
        print(
            json.dumps({
                "error": f"Unexpected error: {exc}",
                "status": "error",
                "timestamp": utc_timestamp(),
            }),
            file=sys.stderr,
        )
        sys.exit(1)

    if result.get("status") in ("not_found", "risk_check_failed", "invalid_input"):
        print(json.dumps(result, indent=2), file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
