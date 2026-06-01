"""Unit tests for pretrade_risk_check.py.

Tests cover every required acceptance criterion:
  - approved order (pass)
  - concentration breach
  - margin cushion breach
  - daily loss lockout
  - stale/wide quote warning
  - options convexity (vega) breach
  - open-order exposure stacking
"""

import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

IB_UNSET = 1.7976931348623157e308

# ── helpers ───────────────────────────────────────────────────────────────────


def _inject_pretrade_module():
    """Inject a minimal ib_async stub and (re-)import pretrade_risk_check."""
    fake = types.ModuleType("ib_async")

    class _Base:
        def __init__(self, *a, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    class Stock(_Base):
        def __init__(self, symbol, exchange="SMART", currency="USD"):
            self.symbol = symbol
            self.secType = "STK"
            self.exchange = exchange
            self.currency = currency

    class Option(_Base):
        secType = "OPT"

    class Future(_Base):
        secType = "FUT"

    class Contract(_Base):
        pass

    class IB:
        pass

    fake.IB = IB
    fake.Contract = Contract
    fake.Stock = Stock
    fake.Option = Option
    fake.Future = Future

    sys.modules["ib_async"] = fake
    for key in list(sys.modules):
        if key.startswith("tools.pretrade_risk_check") or key == "tools.pretrade_risk_check":
            del sys.modules[key]

    import pathlib
    repo = pathlib.Path(__file__).parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    import tools.pretrade_risk_check as mod
    return mod


MOD = _inject_pretrade_module()

BASE_LIMITS = {
    "max_order_notional_usd": 100_000,
    "max_order_pct_nlv": 10.0,
    "max_single_name_pct_nlv": 15.0,
    "max_sector_pct_nlv": 35.0,
    "max_asset_class_pct_nlv": 70.0,
    "min_excess_liquidity_usd": 10_000,
    "margin_cushion_pct": 15.0,
    "max_daily_loss_usd": 5_000,
    "max_daily_loss_pct_nlv": 2.0,
    "max_portfolio_delta": 1_000.0,
    "max_portfolio_vega": 5_000.0,
    "max_open_order_exposure_pct_nlv": 25.0,
    "stale_quote_seconds": 300,
    "max_bid_ask_spread_pct": 5.0,
    "stress_loss_limit_pct_nlv": 15.0,
    "stress_scenarios": {"equity_shock_pct": [-10], "vol_shock_pct": [20]},
}


def _lim(**overrides):
    d = dict(BASE_LIMITS)
    d.update(overrides)
    return d


def _portfolio_item(symbol="AAPL", sec_type="STK", position=100.0, market_value=15_000.0):
    return {
        "symbol": symbol,
        "sec_type": sec_type,
        "position": position,
        "market_value": market_value,
        "market_price": market_value / position if position else 0,
        "unrealized_pnl": 0.0,
        "realized_pnl": 0.0,
        "greeks": {},
    }


def _account(nlv=200_000.0, excess=50_000.0, unrealized=0.0, realized=0.0):
    return {
        "nlv": nlv,
        "excess_liquidity": excess,
        "buying_power": excess * 4,
        "init_margin_req": nlv * 0.1,
        "maint_margin_req": nlv * 0.08,
        "available_funds": excess,
        "gross_position_value": nlv * 0.8,
        "unrealized_pnl": unrealized,
        "realized_pnl": realized,
    }


def _open_order(symbol="XYZ", quantity=10.0, lmt_price=100.0, sec_type="STK"):
    return {
        "symbol": symbol,
        "sec_type": sec_type,
        "action": "BUY",
        "quantity": quantity,
        "lmt_price": lmt_price,
        "status": "Submitted",
        "remaining": quantity,
    }


# ── check_notional ─────────────────────────────────────────────────────────────


class TestCheckNotional:
    def test_passes_within_limit(self):
        r = MOD._check_notional(50_000, _lim())
        assert r["status"] == "pass"
        assert r["reason_code"] is None

    def test_fails_when_above_limit(self):
        r = MOD._check_notional(150_000, _lim())
        assert r["status"] == "fail"
        assert r["reason_code"] == "ERR_NOTIONAL_LIMIT"

    def test_exact_limit_passes(self):
        r = MOD._check_notional(100_000, _lim())
        assert r["status"] == "pass"


# ── check_nlv_pct ──────────────────────────────────────────────────────────────


class TestCheckNlvPct:
    def test_passes_within_limit(self):
        r = MOD._check_nlv_pct(10_000, 200_000, _lim())
        assert r["status"] == "pass"

    def test_fails_above_limit(self):
        # 10% of 200k = 20k; order notional 25k
        r = MOD._check_nlv_pct(25_000, 200_000, _lim())
        assert r["status"] == "fail"
        assert r["reason_code"] == "ERR_NLV_PCT_LIMIT"


# ── concentration ──────────────────────────────────────────────────────────────


class TestConcentration:
    def test_passes_under_limit(self):
        projected = {"symbol": "AAPL", "sec_type": "STK", "pct_nlv": 10.0}
        r = MOD._check_concentration(projected, _lim())
        assert r["status"] == "pass"

    def test_fails_concentration_breach(self):
        # Single-name limit is 15%; projected is 20%
        projected = {"symbol": "AAPL", "sec_type": "STK", "pct_nlv": 20.0}
        r = MOD._check_concentration(projected, _lim())
        assert r["status"] == "fail"
        assert r["reason_code"] == "ERR_CONCENTRATION_SINGLE_NAME"
        assert "AAPL" in r["message"]


# ── margin cushion ─────────────────────────────────────────────────────────────


class TestMarginCushion:
    def test_passes_with_healthy_cushion(self):
        # excess = 50k, nlv = 200k → 25% cushion; limit is 15%
        account = _account(nlv=200_000, excess=50_000)
        results = MOD._check_margin_cushion(account, _lim())
        statuses = [r["status"] for r in results]
        assert all(s == "pass" for s in statuses)

    def test_fails_excess_liquidity_below_floor(self):
        # min_excess = 10k, excess = 5k
        account = _account(nlv=200_000, excess=5_000)
        results = MOD._check_margin_cushion(account, _lim())
        codes = [r.get("reason_code") for r in results]
        assert "ERR_EXCESS_LIQUIDITY" in codes

    def test_fails_margin_cushion_pct_breach(self):
        # excess = 20k, nlv = 200k → 10% cushion; limit is 15%
        account = _account(nlv=200_000, excess=20_000)
        results = MOD._check_margin_cushion(account, _lim())
        codes = [r.get("reason_code") for r in results]
        assert "ERR_MARGIN_CUSHION" in codes


# ── daily loss lockout ─────────────────────────────────────────────────────────


class TestDailyLossLockout:
    def test_passes_when_profitable(self):
        account = _account(unrealized=1_000, realized=500)
        results = MOD._check_daily_loss(account, _lim())
        assert all(r["status"] == "pass" for r in results)

    def test_passes_within_loss_limit(self):
        account = _account(unrealized=-2_000, realized=-1_000)  # -3k < -5k limit
        results = MOD._check_daily_loss(account, _lim())
        assert all(r["status"] == "pass" for r in results)

    def test_fails_daily_loss_usd_lockout(self):
        # -6k loss exceeds -5k limit
        account = _account(nlv=500_000, unrealized=-4_000, realized=-2_000)
        results = MOD._check_daily_loss(account, _lim())
        codes = [r.get("reason_code") for r in results if r["status"] == "fail"]
        assert "ERR_DAILY_LOSS_LOCKOUT" in codes

    def test_fails_daily_loss_pct_lockout(self):
        # nlv=50k, -2.5k loss = -5% > -2% limit
        account = _account(nlv=50_000, excess=20_000, unrealized=-2_500)
        results = MOD._check_daily_loss(account, _lim())
        codes = [r.get("reason_code") for r in results if r["status"] == "fail"]
        assert "ERR_DAILY_LOSS_LOCKOUT" in codes


# ── stale / wide quote warnings ────────────────────────────────────────────────


class TestQuoteQualityWarnings:
    def test_no_warning_on_fresh_tight_quote(self):
        now = datetime.now(timezone.utc)
        quote = {
            "bid": 99.5, "ask": 100.5, "last": 100.0, "close": 100.0,
            "mid": 100.0, "spread_pct": 1.0, "quote_time": now,
        }
        warnings = MOD._check_quote_quality(quote, _lim(), {"symbol": "AAPL"})
        assert warnings == []

    def test_warns_on_stale_quote(self):
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=400)
        quote = {
            "bid": 99.5, "ask": 100.5, "last": 100.0, "close": 100.0,
            "mid": 100.0, "spread_pct": 1.0, "quote_time": stale_time,
        }
        warnings = MOD._check_quote_quality(quote, _lim(), {"symbol": "AAPL"})
        codes = [w["code"] for w in warnings]
        assert "WARN_STALE_QUOTE" in codes

    def test_warns_on_wide_spread(self):
        now = datetime.now(timezone.utc)
        quote = {
            "bid": 95.0, "ask": 110.0, "last": 102.0, "close": 102.0,
            "mid": 102.5, "spread_pct": 14.6, "quote_time": now,
        }
        warnings = MOD._check_quote_quality(quote, _lim(), {"symbol": "AAPL"})
        codes = [w["code"] for w in warnings]
        assert "WARN_WIDE_SPREAD" in codes

    def test_warns_when_no_market_data(self):
        quote = {"bid": None, "ask": None, "last": None, "close": None,
                 "mid": None, "spread_pct": None, "quote_time": None}
        warnings = MOD._check_quote_quality(quote, _lim(), {"symbol": "XYZ"})
        codes = [w["code"] for w in warnings]
        assert "WARN_STALE_QUOTE" in codes


