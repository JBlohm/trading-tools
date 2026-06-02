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
import math
import pathlib
import sys
import uuid
from datetime import datetime, timezone, timedelta

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

DEFAULT_STALE_THRESHOLD = 60   # seconds
SNAPSHOT_TIMEOUT = 3           # seconds to wait for market data snapshot
_HALT_HALTED = 1
_HALT_END_PENDING = 2

_REJECTED_STATUSES = frozenset({"Inactive", "ApiCancelled", "ApiRejected"})
_MARGIN_TAGS = frozenset({"NetLiquidation", "ExcessLiquidity", "BuyingPower", "InitMarginReq", "MaintMarginReq"})


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_float(val) -> "float | None":
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f) or f == IB_UNSET_PRICE:
        return None
    return f


def _detect_et_session_state() -> str:
    """Return session bucket (premarket/regular/after_hours/closed), DST-aware."""
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except ImportError:
        now_utc = datetime.now(timezone.utc)
        m, d = now_utc.month, now_utc.day
        is_dst = (3 < m < 11) or (m == 3 and d >= 8) or (m == 11 and d < 7)
        now_et = now_utc + timedelta(hours=-4 if is_dst else -5)
    if now_et.weekday() >= 5:
        return "closed"
    hour = now_et.hour + now_et.minute / 60.0
    if 4.0 <= hour < 9.5:
        return "premarket"
    if 9.5 <= hour < 16.0:
        return "regular"
    if 16.0 <= hour < 20.0:
        return "after_hours"
    return "closed"


