"""Unit tests for backtesting/ptj_swing_framework/backtest_swing_framework.py.

Tests cover:
  - Portfolio.compute_shares: sizing from stop distance
  - Portfolio.can_enter: all constraint cases
  - Portfolio.enter / exit_position: P&L arithmetic
  - Portfolio.record_daily / drawdown
  - Trade statistics helper
  - run_backtest: minimal synthetic dataset
"""

import math
import sys
import types
import pathlib
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

# ── stub ib_async ──────────────────────────────────────────────────────────────

if "ib_async" not in sys.modules:
    _stub = types.ModuleType("ib_async")
    for _attr in ("IB", "Stock", "Future", "Contract", "Index", "Forex", "Option"):
        setattr(_stub, _attr, object)
    sys.modules["ib_async"] = _stub

# ── path setup ────────────────────────────────────────────────────────────────

_ROOT = pathlib.Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))

from backtesting.ptj_swing_framework.backtest_swing_framework import (
    ACCOUNT_START,
    EQUITY_BROAD,
    MAX_EQUITY_CLUSTER,
    MAX_POSITIONS,
    RISK_PCT,
    SLIPPAGE,
    WEEKLY_COOLDOWN_DAYS,
    Portfolio,
    _trade_stats,
    _max_drawdown_pct,
    run_backtest,
)


def _make_bars(n: int, base: float = 400.0, trend: float = 0.0) -> list:
    bars = []
    price = base
    for i in range(n):
        price *= (1 + trend)
        d = (date(2015, 1, 1) + timedelta(days=i)).isoformat()
        bars.append(SimpleNamespace(
            date=d,
            open=round(price * 0.999, 2),
            high=round(price * 1.005, 2),
            low=round(price * 0.995, 2),
            close=round(price, 2),
            volume=2_000_000,
        ))
    return bars


# ── Portfolio.compute_shares ───────────────────────────────────────────────────

class TestComputeShares:
    def test_basic(self):
        p = Portfolio(ACCOUNT_START)
        # risk = 25000 * 0.01 = 250, stop_dist = 5, shares_by_risk = 50
        # max_notional = 25000 * 0.20 = 5000, shares_by_notional = 50
        shares = p.compute_shares(100.0, 95.0)
        assert shares == 50

    def test_notional_cap(self):
        p = Portfolio(ACCOUNT_START)
        # stop_dist = 1, shares_by_risk = 250
        # max_notional = 5000, shares_by_notional = 50 (at $100/share)
        shares = p.compute_shares(100.0, 99.0)
        assert shares == 50  # capped by notional

    def test_zero_stop_distance(self):
        p = Portfolio(ACCOUNT_START)
        assert p.compute_shares(100.0, 100.0) == 0

    def test_zero_entry(self):
        p = Portfolio(ACCOUNT_START)
        assert p.compute_shares(0.0, 0.0) == 0


# ── Portfolio.can_enter ────────────────────────────────────────────────────────

class TestCanEnter:
    def test_clean_entry(self):
        p = Portfolio(ACCOUNT_START)
        can, reason = p.can_enter("SPY", "2015-06-01")
        assert can

    def test_already_in_position(self):
        p = Portfolio(ACCOUNT_START)
        p.positions["SPY"] = object()
        can, reason = p.can_enter("SPY", "2015-06-01")
        assert not can
        assert "already_in_position" in reason

    def test_max_positions(self):
        p = Portfolio(ACCOUNT_START)
        for i in range(MAX_POSITIONS):
            p.positions[f"X{i}"] = object()
        can, reason = p.can_enter("NEW", "2015-06-01")
        assert not can
        assert "max_positions" in reason

    def test_equity_cluster_limit(self):
        p = Portfolio(ACCOUNT_START)
        syms = list(EQUITY_BROAD)[:MAX_EQUITY_CLUSTER]
        for s in syms:
            p.positions[s] = object()
        remaining = [s for s in EQUITY_BROAD if s not in p.positions]
        if remaining:
            can, reason = p.can_enter(remaining[0], "2015-06-01")
            assert not can
            assert "equity_cluster" in reason

    def test_weekly_cooldown(self):
        p = Portfolio(ACCOUNT_START)
        recent = (date(2015, 6, 1) - timedelta(days=3)).isoformat()
        p.last_trade_date["SPY"] = recent
        can, reason = p.can_enter("SPY", "2015-06-01")
        assert not can
        assert "weekly_cooldown" in reason

    def test_cooldown_expired(self):
        p = Portfolio(ACCOUNT_START)
        old = (date(2015, 6, 1) - timedelta(days=WEEKLY_COOLDOWN_DAYS + 1)).isoformat()
        p.last_trade_date["SPY"] = old
        can, reason = p.can_enter("SPY", "2015-06-01")
        assert can


# ── Portfolio.enter / exit ─────────────────────────────────────────────────────