# ── options convexity (vega) breach ───────────────────────────────────────────


class TestOptionsConvexityBreach:
    def _opt_order(self, action="BUY", qty=10.0):
        return {"symbol": "SPX", "sec_type": "OPT", "action": action, "quantity": qty}

    def test_no_check_for_stock(self):
        order = {"symbol": "AAPL", "sec_type": "STK", "action": "BUY", "quantity": 10.0}
        quote = {"greeks": {"delta": 1.0, "vega": 0.0}}
        results = MOD._check_options_greeks([], order, quote, _lim())
        assert results == []

    def test_passes_within_vega_limit(self):
        # Portfolio has no options; new order adds vega = 10 * 5 * 100 = 5000 (exactly at limit)
        order = self._opt_order(action="BUY", qty=10.0)
        quote = {"greeks": {"delta": 0.3, "vega": 5.0, "gamma": None, "theta": None, "iv": None}}
        results = MOD._check_options_greeks([], order, quote, _lim(max_portfolio_vega=5_000))
        vega_check = next(r for r in results if r["check"] == "options_vega")
        assert vega_check["status"] == "pass"

    def test_fails_options_convexity_breach(self):
        # Portfolio already has vega, adding more exceeds 5000 limit
        portfolio = [
            {**_portfolio_item("SPX", "OPT", 50.0, 25_000.0),
             "greeks": {"delta": 0.4, "vega": 60.0, "gamma": 0.01, "theta": -5.0}},
        ]
        order = self._opt_order(action="BUY", qty=20.0)
        quote = {"greeks": {"delta": 0.3, "vega": 60.0, "gamma": None, "theta": None, "iv": None}}
        # Portfolio vega: 60 * 50 * 100 = 300000; far above 5000 limit
        results = MOD._check_options_greeks(portfolio, order, quote, _lim())
        vega_check = next(r for r in results if r["check"] == "options_vega")
        assert vega_check["status"] == "fail"
        assert vega_check["reason_code"] == "ERR_OPTIONS_CONVEXITY"