async def _fetch_pre_trade_snapshot(ib, contract, symbol: str, sec_type: str,
                                    quantity, stale_threshold: float,
                                    allow_no_data: bool = False) -> dict:
    """Fetch a market data snapshot for the pre-trade gate and audit record."""
    now_utc = datetime.now(timezone.utc)

    # Request 1: snapshot=True, empty genericTickList → core price fields
    # (bid, ask, last, close, volume, quote_time).
    # snapshot=True + any non-empty genericTickList returns all-null against paper TWS.
    ticker = ib.reqMktData(contract, genericTickList="",
                           snapshot=True, regulatorySnapshot=False)
    try:
        await asyncio.sleep(SNAPSHOT_TIMEOUT)
    except asyncio.CancelledError:
        raise
    # snapshot=True auto-cancels on the IB side after data delivery

    # Request 2: snapshot=False, genericTickList='165,236' → avVolume + shortable shares
    # (tick 165 = avgVolume/ADV, tick 236 = shortableShares)
    # For OPT/FOP, also include 100 (option volume), 101 (open interest), 106 (greeks)
    ticks = "165,236"
    if sec_type.upper() in ("OPT", "FOP"):
        ticks += ",100,101,106"

    ticker_aux = ib.reqMktData(contract, genericTickList=ticks,
                               snapshot=False, regulatorySnapshot=False)
    try:
        await asyncio.sleep(SNAPSHOT_TIMEOUT)
    except asyncio.CancelledError:
        raise
    ib.cancelMktData(contract)

    # If ib_async returned different ticker objects, copy aux fields to the core ticker
    if ticker_aux is not ticker:
        ticker.avVolume = getattr(ticker_aux, "avVolume", None)
        ticker.shortableShares = getattr(ticker_aux, "shortableShares", None)
        if sec_type.upper() in ("OPT", "FOP"):
            ticker.openInterest = getattr(ticker_aux, "openInterest", None)
            ticker.modelGreeks = getattr(ticker_aux, "modelGreeks", None)
            ticker.lastGreeks = getattr(ticker_aux, "lastGreeks", None)
            if ticker.volume is None:
                ticker.volume = getattr(ticker_aux, "volume", None)

    bid = _safe_float(ticker.bid)
    ask = _safe_float(ticker.ask)
    last = _safe_float(ticker.last)
    close = _safe_float(getattr(ticker, "close", None))
    volume = _safe_float(getattr(ticker, "volume", None))
    adv = _safe_float(getattr(ticker, "avVolume", None))

    spread = spread_pct = None
    if bid is not None and ask is not None and bid > 0:
        spread = round(ask - bid, 6)
        mid = (bid + ask) / 2.0
        if mid > 0:
            spread_pct = round((spread / mid) * 100.0, 4)

    quote_time = None
    age_seconds = None
    is_stale = False
    raw_time = getattr(ticker, "time", None)
    if raw_time is not None and isinstance(raw_time, datetime):
        qt_utc = (raw_time.astimezone(timezone.utc) if raw_time.tzinfo
                  else raw_time.replace(tzinfo=timezone.utc))
        quote_time = qt_utc.isoformat().replace("+00:00", "Z")
        age_seconds = round((now_utc - qt_utc).total_seconds(), 1)
        is_stale = age_seconds > stale_threshold
    elif bid is None and ask is None and last is None:
        is_stale = True

    halt_raw = getattr(ticker, "halted", 0) or 0
    try:
        halt_flag = int(halt_raw)
    except (TypeError, ValueError):
        halt_flag = 0
    is_halted = halt_flag in (_HALT_HALTED, _HALT_END_PENDING)

    session_state = _detect_et_session_state()

    ref_price = None
    for p in (last, bid, ask, close):
        if p is not None and p > 0:
            ref_price = p
            break

    # Detect total data blackout: all usable price fields are null
    no_market_data = (bid is None and ask is None and last is None and close is None)

    warnings: list[dict] = []
    if is_halted:
        warnings.append({"code": "HALTED",
                         "message": f"Instrument is halted (halt_flag={halt_flag})"})
    if is_stale:
        age_str = f"{age_seconds}s" if age_seconds is not None else "unknown age"
        warnings.append({"code": "STALE_QUOTE",
                         "message": f"Quote is stale ({age_str}, threshold={stale_threshold}s)"})
    if bid is None and ask is None:
        warnings.append({"code": "NO_BID_ASK", "message": "No bid/ask data available"})
    if quote_time is None:
        warnings.append({"code": "NO_QUOTE_TIME", "message": "Quote timestamp unavailable"})
    if volume is None:
        warnings.append({"code": "NO_VOLUME", "message": "Volume data unavailable"})
    if adv is None:
        warnings.append({"code": "NO_ADV",
                         "message": "Average daily volume (ADV) unavailable"})
    if session_state in ("premarket", "after_hours"):
        warnings.append({"code": "EXTENDED_HOURS",
                         "message": f"Trading in {session_state.replace('_', '-')} session"})
    elif session_state == "closed":
        warnings.append({"code": "CLOSED_SESSION", "message": "Market is closed"})
    if no_market_data:
        warnings.append({"code": "NO_MARKET_DATA",
                         "message": "No usable price data available (bid, ask, last, close are all null)"})
    if no_market_data and allow_no_data:
        warnings.append({"code": "OVERRIDE_NO_DATA",
                         "message": "NO_MARKET_DATA rejection bypassed by --allow-no-data flag"})

    rejected = False
    rejection_reason = None
    if is_halted:
        rejected = True
        rejection_reason = "HALTED"
    elif is_stale and (bid is not None or ask is not None):
        rejected = True
        rejection_reason = "STALE_QUOTE"
    elif no_market_data and session_state == "regular" and not allow_no_data:
        rejected = True
        rejection_reason = "NO_MARKET_DATA"

    return {
        "symbol": symbol,
        "sec_type": sec_type,
        "snapshot_timestamp": now_utc.isoformat().replace("+00:00", "Z"),
        "quote": {
            "bid": bid, "ask": ask, "last": last, "close": close,
            "spread": spread, "spread_pct": spread_pct,
            "quote_time": quote_time,
        },
        "session": {
            "state": session_state,
            "is_halted": is_halted,
            "halt_flag": halt_flag,
        },
        "staleness": {
            "is_stale": is_stale,
            "age_seconds": age_seconds,
            "stale_threshold_seconds": stale_threshold,
        },
        "liquidity": {"volume": volume, "adv": adv},
        "warnings": warnings,
        "rejected": rejected,
        "rejection_reason": rejection_reason,
        "ref_price": ref_price,
    }


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
    quote_snapshot: dict,
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
        "quote_snapshot": quote_snapshot,
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

            # --- Pre-trade quote snapshot (always fetched for session/halt/staleness gate) ---
            stale_threshold = getattr(args, "stale_threshold", DEFAULT_STALE_THRESHOLD)
            allow_no_data = getattr(args, "allow_no_data", False)
            quote_snapshot = await _fetch_pre_trade_snapshot(
                ib, contract, args.symbol, args.sec_type.upper(),
                args.quantity, stale_threshold, allow_no_data,
            )
            if quote_snapshot["rejected"] and not simulation:
                failure_message = f"Pre-trade snapshot rejected: {quote_snapshot['rejection_reason']}"
                return {
                    "audit_id": audit_id,
                    "timestamp": utc_timestamp(),
                    "status": "risk_check_failed",
                    "verdict": "fail",
                    "failures": [{
                        "reason_code": quote_snapshot["rejection_reason"],
                        "message": failure_message,
                        "check": "pre_trade_snapshot",
                    }],
                    "risk_check": {
                        "passed": False,
                        "failures": [failure_message],
                        "quote_snapshot": quote_snapshot,
                    },
                    "quote_snapshot": quote_snapshot,
                }

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
            risk_result["quote_snapshot"] = quote_snapshot

            if risk_result["verdict"] != "pass" and not simulation:
                return {
                    "audit_id": audit_id,
                    "timestamp": utc_timestamp(),
                    "status": "risk_check_failed",
                    "verdict": risk_result["verdict"],
                    "failures": risk_result["failures"],
                    "risk_check": risk_result,
                    "quote_snapshot": quote_snapshot,
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
                    position_snapshot, margin_snapshot, quote_snapshot,
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
                    "quote_snapshot": quote_snapshot,
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

    # Snapshot thresholds
    parser.add_argument("--stale-threshold", type=float, default=DEFAULT_STALE_THRESHOLD,
                        help=f"Quote age in seconds before the instrument is considered stale (default: {DEFAULT_STALE_THRESHOLD})")
    parser.add_argument("--allow-no-data", action="store_true", default=False,
                        help="Override NO_MARKET_DATA rejection during regular session (auditable; recorded in warnings)")

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
