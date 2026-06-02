#!/usr/bin/env python3
"""
get_quote_snapshot.py — Pre-trade quote, session, and liquidity snapshot via IB TWS.

Retrieves current market data for a given instrument and returns a structured
snapshot including bid/ask/last, spread metrics, session state, halt flag,
stale-data detection, volume/ADV liquidity metrics, and (for options) Greeks
and open interest.  Unavailable fields are returned as null with a warning code
so callers get a machine-readable signal rather than a silent blank.

Hard rejects (halt, stale quote, insufficient liquidity) are written to stderr
with exit code 2 and rejected=true in the payload.

Usage:
    python get_quote_snapshot.py --symbol AAPL [options]
    python get_quote_snapshot.py --symbol SPY --quantity 500
    python get_quote_snapshot.py --symbol AAPL --sec-type OPT \\
        --expiry 20260619 --strike 180 --right C --quantity 10

Connection ID: 1009 (see tools/connection_ids.json)
"""

import argparse
import asyncio
import contextlib
import io
import json
import math
import sys
from datetime import datetime, timezone, timedelta

try:
    from ib_async import IB, Contract, Stock, Option, Future
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
CLIENT_ID = 1009
CONNECT_TIMEOUT = 10
MARKET_DATA_TIMEOUT = 5  # seconds to wait for market data (split across live + delayed attempts)

DEFAULT_STALE_THRESHOLD = 60        # seconds — quote age before stale flag
DEFAULT_MAX_SPREAD_PCT = 0.5        # percent — spread warning threshold
DEFAULT_MAX_ORDER_PCT_ADV = 10.0    # percent — ADV warn threshold
DEFAULT_HARD_REJECT_ORDER_PCT_ADV = 25.0  # percent — ADV hard-reject threshold

IB_HALT_HALTED = 1
IB_HALT_END_PENDING = 2
IB_UNSET = 1.7976931348623157e308

DATA_TYPE_NAMES = {1: "live", 2: "frozen", 3: "delayed", 4: "delayed_frozen"}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_float(val) -> "float | None":
    """Return a plain float, or None for IB unset sentinel / NaN / Inf."""
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f) or f == IB_UNSET:
        return None
    return f


def _detect_session_state() -> str:
    """Return session bucket (premarket/regular/after_hours/closed), DST-aware."""
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except ImportError:
        # Python <3.9 fallback: approximate DST by month/day
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


def _build_contract(args: argparse.Namespace) -> Contract:
    sec_type = args.sec_type.upper()
    if sec_type == "STK":
        return Stock(args.symbol, args.exchange or "SMART", args.currency)
    if sec_type in ("OPT", "FOP"):
        contract = Option(
            args.symbol,
            args.expiry or "",
            args.strike or 0.0,
            args.right or "C",
            args.exchange or "SMART",
            currency=args.currency,
        )
        contract.secType = sec_type
        return contract
    if sec_type == "FUT":
        return Future(
            args.symbol,
            args.expiry or "",
            args.exchange or "SMART",
            currency=args.currency,
        )
    contract = Contract()
    contract.symbol = args.symbol
    contract.secType = sec_type
    contract.currency = args.currency
    contract.exchange = args.exchange or "SMART"
    return contract