# ── open-order exposure stacking ──────────────────────────────────────────────


class TestOpenOrderStacking:
    def test_passes_when_combined_within_limit(self):
        # Limit 25% of 200k = 50k; existing 20k + new 10k = 30k = 15%
        open_orders = [_open_order(quantity=200, lmt_price=100.0)]  # 20k
        r = MOD._check_open_order_stacking(open_orders, 10_000, 200_000, _lim())
        assert r["status"] == "pass"

    def test_fails_open_order_stacking(self):
        # Limit 25% of 200k = 50k; existing 40k + new 20k = 60k = 30% → fail
        open_orders = [_open_order(quantity=400, lmt_price=100.0)]  # 40k
        r = MOD._check_open_order_stacking(open_orders, 20_000, 200_000, _lim())
        assert r["status"] == "fail"
        assert r["reason_code"] == "ERR_OPEN_ORDER_STACKING"
        assert r["existing_open_notional"] == pytest.approx(40_000)

    def test_no_open_orders_passes(self):
        r = MOD._check_open_order_stacking([], 5_000, 200_000, _lim())
        assert r["status"] == "pass"


# ── full run_check via mocked IB ───────────────────────────────────────────────


def _build_mock_ib(portfolio_items=None, nlv=200_000.0, excess=50_000.0,
                   unrealized=0.0, realized=0.0, open_trades=None,
                   bid=99.5, ask=100.5, last=100.0, close=100.0):
    mock_ib = MagicMock()
    mock_ib.portfolio.return_value = portfolio_items or []

    summary_tags = {
        "NetLiquidation": nlv,
        "ExcessLiquidity": excess,
        "BuyingPower": excess * 4,
        "InitMarginReq": nlv * 0.1,
        "MaintMarginReq": nlv * 0.08,
        "AvailableFunds": excess,
        "GrossPositionValue": nlv * 0.8,
        "UnrealizedPnL": unrealized,
        "RealizedPnL": realized,
    }
    summary = [SimpleNamespace(tag=k, value=str(v)) for k, v in summary_tags.items()]
    mock_ib.accountSummaryAsync = AsyncMock(return_value=summary)
    mock_ib.reqAllOpenOrdersAsync = AsyncMock(return_value=open_trades or [])
    mock_ib.qualifyContractsAsync = AsyncMock()

    ticker = MagicMock()
    ticker.bid = bid
    ticker.ask = ask
    ticker.last = last
    ticker.close = close
    ticker.time = datetime.now(timezone.utc)
    ticker.modelGreeks = None
    ticker.lastGreeks = None

    mock_ib.reqMktData.return_value = ticker
    mock_ib.cancelMktData = MagicMock()
    return mock_ib


