#!/usr/bin/env python3
"""
pretrade_risk_check.py — Structured pre-trade risk gate.

Evaluates an order against portfolio state and configured risk limits before
allowing execution. Returns a structured pass/fail verdict with projected
exposures, margin impact, limit utilization, stress results, and an audit
timestamp.

Usage (CLI):
    python pretrade_risk_check.py --symbol AAPL --action BUY --quantity 10 --order-type MKT
    python pretrade_risk_check.py --symbol SPY --action SELL --quantity 5 --order-type LMT --limit-price 450

As a library:
    from tools.pretrade_risk_check import run_check, load_limits
    limits = load_limits()
    result = asyncio.run(run_check(ib, order_dict, limits))

Connection ID: 1012 (see tools/connection_ids.json)
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
    from ib_async import IB, Contract, Stock, Option, Future
except ImportError:
    print(
        json.dumps({"error": "ib_async not installed. Run: pip install ib_async", "status": "dependency_missing"}),
        file=sys.stderr,
    )
    sys.exit(1)

DEFAULT_HOST = "192.168.2.187"
PORT_PAPER = 7497
PORT_LIVE = 7496
CLIENT_ID = 1012
CONNECT_TIMEOUT = 10
ACCOUNT_DATA_TIMEOUT = 5
MARKET_DATA_TIMEOUT = 3
IB_UNSET = 1.7976931348623157e308

LIMITS_FILE = pathlib.Path(__file__).parent / "risk_limits.json"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _audit_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"pretrade-{ts}-{uuid.uuid4().hex[:8]}"


def load_limits() -> dict:
    if not LIMITS_FILE.exists():
        raise FileNotFoundError(f"Risk limits file not found: {LIMITS_FILE}")
    return json.loads(LIMITS_FILE.read_text())


def _build_contract(order: dict):
    sec_type = order.get("sec_type", "STK").upper()
    symbol = order["symbol"]
    currency = order.get("currency", "USD")
    exchange = order.get("exchange", "SMART")
    if sec_type == "STK":
        return Stock(symbol, exchange, currency)
    if sec_type in ("OPT", "FOP"):
        c = Option(
            symbol,
            order.get("expiry", ""),
            order.get("strike", 0.0),
            order.get("right", "C"),
            exchange,
            currency=currency,
        )
        c.secType = sec_type
        return c
    if sec_type == "FUT":
        return Future(symbol, order.get("expiry", ""), exchange, currency=currency)
    c = Contract()
    c.symbol = symbol
    c.secType = sec_type
    c.currency = currency
    c.exchange = exchange
    return c


async def _fetch_account_data(ib) -> dict:
    try:
        summary = await asyncio.wait_for(ib.accountSummaryAsync(), timeout=ACCOUNT_DATA_TIMEOUT)
    except asyncio.TimeoutError:
        summary = []

    tags: dict = {}
    for item in summary:
        try:
            v = float(item.value)
        except (TypeError, ValueError):
            continue
        # Accumulate across accounts (works for single and multi-account)
        tags[item.tag] = tags.get(item.tag, 0.0) + v

    nlv = tags.get("NetLiquidation", 0.0)
    return {
        "nlv": nlv,
        "excess_liquidity": tags.get("ExcessLiquidity", 0.0),
        "buying_power": tags.get("BuyingPower", 0.0),
        "init_margin_req": tags.get("InitMarginReq", 0.0),
        "maint_margin_req": tags.get("MaintMarginReq", 0.0),
        "available_funds": tags.get("AvailableFunds", 0.0),
        "gross_position_value": tags.get("GrossPositionValue", 0.0),
        "unrealized_pnl": tags.get("UnrealizedPnL", 0.0),
        "realized_pnl": tags.get("RealizedPnL", 0.0),
    }


def _extract_greeks(ticker) -> dict:
    if ticker is None:
        return {"delta": None, "gamma": None, "theta": None, "vega": None, "iv": None}
    g = getattr(ticker, "modelGreeks", None) or getattr(ticker, "lastGreeks", None)
    if g is None:
        return {"delta": None, "gamma": None, "theta": None, "vega": None, "iv": None}
    return {
        "delta": getattr(g, "delta", None),
        "gamma": getattr(g, "gamma", None),
        "theta": getattr(g, "theta", None),
        "vega": getattr(g, "vega", None),
        "iv": getattr(g, "impliedVol", None),
    }


async def _fetch_quote(ib, contract) -> dict:
    ticker = ib.reqMktData(contract, genericTickList="", snapshot=True, regulatorySnapshot=False)
    try:
        await asyncio.sleep(MARKET_DATA_TIMEOUT)
    except asyncio.CancelledError:
        raise

    def _clean(val):
        if val is None or val == IB_UNSET:
            return None
        return val

    bid = _clean(getattr(ticker, "bid", None))
    ask = _clean(getattr(ticker, "ask", None))
    last = _clean(getattr(ticker, "last", None))
    close = _clean(getattr(ticker, "close", None))
    quote_time = getattr(ticker, "time", None)

    ib.cancelMktData(contract)

    mid = (bid + ask) / 2.0 if bid is not None and ask is not None else None
    spread_pct = None
    if bid is not None and ask is not None and mid and mid > 0:
        spread_pct = (ask - bid) / mid * 100.0

    return {
        "bid": bid,
        "ask": ask,
        "last": last,
        "close": close,
        "mid": mid,
        "spread_pct": spread_pct,
        "quote_time": quote_time,
        "greeks": _extract_greeks(ticker),
    }


def _get_ref_price(order: dict, quote: dict) -> float:
    if order.get("limit_price"):
        return float(order["limit_price"])
    for key in ("mid", "last", "close"):
        v = quote.get(key)
        if v is not None:
            return float(v)
    return 0.0


def _portfolio_to_list(portfolio_items, greeks_map: dict | None = None) -> list:
    """Convert IB portfolio items to plain dicts.

    greeks_map: {conId: {"delta": ..., "gamma": ..., "theta": ..., "vega": ..., "iv": ...}}
    If provided, option greeks are populated from it; otherwise greeks are empty.
    """
    result = []
    for item in portfolio_items:
        c = item.contract
        g: dict = {}
        if greeks_map and c.secType in ("OPT", "FOP"):
            g = greeks_map.get(c.conId, {})
        result.append({
            "symbol": c.symbol,
            "sec_type": c.secType,
            "currency": getattr(c, "currency", "USD"),
            "position": item.position,
            "market_price": item.marketPrice,
            "market_value": item.marketValue,
            "unrealized_pnl": item.unrealizedPNL,
            "realized_pnl": item.realizedPNL,
            "greeks": g,
        })
    return result


async def _fetch_portfolio_greeks(ib, portfolio_items) -> dict:
    """Fetch option greeks for all option positions.

    Returns {conId: greeks_dict} for option/FOP positions.
    """
    opt_items = [it for it in portfolio_items if it.contract.secType in ("OPT", "FOP")]
    if not opt_items:
        return {}

    contract_map = {}
    for item in opt_items:
        t = ib.reqMktData(item.contract, genericTickList="", snapshot=True, regulatorySnapshot=False)
        contract_map[item.contract.conId] = (item.contract, t)

    try:
        await asyncio.sleep(MARKET_DATA_TIMEOUT)
    except asyncio.CancelledError:
        raise

    result = {}
    for con_id, (contract, ticker) in contract_map.items():
        ib.cancelMktData(contract)
        g = getattr(ticker, "modelGreeks", None) or getattr(ticker, "lastGreeks", None)
        if g is not None:
            result[con_id] = {
                "delta": getattr(g, "delta", None),
                "gamma": getattr(g, "gamma", None),
                "theta": getattr(g, "theta", None),
                "vega": getattr(g, "vega", None),
                "iv": getattr(g, "impliedVol", None),
            }
        else:
            result[con_id] = {}
    return result


def _open_orders_to_list(trades) -> list:
    result = []
    for t in trades:
        c = t.contract
        o = t.order
        s = t.orderStatus
        lmt = getattr(o, "lmtPrice", IB_UNSET)
        result.append({
            "symbol": c.symbol,
            "sec_type": c.secType,
            "action": o.action,
            "quantity": getattr(o, "totalQuantity", 0.0),
            "lmt_price": lmt if lmt != IB_UNSET else None,
            "status": s.status,
            "remaining": getattr(s, "remaining", getattr(o, "totalQuantity", 0.0)),
        })
    return result


def _compute_open_order_notional(open_orders: list) -> float:
    total = 0.0
    for o in open_orders:
        qty = o.get("remaining", o.get("quantity", 0.0))
        price = o.get("lmt_price") or 0.0
        mult = 100.0 if o.get("sec_type") in ("OPT", "FOP") else 1.0
        total += abs(qty) * price * mult
    return total


def _compute_portfolio_greeks(portfolio: list) -> dict:
    delta = gamma = theta = vega = 0.0
    for item in portfolio:
        if item.get("sec_type") not in ("OPT", "FOP"):
            continue
        pos = item.get("position", 0.0)
        g = item.get("greeks", {})
        if g.get("delta") is not None:
            delta += g["delta"] * pos * 100
        if g.get("gamma") is not None:
            gamma += g["gamma"] * pos * 100
        if g.get("theta") is not None:
            theta += g["theta"] * pos * 100
        if g.get("vega") is not None:
            vega += g["vega"] * pos * 100
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def _compute_projected_position(portfolio: list, order: dict, ref_price: float, nlv: float) -> dict:
    symbol = order["symbol"].upper()
    sec_type = order.get("sec_type", "STK").upper()
    action = order["action"].upper()
    qty = float(order["quantity"])
    signed_qty = qty if action == "BUY" else -qty
    mult = 100.0 if sec_type in ("OPT", "FOP") else 1.0

    current_qty = 0.0
    current_notional = 0.0
    for item in portfolio:
        if item.get("symbol", "").upper() == symbol and item.get("sec_type", "STK").upper() == sec_type:
            current_qty += item.get("position", 0.0)
            current_notional += abs(item.get("market_value", 0.0))

    projected_qty = current_qty + signed_qty
    order_notional = abs(signed_qty) * ref_price * mult
    projected_notional = abs(projected_qty) * ref_price * mult
    pct_nlv = (projected_notional / nlv * 100.0) if nlv > 0 else 0.0

    return {
        "symbol": symbol,
        "sec_type": sec_type,
        "current_quantity": current_qty,
        "order_quantity": signed_qty,
        "projected_quantity": projected_qty,
        "ref_price": ref_price,
        "current_notional": round(current_notional, 2),
        "order_notional": round(order_notional, 2),
        "projected_notional": round(projected_notional, 2),
        "pct_nlv": round(pct_nlv, 4),
    }


# ── Individual risk check functions ──────────────────────────────────────────


def _check_notional(order_notional: float, lim: dict) -> dict:
    limit = lim.get("max_order_notional_usd", float("inf"))
    passed = order_notional <= limit
    return {
        "check": "notional_limit",
        "status": "pass" if passed else "fail",
        "reason_code": None if passed else "ERR_NOTIONAL_LIMIT",
        "message": (None if passed else
                    f"Order notional {order_notional:,.2f} USD exceeds limit {limit:,.2f} USD"),
        "limit": limit,
        "actual": round(order_notional, 2),
    }


def _check_nlv_pct(order_notional: float, nlv: float, lim: dict) -> dict:
    limit_pct = lim.get("max_order_pct_nlv", float("inf"))
    actual_pct = (order_notional / nlv * 100.0) if nlv > 0 else float("inf")
    passed = actual_pct <= limit_pct
    return {
        "check": "nlv_pct_limit",
        "status": "pass" if passed else "fail",
        "reason_code": None if passed else "ERR_NLV_PCT_LIMIT",
        "message": (None if passed else
                    f"Order is {actual_pct:.2f}% of NLV, exceeds limit {limit_pct:.2f}%"),
        "limit_pct": limit_pct,
        "actual_pct": round(actual_pct, 4),
    }


def _check_concentration(projected: dict, lim: dict) -> dict:
    limit_pct = lim.get("max_single_name_pct_nlv", float("inf"))
    actual_pct = projected.get("pct_nlv", 0.0)
    passed = actual_pct <= limit_pct
    return {
        "check": "concentration_single_name",
        "status": "pass" if passed else "fail",
        "reason_code": None if passed else "ERR_CONCENTRATION_SINGLE_NAME",
        "message": (None if passed else
                    f"Projected {projected['symbol']} position is {actual_pct:.2f}% of NLV, "
                    f"exceeds single-name limit {limit_pct:.2f}%"),
        "limit_pct": limit_pct,
        "actual_pct": round(actual_pct, 4),
    }


def _check_margin_cushion(account: dict, lim: dict, margin_impact: dict | None = None) -> list:
    """Check margin cushion using projected post-order values when margin_impact is supplied."""
    nlv = account.get("nlv", 0.0)
    excess_before = account.get("excess_liquidity", 0.0)
    min_excess = lim.get("min_excess_liquidity_usd", 0.0)
    cushion_pct_limit = lim.get("margin_cushion_pct", 0.0)

    # Use projected post-order excess if margin_impact is available
    if margin_impact is not None:
        excess = margin_impact.get("excess_liquidity_after", excess_before)
    else:
        excess = excess_before

    results = []

    passed_abs = excess > min_excess
    results.append({
        "check": "excess_liquidity",
        "status": "pass" if passed_abs else "fail",
        "reason_code": None if passed_abs else "ERR_EXCESS_LIQUIDITY",
        "message": (None if passed_abs else
                    f"Projected excess liquidity {excess:,.2f} USD at or below floor {min_excess:,.2f} USD"),
        "limit": min_excess,
        "actual": excess,
        "actual_before_order": excess_before,
    })

    cushion_pct = (excess / nlv * 100.0) if nlv > 0 else 0.0
    passed_pct = cushion_pct >= cushion_pct_limit
    results.append({
        "check": "margin_cushion",
        "status": "pass" if passed_pct else "fail",
        "reason_code": None if passed_pct else "ERR_MARGIN_CUSHION",
        "message": (None if passed_pct else
                    f"Projected margin cushion {cushion_pct:.2f}% of NLV below required {cushion_pct_limit:.2f}%"),
        "limit_pct": cushion_pct_limit,
        "actual_pct": round(cushion_pct, 4),
    })

    return results


def _check_asset_class_concentration(portfolio: list, order: dict, ref_price: float, nlv: float, lim: dict) -> dict:
    """Check projected asset-class concentration (STK, OPT/FOP, FUT) against limit."""
    if nlv <= 0:
        return {"check": "asset_class_concentration", "status": "pass", "reason_code": None, "message": None}

    sec_type = order.get("sec_type", "STK").upper()
    action = order["action"].upper()
    qty = float(order["quantity"])
    signed_qty = qty if action == "BUY" else -qty
    mult = 100.0 if sec_type in ("OPT", "FOP") else 1.0
    order_notional = abs(signed_qty) * ref_price * mult

    # Bucket current portfolio by asset class
    buckets: dict[str, float] = {}
    for item in portfolio:
        st = item.get("sec_type", "STK").upper()
        bucket = "options" if st in ("OPT", "FOP") else ("futures" if st == "FUT" else "equities")
        buckets[bucket] = buckets.get(bucket, 0.0) + abs(item.get("market_value", 0.0))

    order_bucket = "options" if sec_type in ("OPT", "FOP") else ("futures" if sec_type == "FUT" else "equities")
    buckets[order_bucket] = buckets.get(order_bucket, 0.0) + order_notional

    # Check options-specific limit
    limit_pct = lim.get("max_asset_class_pct_nlv", float("inf"))
    opt_limit_pct = lim.get("max_options_pct_nlv", float("inf"))

    worst_breach = None
    worst_pct = 0.0
    for bucket, notional in buckets.items():
        pct = notional / nlv * 100.0
        bucket_limit = opt_limit_pct if bucket == "options" else limit_pct
        if pct > bucket_limit and pct > worst_pct:
            worst_pct = pct
            worst_breach = (bucket, pct, bucket_limit)

    if worst_breach:
        bucket, pct, blimit = worst_breach
        return {
            "check": "asset_class_concentration",
            "status": "fail",
            "reason_code": "ERR_ASSET_CLASS_CONCENTRATION",
            "message": f"Projected {bucket} exposure {pct:.2f}% of NLV exceeds limit {blimit:.2f}%",
            "bucket": bucket,
            "actual_pct": round(worst_pct, 4),
            "limit_pct": blimit,
            "buckets": {b: round(v / nlv * 100, 4) for b, v in buckets.items()},
        }

    return {
        "check": "asset_class_concentration",
        "status": "pass",
        "reason_code": None,
        "message": None,
        "buckets": {b: round(v / nlv * 100, 4) for b, v in buckets.items()},
    }


def _check_daily_loss(account: dict, lim: dict) -> list:
    max_loss_usd = lim.get("max_daily_loss_usd", float("inf"))
    max_loss_pct = lim.get("max_daily_loss_pct_nlv", float("inf"))
    nlv = account.get("nlv", 0.0)
    daily_pnl = account.get("unrealized_pnl", 0.0) + account.get("realized_pnl", 0.0)

    results = []

    passed_usd = daily_pnl >= -abs(max_loss_usd)
    results.append({
        "check": "daily_loss_lockout_usd",
        "status": "pass" if passed_usd else "fail",
        "reason_code": None if passed_usd else "ERR_DAILY_LOSS_LOCKOUT",
        "message": (None if passed_usd else
                    f"Daily P&L {daily_pnl:,.2f} USD breaches max daily loss -{abs(max_loss_usd):,.2f} USD"),
        "limit": -abs(max_loss_usd),
        "actual": round(daily_pnl, 2),
    })

    daily_pnl_pct = (daily_pnl / nlv * 100.0) if nlv > 0 else 0.0
    passed_pct = daily_pnl_pct >= -abs(max_loss_pct)
    results.append({
        "check": "daily_loss_lockout_pct",
        "status": "pass" if passed_pct else "fail",
        "reason_code": None if passed_pct else "ERR_DAILY_LOSS_LOCKOUT",
        "message": (None if passed_pct else
                    f"Daily P&L {daily_pnl_pct:.2f}% of NLV breaches max -{abs(max_loss_pct):.2f}%"),
        "limit_pct": -abs(max_loss_pct),
        "actual_pct": round(daily_pnl_pct, 4),
    })

    return results


def _check_open_order_stacking(open_orders: list, order_notional: float, nlv: float, lim: dict) -> dict:
    limit_pct = lim.get("max_open_order_exposure_pct_nlv", float("inf"))
    existing = _compute_open_order_notional(open_orders)
    total = existing + order_notional
    actual_pct = (total / nlv * 100.0) if nlv > 0 else float("inf")
    passed = actual_pct <= limit_pct
    return {
        "check": "open_order_stacking",
        "status": "pass" if passed else "fail",
        "reason_code": None if passed else "ERR_OPEN_ORDER_STACKING",
        "message": (None if passed else
                    f"Combined open order exposure {actual_pct:.2f}% of NLV exceeds limit {limit_pct:.2f}%"),
        "limit_pct": limit_pct,
        "actual_pct": round(actual_pct, 4),
        "existing_open_notional": round(existing, 2),
        "new_order_notional": round(order_notional, 2),
    }


def _check_options_greeks(portfolio: list, order: dict, quote: dict, lim: dict) -> list:
    sec_type = order.get("sec_type", "STK").upper()
    if sec_type not in ("OPT", "FOP"):
        return []

    port_greeks = _compute_portfolio_greeks(portfolio)
    action = order["action"].upper()
    qty = float(order["quantity"])
    sign = 1 if action == "BUY" else -1
    og = quote.get("greeks", {})

    proj_delta = port_greeks["delta"]
    proj_vega = port_greeks["vega"]
    if og.get("delta") is not None:
        proj_delta += sign * og["delta"] * qty * 100
    if og.get("vega") is not None:
        proj_vega += sign * og["vega"] * qty * 100

    results = []
    max_delta = lim.get("max_portfolio_delta", float("inf"))
    passed_delta = abs(proj_delta) <= max_delta
    results.append({
        "check": "options_delta",
        "status": "pass" if passed_delta else "fail",
        "reason_code": None if passed_delta else "ERR_OPTIONS_DELTA",
        "message": (None if passed_delta else
                    f"Projected portfolio delta {proj_delta:.2f} exceeds limit ±{max_delta:.2f}"),
        "limit": max_delta,
        "actual": round(proj_delta, 4),
    })

    max_vega = lim.get("max_portfolio_vega", float("inf"))
    passed_vega = abs(proj_vega) <= max_vega
    results.append({
        "check": "options_vega",
        "status": "pass" if passed_vega else "fail",
        "reason_code": None if passed_vega else "ERR_OPTIONS_CONVEXITY",
        "message": (None if passed_vega else
                    f"Projected portfolio vega {proj_vega:.2f} exceeds limit ±{max_vega:.2f}"),
        "limit": max_vega,
        "actual": round(proj_vega, 4),
    })

    return results


def _check_quote_quality(quote: dict, lim: dict, order: dict) -> list:
    warnings = []
    stale_sec = lim.get("stale_quote_seconds", 300)
    max_spread = lim.get("max_bid_ask_spread_pct", 5.0)
    symbol = order.get("symbol", "?")

    qt = quote.get("quote_time")
    if qt is not None:
        try:
            now = datetime.now(timezone.utc)
            if isinstance(qt, datetime):
                ts = qt if qt.tzinfo else qt.replace(tzinfo=timezone.utc)
                age = (now - ts).total_seconds()
            else:
                age = (now.timestamp() - float(qt))
            if age > stale_sec:
                warnings.append({
                    "code": "WARN_STALE_QUOTE",
                    "message": f"Quote for {symbol} is {age:.0f}s old (limit: {stale_sec}s)",
                    "age_seconds": round(age, 1),
                    "limit_seconds": stale_sec,
                })
        except Exception:
            pass
    elif quote.get("bid") is None and quote.get("last") is None and quote.get("close") is None:
        warnings.append({
            "code": "WARN_STALE_QUOTE",
            "message": f"No market data available for {symbol}",
            "age_seconds": None,
            "limit_seconds": stale_sec,
        })

    spread_pct = quote.get("spread_pct")
    if spread_pct is not None and spread_pct > max_spread:
        warnings.append({
            "code": "WARN_WIDE_SPREAD",
            "message": f"Bid-ask spread for {symbol} is {spread_pct:.2f}% (limit: {max_spread:.2f}%)",
            "spread_pct": round(spread_pct, 4),
            "limit_pct": max_spread,
        })

    return warnings


def _run_stress(order_notional: float, sec_type: str, port_greeks: dict, lim: dict, nlv: float, order: dict | None = None) -> dict:
    scenarios = lim.get("stress_scenarios", {})
    stress_limit_pct = lim.get("stress_loss_limit_pct_nlv", 15.0)
    results = {}

    for shock_pct in scenarios.get("equity_shock_pct", []):
        key = f"equity_shock_{shock_pct}pct"
        if sec_type in ("STK", "ETF"):
            loss = order_notional * abs(shock_pct) / 100.0
        elif sec_type in ("OPT", "FOP"):
            delta = port_greeks.get("delta", 0.0)
            loss = abs(delta) * order_notional * abs(shock_pct) / 100.0
        else:
            loss = 0.0
        loss_pct = (loss / nlv * 100.0) if nlv > 0 else 0.0
        results[key] = {
            "scenario": key,
            "shock_pct": shock_pct,
            "estimated_loss": round(loss, 2),
            "pct_nlv": round(loss_pct, 4),
            "status": "fail" if abs(loss_pct) > stress_limit_pct else "pass",
        }

    for shock_pct in scenarios.get("vol_shock_pct", []):
        key = f"vol_shock_{shock_pct}pct"
        vega = port_greeks.get("vega", 0.0)
        # Short options lose when vol rises; long options lose when vol falls
        loss = abs(vega) * abs(shock_pct) if sec_type in ("OPT", "FOP") else 0.0
        loss_pct = (loss / nlv * 100.0) if nlv > 0 else 0.0
        results[key] = {
            "scenario": key,
            "shock_pct": shock_pct,
            "estimated_loss": round(loss, 2),
            "pct_nlv": round(loss_pct, 4),
            "status": "fail" if abs(loss_pct) > stress_limit_pct else "pass",
        }

    # Rate shock: mainly impacts futures and bonds; approximate equity impact as negligible
    for shock_bps in scenarios.get("rate_shock_bps", []):
        key = f"rate_shock_{shock_bps}bps"
        if sec_type == "FUT":
            # Rough DV01 estimate: 1bp move on a bond future ~ 0.08% of notional
            loss = order_notional * shock_bps * 0.0008
        elif sec_type in ("OPT", "FOP"):
            # Rate sensitivity via rho (not tracked, use conservative 0)
            loss = 0.0
        else:
            loss = 0.0
        loss_pct = (loss / nlv * 100.0) if nlv > 0 else 0.0
        results[key] = {
            "scenario": key,
            "shock_bps": shock_bps,
            "estimated_loss": round(loss, 2),
            "pct_nlv": round(loss_pct, 4),
            "status": "fail" if abs(loss_pct) > stress_limit_pct else "pass",
        }

    # FX shock: applies when order currency differs from account currency (USD assumed)
    order_currency = (order or {}).get("currency", "USD")
    for shock_pct in scenarios.get("fx_shock_pct", []):
        key = f"fx_shock_{shock_pct}pct"
        if order_currency != "USD":
            loss = order_notional * abs(shock_pct) / 100.0
        else:
            loss = 0.0
        loss_pct = (loss / nlv * 100.0) if nlv > 0 else 0.0
        results[key] = {
            "scenario": key,
            "shock_pct": shock_pct,
            "currency": order_currency,
            "estimated_loss": round(loss, 2),
            "pct_nlv": round(loss_pct, 4),
            "status": "fail" if abs(loss_pct) > stress_limit_pct else "pass",
        }

    return results


def _estimate_margin_impact(order_notional: float, account: dict, order: dict) -> dict:
    sec_type = order.get("sec_type", "STK").upper()
    action = order.get("action", "BUY").upper()
    nlv = account.get("nlv", 0.0)
    excess = account.get("excess_liquidity", 0.0)

    if sec_type == "STK":
        margin_rate = 0.25 if action == "BUY" else 1.5
    elif sec_type in ("OPT", "FOP"):
        margin_rate = 0.0
    elif sec_type == "FUT":
        margin_rate = 0.10
    else:
        margin_rate = 0.25

    additional = order_notional * margin_rate
    excess_after = excess - additional
    return {
        "estimated_additional_margin": round(additional, 2),
        "excess_liquidity_before": excess,
        "excess_liquidity_after": round(excess_after, 2),
        "margin_cushion_pct_before": round((excess / nlv * 100.0) if nlv > 0 else 0.0, 4),
        "margin_cushion_pct_after": round((excess_after / nlv * 100.0) if nlv > 0 else 0.0, 4),
        "note": "Simplified estimate; actual margin depends on IB house requirements",
    }


def _compute_stop_risk(order: dict, ref_price: float, nlv: float) -> dict | None:
    """Compute estimated max loss based on stop price when supplied."""
    stop_price = order.get("stop_price")
    if stop_price is None:
        return None
    action = order.get("action", "BUY").upper()
    qty = float(order.get("quantity", 0))
    sec_type = order.get("sec_type", "STK").upper()
    mult = 100.0 if sec_type in ("OPT", "FOP") else 1.0
    if action == "BUY":
        risk_per_unit = max(ref_price - float(stop_price), 0.0)
    else:
        risk_per_unit = max(float(stop_price) - ref_price, 0.0)
    max_loss = risk_per_unit * qty * mult
    pct_nlv = (max_loss / nlv * 100.0) if nlv > 0 else 0.0
    return {
        "stop_price": float(stop_price),
        "ref_price": round(ref_price, 4),
        "risk_per_unit": round(risk_per_unit, 4),
        "estimated_max_loss": round(max_loss, 2),
        "pct_nlv": round(pct_nlv, 4),
    }


def _check_options_detail(order: dict, quote: dict, portfolio: list, lim: dict) -> list:
    """Extra option-specific checks: assignment risk, short exposure, IV rank."""
    sec_type = order.get("sec_type", "STK").upper()
    if sec_type not in ("OPT", "FOP"):
        return []

    warnings = []
    action = order["action"].upper()
    right = (order.get("right") or "").upper()

    # Short option exposure check
    if action == "SELL":
        short_contracts = sum(
            abs(p.get("position", 0)) for p in portfolio
            if p.get("sec_type") in ("OPT", "FOP") and p.get("position", 0) < 0
        )
        short_contracts += float(order.get("quantity", 0))
        max_short = lim.get("max_short_option_contracts", float("inf"))
        if short_contracts > max_short:
            warnings.append({
                "check": "short_option_exposure",
                "status": "fail",
                "reason_code": "ERR_SHORT_OPTION_EXPOSURE",
                "message": f"Projected short option contracts {short_contracts:.0f} exceeds limit {max_short:.0f}",
                "limit": max_short,
                "actual": short_contracts,
            })

    # Assignment risk: short options near/at expiry
    if action == "SELL":
        expiry_str = order.get("expiry", "")
        if expiry_str:
            try:
                from datetime import date
                exp_date = datetime.strptime(expiry_str, "%Y%m%d").date()
                days_to_expiry = (exp_date - date.today()).days
                if days_to_expiry <= 7:
                    warnings.append({
                        "code": "WARN_ASSIGNMENT_RISK",
                        "message": f"Short {right} option expires in {days_to_expiry} day(s) — elevated assignment risk",
                        "days_to_expiry": days_to_expiry,
                    })
            except Exception:
                pass

    # IV rank / percentile from quote greeks
    iv = quote.get("greeks", {}).get("iv")
    if iv is not None:
        warnings.append({
            "code": "INFO_IV",
            "message": f"Implied volatility: {iv * 100:.1f}%",
            "implied_vol": round(iv, 6),
        })

    return warnings


def _compute_limit_utilization(
    projected: dict, account: dict, open_orders: list, order_notional: float, lim: dict, nlv: float
) -> dict:
    notional_limit = lim.get("max_order_notional_usd", None)
    nlv_pct_limit = lim.get("max_order_pct_nlv", 100.0)
    nlv_pct_actual = (order_notional / nlv * 100.0) if nlv > 0 else 0.0
    excess = account.get("excess_liquidity", 0.0)
    cushion_pct_limit = lim.get("margin_cushion_pct", 15.0)
    cushion_pct = (excess / nlv * 100.0) if nlv > 0 else 0.0
    open_notional = _compute_open_order_notional(open_orders)
    open_limit_pct = lim.get("max_open_order_exposure_pct_nlv", 25.0)
    open_pct = ((open_notional + order_notional) / nlv * 100.0) if nlv > 0 else 0.0
    single_name_limit = lim.get("max_single_name_pct_nlv", 15.0)

    return {
        "notional": {
            "used": round(order_notional, 2),
            "limit": notional_limit,
            "utilization_pct": round(order_notional / notional_limit * 100.0, 2) if notional_limit else None,
        },
        "order_pct_nlv": {"used_pct": round(nlv_pct_actual, 4), "limit_pct": nlv_pct_limit},
        "single_name_pct_nlv": {
            "used_pct": round(projected.get("pct_nlv", 0.0), 4),
            "limit_pct": single_name_limit,
        },
        "margin_cushion": {"current_pct": round(cushion_pct, 4), "required_pct": cushion_pct_limit},
        "open_order_exposure": {"used_pct": round(open_pct, 4), "limit_pct": open_limit_pct},
    }


# ── Core gate function ────────────────────────────────────────────────────────


async def run_check(ib, order: dict, limits: dict) -> dict:
    """
    Run the full pre-trade risk check against a live IB connection.

    Args:
        ib: Already-connected ib_async IB instance.
        order: Order spec dict — keys: symbol, action, quantity, sec_type,
               order_type, limit_price (opt), tif, currency, exchange,
               expiry (opt), strike (opt), right (opt), stop_price (opt).
        limits: Full limits dict as returned by load_limits().

    Returns:
        Structured risk response dict with verdict, checks, warnings, stress.
    """
    audit_id = _audit_id()
    ts = utc_timestamp()

    # Support both wrapped {"limits": {...}} and flat dicts
    lim = limits.get("limits", limits)

    # 1. Fetch portfolio, account, and open orders concurrently
    portfolio_items = ib.portfolio()

    account, open_trades = await asyncio.gather(
        _fetch_account_data(ib),
        ib.reqAllOpenOrdersAsync(),
    )
    open_orders = _open_orders_to_list(open_trades)

    # Fetch option greeks for existing option positions so portfolio delta/vega are accurate
    greeks_map = await _fetch_portfolio_greeks(ib, portfolio_items)
    portfolio = _portfolio_to_list(portfolio_items, greeks_map)

    # 2. Qualify contract and fetch quote
    contract = _build_contract(order)
    with contextlib.redirect_stdout(sys.stderr):
        try:
            await ib.qualifyContractsAsync(contract)
        except Exception:
            pass
    quote = await _fetch_quote(ib, contract)

    # 3. Reference price and order notional
    ref_price = _get_ref_price(order, quote)
    mult = 100.0 if order.get("sec_type", "STK").upper() in ("OPT", "FOP") else 1.0
    order_notional = abs(float(order["quantity"])) * ref_price * mult
    nlv = account["nlv"]

    # 4. Projected position and portfolio Greeks
    projected = _compute_projected_position(portfolio, order, ref_price, nlv)
    port_greeks = _compute_portfolio_greeks(portfolio)

    # 5. Compute margin impact first — used by the cushion check
    margin_impact = _estimate_margin_impact(order_notional, account, order)

    # 6. Run hard checks
    checks: list = []
    checks.append(_check_notional(order_notional, lim))
    checks.append(_check_nlv_pct(order_notional, nlv, lim))
    checks.append(_check_concentration(projected, lim))
    checks.append(_check_asset_class_concentration(portfolio, order, ref_price, nlv, lim))
    # Margin cushion uses projected post-order excess liquidity
    checks.extend(_check_margin_cushion(account, lim, margin_impact))
    checks.extend(_check_daily_loss(account, lim))
    checks.append(_check_open_order_stacking(open_orders, order_notional, nlv, lim))
    checks.extend(_check_options_greeks(portfolio, order, quote, lim))

    # Short-option exposure and related hard checks from options detail
    opt_detail = _check_options_detail(order, quote, portfolio, lim)
    hard_opt = [c for c in opt_detail if isinstance(c, dict) and c.get("status") == "fail"]
    soft_opt = [c for c in opt_detail if isinstance(c, dict) and c.get("status") != "fail"]
    checks.extend(hard_opt)

    # 7. Stress scenarios — failures are hard gate failures, not warnings
    sec_type = order.get("sec_type", "STK").upper()
    stress = _run_stress(projected["order_notional"], sec_type, port_greeks, lim, nlv, order)
    for v in stress.values():
        if v["status"] == "fail":
            checks.append({
                "check": f"stress_{v['scenario']}",
                "status": "fail",
                "reason_code": "ERR_STRESS_LOSS",
                "message": (f"Stress {v['scenario']}: est. loss {v['estimated_loss']:,.2f} USD "
                            f"({v['pct_nlv']:.2f}% NLV) exceeds {lim.get('stress_loss_limit_pct_nlv', 15)}% limit"),
                "scenario": v["scenario"],
                "estimated_loss": v["estimated_loss"],
                "pct_nlv": v["pct_nlv"],
            })

    # 8. Warnings (non-blocking)
    warnings: list = []
    warnings.extend(_check_quote_quality(quote, lim, order))
    warnings.extend(soft_opt)

    # 9. Verdict
    failures = [c for c in checks if c["status"] == "fail"]
    verdict = "pass" if not failures else "fail"

    # 10. Stop-risk estimation when stop_price is provided
    stop_risk = _compute_stop_risk(order, ref_price, nlv)

    return {
        "audit_id": audit_id,
        "timestamp": ts,
        "verdict": verdict,
        "order": {
            "symbol": order["symbol"].upper(),
            "sec_type": order.get("sec_type", "STK").upper(),
            "action": order["action"].upper(),
            "quantity": float(order["quantity"]),
            "order_type": order.get("order_type", "MKT").upper(),
            "limit_price": order.get("limit_price"),
            "tif": order.get("tif", "DAY"),
            "currency": order.get("currency", "USD"),
            "exchange": order.get("exchange", "SMART"),
            "expiry": order.get("expiry"),
            "strike": order.get("strike"),
            "right": order.get("right"),
            "stop_price": order.get("stop_price"),
            "estimated_notional": round(order_notional, 2),
            "ref_price": round(ref_price, 4),
        },
        "account": account,
        "projected_position": projected,
        "margin_impact": margin_impact,
        "stop_risk": stop_risk,
        "risk_checks": checks,
        "limit_utilization": _compute_limit_utilization(projected, account, open_orders, order_notional, lim, nlv),
        "warnings": warnings,
        "stress_results": stress,
        "failures": [{"reason_code": c["reason_code"], "message": c["message"], "check": c["check"]} for c in failures],
    }


# ── CLI ───────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run structured pre-trade risk check against configured limits.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pretrade_risk_check.py --symbol AAPL --action BUY --quantity 10 --order-type MKT
  python pretrade_risk_check.py --symbol SPY --action BUY --quantity 50 --order-type LMT --limit-price 450
  python pretrade_risk_check.py --symbol AAPL --action BUY --quantity 10 --order-type MKT --live
        """,
    )
    parser.add_argument("--host", default=DEFAULT_HOST)

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--paper", dest="port", action="store_const", const=PORT_PAPER)
    mode.add_argument("--live", dest="port", action="store_const", const=PORT_LIVE)
    mode.add_argument("--port", dest="port", type=int)
    parser.set_defaults(port=PORT_PAPER)

    parser.add_argument("--symbol", required=True)
    parser.add_argument("--sec-type", default="STK", dest="sec_type")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--exchange", default="SMART")
    parser.add_argument("--expiry")
    parser.add_argument("--strike", type=float)
    parser.add_argument("--right", choices=["C", "P"])
    parser.add_argument("--action", required=True, choices=["BUY", "SELL"])
    parser.add_argument("--quantity", required=True, type=float)
    parser.add_argument("--order-type", required=True, choices=["MKT", "LMT"], dest="order_type")
    parser.add_argument("--limit-price", type=float, dest="limit_price")
    parser.add_argument("--tif", default="DAY", choices=["DAY", "GTC", "IOC", "FOK"])

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    order = {
        "symbol": args.symbol,
        "sec_type": args.sec_type,
        "action": args.action,
        "quantity": args.quantity,
        "order_type": args.order_type,
        "limit_price": args.limit_price,
        "tif": args.tif,
        "currency": args.currency,
        "exchange": args.exchange,
        "expiry": args.expiry,
        "strike": args.strike,
        "right": args.right,
    }

    try:
        limits = load_limits()
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc), "status": "config_error", "timestamp": utc_timestamp()}),
              file=sys.stderr)
        sys.exit(1)

    ib = IB()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            asyncio.run(
                asyncio.wait_for(
                    ib.connectAsync(args.host, args.port, clientId=CLIENT_ID, readonly=True),
                    timeout=CONNECT_TIMEOUT,
                )
            )
    except asyncio.TimeoutError:
        print(json.dumps({"error": f"Timed out connecting to TWS at {args.host}:{args.port}",
                          "status": "tws_unavailable", "timestamp": utc_timestamp()}), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(json.dumps({"error": f"Cannot reach TWS at {args.host}:{args.port} — {exc}",
                          "status": "tws_unavailable", "timestamp": utc_timestamp()}), file=sys.stderr)
        sys.exit(1)

    try:
        result = asyncio.run(run_check(ib, order, limits))
    except Exception as exc:
        print(json.dumps({"error": f"Unexpected error: {exc}", "status": "error", "timestamp": utc_timestamp()}),
              file=sys.stderr)
        sys.exit(1)
    finally:
        ib.disconnect()

    exit_code = 0 if result["verdict"] == "pass" else 2
    print(json.dumps(result, indent=2))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