def _build_snapshot(ticker, args: argparse.Namespace, sec_type: str, data_type: int) -> dict:
    now_utc = datetime.now(timezone.utc)

    bid = _safe_float(ticker.bid)
    ask = _safe_float(ticker.ask)
    last = _safe_float(ticker.last)
    bid_size = _safe_float(ticker.bidSize)
    ask_size = _safe_float(ticker.askSize)
    volume = _safe_float(ticker.volume)
    adv = _safe_float(ticker.avVolume)
    close = _safe_float(ticker.close)

    # Spread
    spread = spread_pct = None
    if bid is not None and ask is not None and bid > 0:
        spread = round(ask - bid, 6)
        mid = (bid + ask) / 2.0
        if mid > 0:
            spread_pct = round((spread / mid) * 100.0, 4)

    # Quote timestamp and staleness
    quote_time = None
    age_seconds = None
    is_stale = False
    raw_time = getattr(ticker, "time", None)
    if raw_time is not None:
        if isinstance(raw_time, datetime):
            qt_utc = raw_time.astimezone(timezone.utc) if raw_time.tzinfo else raw_time.replace(tzinfo=timezone.utc)
            quote_time = qt_utc.isoformat().replace("+00:00", "Z")
            age_seconds = round((now_utc - qt_utc).total_seconds(), 1)
            is_stale = age_seconds > args.stale_threshold
        else:
            quote_time = str(raw_time)
    elif bid is None and ask is None and last is None:
        is_stale = True  # no data at all

    data_source = DATA_TYPE_NAMES.get(data_type, "unknown")

    # Halt
    halt_raw = getattr(ticker, "halted", 0) or 0
    try:
        halt_flag = int(halt_raw)
    except (TypeError, ValueError):
        halt_flag = 0
    is_halted = halt_flag in (IB_HALT_HALTED, IB_HALT_END_PENDING)

    # Session
    session_state = _detect_session_state()

    # Shortable shares (STK only)
    shortable_shares = None
    if sec_type == "STK":
        shortable_shares = _safe_float(getattr(ticker, "shortableShares", None))

    # Options block
    options_out = None
    if sec_type in ("OPT", "FOP"):
        greeks = getattr(ticker, "modelGreeks", None) or getattr(ticker, "lastGreeks", None)
        iv = delta = gamma = theta = vega = underlying_price = None
        if greeks is not None:
            iv = _safe_float(getattr(greeks, "impliedVol", None))
            delta = _safe_float(getattr(greeks, "delta", None))
            gamma = _safe_float(getattr(greeks, "gamma", None))
            theta = _safe_float(getattr(greeks, "theta", None))
            vega = _safe_float(getattr(greeks, "vega", None))
            underlying_price = _safe_float(getattr(greeks, "undPrice", None))
        open_interest = _safe_float(getattr(ticker, "openInterest", None))
        options_out = {
            "underlying_price": underlying_price,
            "iv": iv,
            "delta": delta,
            "gamma": gamma,
            "theta": theta,
            "vega": vega,
            "open_interest": open_interest,
            "option_volume": volume,
        }

    # Liquidity
    order_size = args.quantity if args.quantity else None
    order_pct_adv = None
    if order_size is not None and adv is not None and adv > 0:
        order_pct_adv = round((order_size / adv) * 100.0, 4)

    # Warnings (machine-readable)
    warnings: list[dict] = []
    if bid is None and ask is None:
        warnings.append({"code": "NO_BID_ASK", "message": "No bid/ask data available"})
    if is_halted:
        warnings.append({"code": "HALTED", "message": f"Instrument is halted (halt_flag={halt_flag})"})
    if is_stale:
        age_str = f"{age_seconds}s" if age_seconds is not None else "unknown age"
        warnings.append({"code": "STALE_QUOTE",
                         "message": f"Quote is stale ({age_str}, threshold={args.stale_threshold}s)"})
    if session_state == "closed":
        warnings.append({"code": "CLOSED_SESSION", "message": "Market is closed"})
    elif session_state in ("premarket", "after_hours"):
        warnings.append({"code": "EXTENDED_HOURS",
                         "message": f"Trading in {session_state.replace('_', '-')} session"})
    if spread_pct is not None and spread_pct > args.max_spread_pct:
        warnings.append({"code": "WIDE_SPREAD",
                         "message": f"Spread {spread_pct:.4f}% exceeds threshold {args.max_spread_pct}%"})
    if order_pct_adv is not None and order_pct_adv > args.hard_reject_order_pct_adv:
        warnings.append({"code": "INSUFFICIENT_LIQUIDITY",
                         "message": (f"Order is {order_pct_adv:.2f}% of ADV "
                                     f"(hard-reject threshold: {args.hard_reject_order_pct_adv}%)")})
    elif order_pct_adv is not None and order_pct_adv > args.max_order_pct_adv:
        warnings.append({"code": "LOW_LIQUIDITY",
                         "message": (f"Order is {order_pct_adv:.2f}% of ADV "
                                     f"(warning threshold: {args.max_order_pct_adv}%)")})

    # Unavailable-field warning codes — every null must be explicit
    if quote_time is None:
        warnings.append({"code": "NO_QUOTE_TIME", "message": "Quote timestamp unavailable"})
    if volume is None:
        warnings.append({"code": "NO_VOLUME", "message": "Volume data unavailable"})
    if adv is None:
        warnings.append({"code": "NO_ADV", "message": "Average daily volume (ADV) unavailable"})
    if sec_type == "STK" and shortable_shares is None:
        warnings.append({"code": "NO_SHORTABLE_SHARES", "message": "Shortable shares data unavailable"})
    if sec_type in ("OPT", "FOP") and options_out is not None:
        if all(options_out.get(k) is None for k in ("delta", "gamma", "theta", "vega", "iv")):
            warnings.append({"code": "NO_OPTION_GREEKS", "message": "Option Greeks unavailable"})
        if options_out.get("open_interest") is None:
            warnings.append({"code": "NO_OPEN_INTEREST", "message": "Open interest unavailable"})

    # Detect total data blackout: all usable price fields are null
    no_market_data = (bid is None and ask is None and last is None and close is None)
    allow_no_data = getattr(args, "allow_no_data", False)

    if no_market_data:
        warnings.append({"code": "NO_MARKET_DATA",
                         "message": "No usable price data available (bid, ask, last, close are all null)"})
    if no_market_data and allow_no_data:
        warnings.append({"code": "OVERRIDE_NO_DATA",
                         "message": "NO_MARKET_DATA rejection bypassed by --allow-no-data flag"})

    # Hard rejection — first match wins
    rejected = False
    rejection_reason = None
    if is_halted:
        rejected = True
        rejection_reason = "HALTED"
    elif is_stale and (bid is not None or ask is not None):
        # Reject only when we can confirm data is stale (have prices but stale timestamp)
        rejected = True
        rejection_reason = "STALE_QUOTE"
    elif no_market_data and session_state == "regular" and not allow_no_data:
        rejected = True
        rejection_reason = "NO_MARKET_DATA"
    elif bid is None and ask is None and session_state == "regular" and not allow_no_data:
        rejected = True
        rejection_reason = "NO_BID_ASK"
    elif order_pct_adv is not None and order_pct_adv > args.hard_reject_order_pct_adv:
        rejected = True
        rejection_reason = "INSUFFICIENT_LIQUIDITY"

    return {
        "timestamp": utc_timestamp(),
        "symbol": args.symbol,
        "sec_type": sec_type,
        "currency": args.currency,
        "exchange": args.exchange or "SMART",
        "quote": {
            "bid": bid,
            "ask": ask,
            "last": last,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "spread": spread,
            "spread_pct": spread_pct,
            "close": close,
            "quote_time": quote_time,
            "data_source": data_source,
        },
        "session": {
            "state": session_state,
            "is_halted": is_halted,
            "halt_flag": halt_flag,
        },
        "staleness": {
            "is_stale": is_stale,
            "age_seconds": age_seconds,
            "stale_threshold_seconds": args.stale_threshold,
        },
        "liquidity": {
            "volume": volume,
            "adv": adv,
            "order_size": order_size,
            "order_pct_adv": order_pct_adv,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "shortable_shares": shortable_shares,
        },
        "options": options_out,
        "warnings": warnings,
        "rejected": rejected,
        "rejection_reason": rejection_reason,
    }