def _order(symbol="AAPL", action="BUY", quantity=10.0, sec_type="STK",
           limit_price=100.0, order_type="LMT"):
    return {
        "symbol": symbol, "action": action, "quantity": quantity,
        "sec_type": sec_type, "order_type": order_type,
        "limit_price": limit_price, "tif": "DAY",
        "currency": "USD", "exchange": "SMART",
    }


class TestRunCheck:
    def test_approved_order_returns_pass(self):
        ib = _build_mock_ib(nlv=200_000, excess=50_000)
        limits = {"limits": BASE_LIMITS}
        result = asyncio.run(MOD.run_check(ib, _order(quantity=5.0, limit_price=100.0), limits))
        assert result["verdict"] == "pass"
        assert result["audit_id"].startswith("pretrade-")
        assert result["timestamp"]
        assert "projected_position" in result
        assert "margin_impact" in result
        assert "stress_results" in result
        assert "limit_utilization" in result

    def test_concentration_breach_returns_fail(self):
        # NLV=50k; order 10 shares @ 100 = 1k. Projected AAPL = 1k / 50k = 2% → fine.
        # Let's push it: 50 * 200 = 10k = 20% of 50k → exceeds 15% single-name limit
        ib = _build_mock_ib(nlv=50_000, excess=20_000, bid=200.0, ask=201.0, last=200.0, close=200.0)
        limits = {"limits": BASE_LIMITS}
        result = asyncio.run(MOD.run_check(ib, _order(quantity=50.0, limit_price=200.0), limits))
        assert result["verdict"] == "fail"
        codes = [f["reason_code"] for f in result["failures"]]
        assert "ERR_CONCENTRATION_SINGLE_NAME" in codes

    def test_margin_cushion_breach_returns_fail(self):
        # excess=5k < floor 10k → ERR_EXCESS_LIQUIDITY
        ib = _build_mock_ib(nlv=200_000, excess=5_000)
        limits = {"limits": BASE_LIMITS}
        result = asyncio.run(MOD.run_check(ib, _order(quantity=1.0, limit_price=50.0), limits))
        assert result["verdict"] == "fail"
        codes = [f["reason_code"] for f in result["failures"]]
        assert "ERR_EXCESS_LIQUIDITY" in codes

    def test_daily_loss_lockout_returns_fail(self):
        # -6k P&L breaches -5k USD limit
        ib = _build_mock_ib(nlv=200_000, excess=50_000, unrealized=-4_000, realized=-2_000)
        limits = {"limits": BASE_LIMITS}
        result = asyncio.run(MOD.run_check(ib, _order(quantity=1.0, limit_price=50.0), limits))
        assert result["verdict"] == "fail"
        codes = [f["reason_code"] for f in result["failures"]]
        assert "ERR_DAILY_LOSS_LOCKOUT" in codes

    def test_stale_quote_generates_warning(self):
        ib = _build_mock_ib(nlv=200_000, excess=50_000)
        # Patch the ticker time to be stale
        ticker = ib.reqMktData.return_value
        ticker.time = datetime.now(timezone.utc) - timedelta(seconds=500)
        limits = {"limits": BASE_LIMITS}
        result = asyncio.run(MOD.run_check(ib, _order(quantity=1.0, limit_price=50.0), limits))
        warning_codes = [w["code"] for w in result["warnings"]]
        assert "WARN_STALE_QUOTE" in warning_codes

    def test_wide_spread_generates_warning(self):
        ib = _build_mock_ib(nlv=200_000, excess=50_000, bid=80.0, ask=120.0, last=100.0, close=100.0)
        limits = {"limits": BASE_LIMITS}
        result = asyncio.run(MOD.run_check(ib, _order(quantity=1.0, limit_price=100.0), limits))
        warning_codes = [w["code"] for w in result["warnings"]]
        assert "WARN_WIDE_SPREAD" in warning_codes

    def test_result_is_json_serialisable(self):
        import json
        ib = _build_mock_ib(nlv=200_000, excess=50_000)
        limits = {"limits": BASE_LIMITS}
        result = asyncio.run(MOD.run_check(ib, _order(quantity=5.0, limit_price=100.0), limits))
        json.dumps(result)

    def test_open_order_stacking_fail(self):
        # Limit 25% of 200k = 50k. We create an open order for 40k notional,
        # then add a new order of 20k → combined 60k = 30% → breach.
        mock_trade = MagicMock()
        mock_trade.contract.symbol = "XYZ"
        mock_trade.contract.secType = "STK"
        mock_trade.order.action = "BUY"
        mock_trade.order.totalQuantity = 400.0
        mock_trade.order.lmtPrice = 100.0
        mock_trade.orderStatus.status = "Submitted"
        mock_trade.orderStatus.remaining = 400.0

        ib = _build_mock_ib(nlv=200_000, excess=50_000, open_trades=[mock_trade])
        limits = {"limits": BASE_LIMITS}
        # New order: 200 shares @ 100 = 20k
        result = asyncio.run(MOD.run_check(ib, _order(quantity=200.0, limit_price=100.0), limits))
        codes = [f["reason_code"] for f in result["failures"]]
        assert "ERR_OPEN_ORDER_STACKING" in codes
