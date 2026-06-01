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

Connection ID: 1006 (see tools/connection_ids.json)
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
CLIENT_ID = 1006
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


def _portfolio_to_list(portfolio_items) -> list:
    result = []
    for item in portfolio_items:
        c = item.contract
        result.append({
            "symbol": c.symbol,
            "sec_type": c.secType,
            "currency": getattr(c, "currency", "USD"),
            "position": item.position,
            "market_price": item.marketPrice,
            "market_value": item.marketValue,
            "unrealized_pnl": item.unrealizedPNL,
            "realized_pnl": item.realizedPNL,
            "greeks": {},
        })
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


def _check_margin_cushion(account: dict, lim: dict) -> list:
    nlv = account.get("nlv", 0.0)
    excess = account.get("excess_liquidity", 0.0)
    min_excess = lim.get("min_excess_liquidity_usd", 0.0)
    cushion_pct_limit = lim.get("margin_cushion_pct", 0.0)

    results = []

    passed_abs = excess > min_excess
    results.append({
        "check": "excess_liquidity",
        "status": "pass" if passed_abs else "fail",
        "reason_code": None if passed_abs else "ERR_EXCESS_LIQUIDITY",
        "message": (None if passed_abs else
                    f"Excess liquidity {excess:,.2f} USD at or below floor {min_excess:,.2f} USD"),
        "limit": min_excess,
        "actual": excess,
    })

    cushion_pct = (excess / nlv * 100.0) if nlv > 0 else 0.0
    passed_pct = cushion_pct >= cushion_pct_limit
    results.append({
        "check": "margin_cushion",
        "status": "pass" if passed_pct else "fail",
        "reason_code": None if passed_pct else "ERR_MARGIN_CUSHION",
        "message": (None if passed_pct else
                    f"Margin cushion {cushion_pct:.2f}% of NLV below required {cushion_pct_limit:.2f}%"),
        "limit_pct": cushion_pct_limit,
        "actual_pct": round(cushion_pct, 4),
    })

    return results


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


def _run_stress(order_notional: float, sec_type: str, port_greeks: dict, lim: dict, nlv: float) -> dict:
    scenarios = lim.get("stress_scenarios", {})
    stress_limit_pct = lim.get("stress_loss_limit_pct_nlv", 15.0)
    results = {}

    for shock_pct in scenarios.get("equity_shock_pct", []):
        key = f"equity_shock_{shock_pct}pct"
        if sec_type in ("STK", "ETF"):
            loss = order_notional * shock_pct / 100.0
        elif sec_type in ("OPT", "FOP"):
            delta = port_greeks.get("delta", 0.0)
            loss = delta * order_notional * shock_pct / 100.0
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
        loss = vega * shock_pct if sec_type in ("OPT", "FOP") else 0.0
        loss_pct = (loss / nlv * 100.0) if nlv > 0 else 0.0
        results[key] = {
            "scenario": key,
            "shock_pct": shock_pct,
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
               expiry (opt), strike (opt), right (opt).
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
    portfolio = _portfolio_to_list(portfolio_items)

    account, open_trades = await asyncio.gather(
        _fetch_account_data(ib),
        ib.reqAllOpenOrdersAsync(),
    )
    open_orders = _open_orders_to_list(open_trades)

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

    # 5. Run checks
    checks: list = []
    checks.append(_check_notional(order_notional, lim))
    checks.append(_check_nlv_pct(order_notional, nlv, lim))
    checks.append(_check_concentration(projected, lim))
    checks.extend(_check_margin_cushion(account, lim))
    checks.extend(_check_daily_loss(account, lim))
    checks.append(_check_open_order_stacking(open_orders, order_notional, nlv, lim))
    checks.extend(_check_options_greeks(portfolio, order, quote, lim))

    # 6. Warnings (non-blocking)
    warnings = _check_quote_quality(quote, lim, order)

    # 7. Stress scenarios
    stress = _run_stress(projected["order_notional"], order.get("sec_type", "STK").upper(), port_greeks, lim, nlv)
    for v in stress.values():
        if v["status"] == "fail":
            warnings.append({
                "code": "WARN_STRESS_LOSS",
                "message": (f"Stress {v['scenario']}: est. loss {v['estimated_loss']:,.2f} USD "
                            f"({v['pct_nlv']:.2f}% NLV) exceeds {lim.get('stress_loss_limit_pct_nlv', 15)}% limit"),
                "scenario": v["scenario"],
            })

    # 8. Verdict
    failures = [c for c in checks if c["status"] == "fail"]
    verdict = "pass" if not failures else "fail"

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
            "estimated_notional": round(order_notional, 2),
            "ref_price": round(ref_price, 4),
        },
        "account": account,
        "projected_position": projected,
        "margin_impact": _estimate_margin_impact(order_notional, account, order),
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