async def get_quote_snapshot(host: str, port: int, client_id: int, args: argparse.Namespace) -> dict:
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
        with contextlib.redirect_stdout(sys.stderr):
            contract = _build_contract(args)
            qualified = await ib.qualifyContractsAsync(contract)
            if not qualified or qualified[0] is None:
                raise ValueError(
                    f"Contract not found in IB system: {args.symbol} {args.sec_type}"
                    + (f" {args.expiry} {args.strike} {args.right}" if args.sec_type.upper() in ("OPT", "FOP") else "")
                    + " — IB Error 200. For paper accounts check options market data API permissions in IB Client Portal."
                )

            # Request delayed data (type 3) so paper accounts without live
            # subscriptions return actual quotes instead of all-null snapshots.
            ib.reqMarketDataType(3)

            # Track data type reported by TWS for source labeling
            data_type_ref = [1]

            def _on_market_data_type(req_id, market_data_type):
                data_type_ref[0] = market_data_type

            try:
                ib.client.wrapper.marketDataType = _on_market_data_type
            except AttributeError:
                pass  # older ib_async versions; data_type stays 1 (live)

            # Request 1: snapshot=True, empty genericTickList → core price fields
            # (bid, ask, last, close, volume, quote_time).
            # snapshot=True + any non-empty genericTickList returns all-null against paper TWS.
            ticker = ib.reqMktData(
                contract,
                genericTickList="",
                snapshot=True,
                regulatorySnapshot=False,
            )
            try:
                await asyncio.sleep(MARKET_DATA_TIMEOUT)
            except asyncio.CancelledError:
                raise
            # snapshot=True auto-cancels on the IB side after data delivery

            # Request 2: snapshot=False, genericTickList='165,236' → avVolume + shortable shares
            # (tick 165 = avgVolume/ADV, tick 236 = shortableShares)
            ticker_aux = ib.reqMktData(
                contract,
                genericTickList="165,236",
                snapshot=False,
                regulatorySnapshot=False,
            )
            try:
                await asyncio.sleep(MARKET_DATA_TIMEOUT)
            except asyncio.CancelledError:
                raise
            ib.cancelMktData(contract)

            # If ib_async returned different ticker objects, copy aux fields to the core ticker
            if ticker_aux is not ticker:
                ticker.avVolume = getattr(ticker_aux, "avVolume", None)
                ticker.shortableShares = getattr(ticker_aux, "shortableShares", None)

            return _build_snapshot(ticker, args, args.sec_type.upper(), data_type_ref[0])
    finally:
        ib.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pre-trade quote, session, and liquidity snapshot from Interactive Brokers TWS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python get_quote_snapshot.py --symbol AAPL
  python get_quote_snapshot.py --symbol SPY --quantity 500
  python get_quote_snapshot.py --symbol AAPL --sec-type OPT \\
      --expiry 20260619 --strike 180 --right C --quantity 10
  python get_quote_snapshot.py --symbol AAPL --max-spread-pct 0.3 --stale-threshold 30
        """,
    )
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"TWS hostname or IP (default: {DEFAULT_HOST})")

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

    # Quantity for liquidity checks
    parser.add_argument("--quantity", type=float, default=None,
                        help="Proposed order size (enables order-size-as-%%ADV liquidity check)")

    # Market data type
    parser.add_argument("--delayed", action="store_true",
                        help="Request delayed market data (type 3); skips live attempt. "
                             "Use for paper accounts without live data subscriptions.")

    # Thresholds
    parser.add_argument("--stale-threshold", type=float, default=DEFAULT_STALE_THRESHOLD,
                        help=f"Seconds before a quote is stale (default: {DEFAULT_STALE_THRESHOLD})")
    parser.add_argument("--max-spread-pct", type=float, default=DEFAULT_MAX_SPREAD_PCT,
                        help=f"Spread%% warn threshold (default: {DEFAULT_MAX_SPREAD_PCT})")
    parser.add_argument("--max-order-pct-adv", type=float, default=DEFAULT_MAX_ORDER_PCT_ADV,
                        help=f"Order size as %% of ADV warn threshold (default: {DEFAULT_MAX_ORDER_PCT_ADV})")
    parser.add_argument("--hard-reject-order-pct-adv", type=float,
                        default=DEFAULT_HARD_REJECT_ORDER_PCT_ADV,
                        help=f"Order size as %% of ADV hard-reject threshold (default: {DEFAULT_HARD_REJECT_ORDER_PCT_ADV})")
    parser.add_argument("--allow-no-data", action="store_true", default=False,
                        help="Override NO_MARKET_DATA rejection during regular session (auditable; recorded in warnings)")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        result = asyncio.run(get_quote_snapshot(args.host, args.port, CLIENT_ID, args))
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
            json.dumps({
                "error": str(exc),
                "status": "contract_not_found",
                "timestamp": utc_timestamp(),
            }),
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

    if result.get("rejected"):
        print(json.dumps(result, indent=2), file=sys.stderr)
        sys.exit(2)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