class TestEnterExit:
    def test_enter_records_position(self):
        p = Portfolio(ACCOUNT_START)
        ctx = p.enter("SPY", "long", 100.0, 95.0, "2015-01-05", 5)
        assert ctx is not None
        assert "SPY" in p.positions
        assert p.last_trade_date["SPY"] == "2015-01-05"

    def test_enter_zero_shares_returns_none(self):
        p = Portfolio(ACCOUNT_START)
        # stop_dist = 0 → shares = 0 → no position
        ctx = p.enter("SPY", "long", 100.0, 100.0, "2015-01-05", 5)
        assert ctx is None
        assert "SPY" not in p.positions

    def test_long_profit(self):
        p = Portfolio(ACCOUNT_START)
        ctx = p.enter("SPY", "long", 100.0, 95.0, "2015-01-05", 5)
        shares = ctx.shares
        entry_with_slip = 100.0 * (1 + SLIPPAGE)
        trade = p.exit_position("SPY", 110.0, "2015-01-20", 15, "time_stop")
        exit_with_slip = 110.0 * (1 - SLIPPAGE)
        expected_pnl = (exit_with_slip - entry_with_slip) * shares
        assert trade["pnl_usd"] == pytest.approx(expected_pnl, abs=0.01)
        assert trade["pnl_usd"] > 0
        assert "SPY" not in p.positions

    def test_long_loss(self):
        p = Portfolio(ACCOUNT_START)
        ctx = p.enter("SPY", "long", 100.0, 95.0, "2015-01-05", 5)
        trade = p.exit_position("SPY", 90.0, "2015-01-10", 5, "stop")
        assert trade["pnl_usd"] < 0

    def test_short_profit(self):
        p = Portfolio(ACCOUNT_START)
        ctx = p.enter("SPY", "short", 100.0, 105.0, "2015-01-05", 5)
        trade = p.exit_position("SPY", 85.0, "2015-01-20", 15, "time_stop")
        assert trade["pnl_usd"] > 0

    def test_partial_exit_keeps_position(self):
        p = Portfolio(ACCOUNT_START)
        ctx = p.enter("SPY", "long", 100.0, 95.0, "2015-01-05", 5)
        original_shares = ctx.shares
        if original_shares < 2:
            pytest.skip("Need at least 2 shares for partial test")
        partial = max(1, original_shares // 3)
        p.exit_position("SPY", 110.0, "2015-01-15", 10, "partial_profit",
                        shares_to_close=partial)
        assert "SPY" in p.positions
        assert p.positions["SPY"].shares == original_shares - partial


# ── _trade_stats ───────────────────────────────────────────────────────────────

class TestTradeStats:
    def test_empty(self):
        assert _trade_stats([]) == {"count": 0}

    def test_basic_stats(self):
        trades = [
            {"pnl_pct": 2.0, "pnl_usd": 50.0, "partial": False},
            {"pnl_pct": -1.0, "pnl_usd": -25.0, "partial": False},
            {"pnl_pct": 3.0, "pnl_usd": 75.0, "partial": False},
        ]
        stats = _trade_stats(trades)
        assert stats["count"] == 3
        assert stats["win_rate"] == pytest.approx(66.7, abs=0.1)
        assert stats["mean_pct"] == pytest.approx(4 / 3, abs=0.01)
        assert stats["best_pct"] == 3.0
        assert stats["worst_pct"] == -1.0

    def test_partial_excluded(self):
        trades = [
            {"pnl_pct": 2.0, "partial": False},
            {"pnl_pct": 1.0, "partial": True},  # should be excluded
        ]
        stats = _trade_stats(trades)
        assert stats["count"] == 1


# ── _max_drawdown_pct ──────────────────────────────────────────────────────────

class TestMaxDrawdown:
    def test_no_drawdown(self):
        daily = [{"equity": e} for e in [25000, 25100, 25200, 25300]]
        assert _max_drawdown_pct(daily) == 0.0

    def test_drawdown_computed(self):
        daily = [{"equity": e} for e in [25000, 20000, 25000]]
        dd = _max_drawdown_pct(daily)
        expected = (25000 - 20000) / 25000 * 100
        assert dd == pytest.approx(expected, abs=0.01)

    def test_empty(self):
        assert _max_drawdown_pct([]) == 0.0


# ── run_backtest (synthetic data, no network) ──────────────────────────────────

class TestRunBacktest:
    def test_completes_with_synthetic_bars(self):
        """run_backtest should complete and return a Portfolio without raising."""
        bars = _make_bars(500, base=400.0, trend=0.001)
        primary = {sym: bars for sym in ["SPY", "QQQ"]}
        macro = {"rates": bars, "dollar": bars, "credit": bars}

        portfolio = run_backtest(
            primary,
            macro,
            start_date="2015-01-01",
            end_date="2016-12-31",
        )
        assert isinstance(portfolio, Portfolio)
        assert portfolio.equity > 0
        assert len(portfolio.daily_equity) > 0

    def test_portfolio_equity_tracked(self):
        bars = _make_bars(500, base=100.0, trend=0.0005)
        primary = {"SPY": bars}
        macro = {"rates": bars, "dollar": bars, "credit": bars}

        portfolio = run_backtest(primary, macro,
                                 start_date="2015-01-01", end_date="2016-12-31")
        # daily_equity should have one entry per trading day in range
        assert len(portfolio.daily_equity) > 0
        for entry in portfolio.daily_equity:
            assert "date" in entry
            assert "equity" in entry
            assert entry["equity"] > 0
