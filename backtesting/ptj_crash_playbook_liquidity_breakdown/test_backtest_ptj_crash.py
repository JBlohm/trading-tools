"""
Tests for backtest_ptj_crash.py.

Covers:
  1. Data helpers: _df_to_bars round-trips
  2. No look-ahead bias: entry at next bar, not signal bar
  3. Gap-down open exclusion
  4. Stop trigger: close above risk_high exits trade
  5. De-risk signal exit
  6. Time-stop after MAX_HOLD_BARS
  7. Forward return calculation
  8. Metrics: signal frequency, forward stats, episode hits
  9. No trade duplication (only one open position at a time)
 10. End-of-data position close
"""

import sys
import os
from types import SimpleNamespace
from datetime import datetime, timedelta
from typing import List

import pandas as pd
import numpy as np
import pytest

# Allow importing from backtesting dir
_BT_DIR = os.path.dirname(__file__)
sys.path.insert(0, _BT_DIR)

import backtest_ptj_crash as bt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(close, open_=None, high=None, low=None, date="2020-01-01"):
    c = close
    return SimpleNamespace(
        date=date,
        open=open_ if open_ is not None else c,
        high=high if high is not None else c * 1.005,
        low=low if low is not None else c * 0.995,
        close=c,
        volume=1_000_000,
    )


def _make_bars(n: int, base_close: float, trend: float = 0.0, start_date="2015-01-02") -> List:
    """Generate n bars with optional per-bar trend."""
    bars = []
    c = base_close
    date = datetime.strptime(start_date, "%Y-%m-%d")
    while len(bars) < n:
        if date.weekday() < 5:  # weekdays only
            bars.append(_bar(c, date=date.strftime("%Y-%m-%d")))
            c = c * (1 + trend)
        date += timedelta(days=1)
    return bars


def _make_vix_bars(n: int, level: float = 15.0) -> List:
    return [_bar(level, date=f"2015-{(i // 22 + 1):02d}-{(i % 22 + 1):02d}") for i in range(n)]


def _df_to_bars_helper(n=5, base_close=100.0):
    """Build a minimal DataFrame and convert to bars."""
    dates = pd.date_range("2020-01-02", periods=n, freq="B")
    df = pd.DataFrame(
        {
            "date": dates,
            "open": [base_close] * n,
            "high": [base_close * 1.01] * n,
            "low": [base_close * 0.99] * n,
            "close": [base_close] * n,
            "volume": [1_000_000] * n,
        }
    )
    return df


# ---------------------------------------------------------------------------
# Tests: _df_to_bars
# ---------------------------------------------------------------------------

class TestDfToBars:
    def test_bar_count_matches(self):
        df = _df_to_bars_helper(10)
        bars = bt._df_to_bars(df)
        assert len(bars) == 10

    def test_bar_has_ohlcv_attrs(self):
        df = _df_to_bars_helper(1, base_close=150.0)
        bar = bt._df_to_bars(df)[0]
        assert hasattr(bar, "open")
        assert hasattr(bar, "high")
        assert hasattr(bar, "low")
        assert hasattr(bar, "close")
        assert hasattr(bar, "volume")
        assert hasattr(bar, "date")

    def test_close_value_preserved(self):
        df = _df_to_bars_helper(3, base_close=300.0)
        bars = bt._df_to_bars(df)
        assert all(b.close == pytest.approx(300.0) for b in bars)

    def test_date_is_string(self):
        df = _df_to_bars_helper(1)
        bar = bt._df_to_bars(df)[0]
        assert isinstance(bar.date, str)
        assert "-" in bar.date


# ---------------------------------------------------------------------------
# Tests: run_backtest — basic smoke / no-crash scenario
# ---------------------------------------------------------------------------

class TestRunBacktestSmoke:
    def test_returns_expected_keys(self):
        spy = _make_bars(300, 400.0)
        vix = _make_bars(300, 15.0)
        hyg = _make_bars(300, 85.0)
        rsp = _make_bars(300, 100.0)
        result = bt.run_backtest(spy, vix, hyg, rsp)
        assert "signal_log" in result
        assert "trade_log" in result
        assert "forward_returns" in result

    def test_signal_log_length(self):
        # With 300 bars and 260 warmup, should evaluate 40 bars
        spy = _make_bars(300, 400.0)
        vix = _make_bars(300, 15.0)
        hyg = _make_bars(300, 85.0)
        rsp = _make_bars(300, 100.0)
        result = bt.run_backtest(spy, vix, hyg, rsp)
        assert len(result["signal_log"]) == 40

    def test_no_trades_in_calm_market(self):
        # Flat market at 400 → no entry_trigger_short expected
        spy = _make_bars(300, 400.0)
        vix = _make_bars(300, 14.0)   # low VIX
        hyg = _make_bars(300, 87.0)
        rsp = _make_bars(300, 100.0)
        result = bt.run_backtest(spy, vix, hyg, rsp)
        assert len(result["trade_log"]) == 0

    def test_signal_log_has_required_fields(self):
        spy = _make_bars(300, 400.0)
        vix = _make_bars(300, 15.0)
        hyg = _make_bars(300, 85.0)
        rsp = _make_bars(300, 100.0)
        result = bt.run_backtest(spy, vix, hyg, rsp)
        for row in result["signal_log"]:
            assert "date" in row
            assert "state" in row
            assert "confidence" in row
            assert "close" in row

    def test_no_position_overlap(self):
        # Once we are in a position, we should not open another
        spy = _make_bars(400, 400.0)
        vix = _make_bars(400, 15.0)
        hyg = _make_bars(400, 85.0)
        rsp = _make_bars(400, 100.0)
        result = bt.run_backtest(spy, vix, hyg, rsp)
        # If any trades exist, verify they don't overlap in time
        trades = result["trade_log"]
        for i in range(len(trades) - 1):
            assert trades[i]["exit_date"] <= trades[i + 1]["entry_date"]


# ---------------------------------------------------------------------------
# Tests: entry logic
# ---------------------------------------------------------------------------

class TestEntryLogic:
    """
    Uses the same reliable entry scenario as TestExitLogic:
    260 flat bars at 350 → shelf=350; 3 bars at 400 (bounce); signal bar
    with close=280, open=355 → shelf_break+failed_retest+tr_expansion;
    next bar is the entry bar.
    """

    def _build_entry_scenario(self, signal_open=355.0, signal_close=280.0,
                               signal_high=405.0, signal_low=279.0):
        dates = pd.date_range("2015-01-02", periods=1000, freq="B")
        bars = []
        for i in range(260):
            bars.append(_bar(350.0, date=dates[i].strftime("%Y-%m-%d")))
        for i in range(3):
            bars.append(_bar(400.0, date=dates[260 + i].strftime("%Y-%m-%d")))
        bars.append(_bar(signal_close, open_=signal_open, high=signal_high,
                         low=signal_low, date=dates[263].strftime("%Y-%m-%d")))
        return bars, dates

    def _aux_bars(self, dates, n):
        """VIX=50, HYG with SMA50>last, RSP underperforming SPY → 3 confirmations."""
        vix = [_bar(50.0, date=dates[i].strftime("%Y-%m-%d")) for i in range(n)]
        hyg = [_bar(85.0 if i < 230 else 70.0, date=dates[i].strftime("%Y-%m-%d"))
               for i in range(n)]
        rsp = [_bar(100.0 if i < 254 else 77.0, date=dates[i].strftime("%Y-%m-%d"))
               for i in range(n)]
        return vix, hyg, rsp

    def test_entry_not_at_signal_bar(self):
        """Entry must be at the NEXT bar's open, not the signal bar's close."""
        spy, dates = self._build_entry_scenario()
        # Entry bar opens at 352 (above shelf=350) so the execution guard allows entry
        spy.append(_bar(349.0, open_=352.0, date=dates[264].strftime("%Y-%m-%d")))
        spy.append(_bar(349.0, date=dates[265].strftime("%Y-%m-%d")))  # allow exit check

        n = len(spy)
        vix, hyg, rsp = self._aux_bars(dates, n)
        result = bt.run_backtest(spy, vix, hyg, rsp)
        for trade in result["trade_log"]:
            assert trade["entry_date"] > trade["signal_date"], (
                f"Entry {trade['entry_date']} must be after signal {trade['signal_date']}"
            )

    def test_gap_down_open_prevents_entry(self):
        """Gap-down open (open < support_shelf=350) must not trigger entry."""
        spy, dates = self._build_entry_scenario(
            signal_open=340.0,   # 340 < 350 = shelf → gap_down_open=True
            signal_close=280.0,
        )
        spy.append(_bar(282.0, date=dates[264].strftime("%Y-%m-%d")))

        n = len(spy)
        vix, hyg, rsp = self._aux_bars(dates, n)
        result = bt.run_backtest(spy, vix, hyg, rsp)
        gap_date = dates[263].strftime("%Y-%m-%d")
        for trade in result["trade_log"]:
            assert trade["signal_date"] != gap_date, (
                "Entry should not fire on a gap-down open signal bar"
            )

    def test_next_bar_gap_down_prevents_entry(self):
        """Entry must be skipped when execution bar (d+1) opens below the signal shelf."""
        spy, dates = self._build_entry_scenario(
            signal_open=355.0,  # signal day open is above shelf=350 → gap_down_open=False
            signal_close=280.0,
            signal_high=405.0,
            signal_low=279.0,
        )
        # Next bar opens at 340, which is below support_shelf≈350 → crash-gap at execution
        spy.append(_bar(280.0, open_=340.0, date=dates[264].strftime("%Y-%m-%d")))
        spy.append(_bar(278.0, date=dates[265].strftime("%Y-%m-%d")))  # extra bar

        n = len(spy)
        vix, hyg, rsp = self._aux_bars(dates, n)
        result = bt.run_backtest(spy, vix, hyg, rsp)
        signal_date = dates[263].strftime("%Y-%m-%d")
        for trade in result["trade_log"]:
            assert trade["signal_date"] != signal_date, (
                "Entry should not fire when execution bar opens below the support shelf"
            )


# ---------------------------------------------------------------------------
# Tests: exit logic
# ---------------------------------------------------------------------------

class TestExitLogic:
    """
    Reliable entry setup: 260 flat bars at 350 (shelf=350) + 3 bars at 400
    (bounce above shelf) + 1 signal bar (open=355, close=280) with large
    range → shelf_break=True, failed_retest=True, tr_expansion=True.
    VIX=50 ensures vol_confirmed, and stressed HYG/RSP give all 3 confirmations.
    risk_high = support_shelf * 1.005 = 350 * 1.005 ≈ 351.75.
    """

    def _make_entry_scenario(self):
        """
        Build bars that reliably produce entry_trigger_short on the 2nd-to-last bar.

        - 260 bars at 350.0: establishes SMA200≈350, shelf lookback window at 350.
        - 3 bars at 400.0: bounce above shelf (needed for failed_retest detection).
        - 1 signal bar (open=355, high=405, low=279, close=280):
            shelf_break: 280 < 350*0.995=348.25 → True
            gap_down_open: 355 > 350 → False
            failed_retest: 400.0 > 350 in last 20 bars + close < 350 → True
            tr_expansion: TR = max(405-279, |405-400|, |279-400|) = max(126, 5, 121) = 126
                          vs ATR20 ≈ 350*0.005 ≈ 1.75 → massive expansion → True
        - 1 entry bar (open=352): trade enters at 352*(1-SLIPPAGE), risk_high≈351.75.
          open=352 > shelf=350 so the execution gap-down guard allows entry;
          close=349 < risk_high=351.75 so the stop does not fire on the entry bar.

        Returns: (bars, dates) where dates is the full pandas DatetimeIndex.
        """
        dates = pd.date_range("2015-01-02", periods=1000, freq="B")
        bars = []
        for i in range(260):
            bars.append(_bar(350.0, date=dates[i].strftime("%Y-%m-%d")))
        for i in range(260, 263):
            bars.append(_bar(400.0, date=dates[i].strftime("%Y-%m-%d")))
        # Signal bar
        bars.append(_bar(280.0, open_=355.0, high=405.0, low=279.0,
                         date=dates[263].strftime("%Y-%m-%d")))
        # Entry bar: opens above shelf (352 > 350) so execution guard passes;
        # closes at 349 (< risk_high=351.75) so stop does not fire immediately.
        bars.append(_bar(349.0, open_=352.0, date=dates[264].strftime("%Y-%m-%d")))
        return bars, dates

    def _aux_bars(self, dates, n):
        """Create aligned VIX/HYG/RSP bars for the first n dates.

        VIX=50 (well above 30 → vol_confirmed=True).
        HYG: 230 bars at 85 then 70 → SMA50 spans the transition, last < SMA50
             → credit_below_50dma=True.
        RSP: 254 bars at 100 then 77 → at d=263 rsp[-11]=100, rsp[-1]=77;
             rsp_roc10=-23% vs spy_roc10=-20% → rsp_vs_spy_10d=-3 < -1
             → breadth_weak=True.
        """
        vix = [_bar(50.0, date=dates[i].strftime("%Y-%m-%d")) for i in range(n)]
        hyg = [_bar(85.0 if i < 230 else 70.0, date=dates[i].strftime("%Y-%m-%d"))
               for i in range(n)]
        rsp = [_bar(100.0 if i < 254 else 77.0, date=dates[i].strftime("%Y-%m-%d"))
               for i in range(n)]
        return vix, hyg, rsp

    def test_stop_risk_high_triggers_exit(self):
        """When price closes above risk_high (support_shelf * 1.005 ≈ 351.75), exit fires."""
        bars, dates = self._make_entry_scenario()
        # Append 3 bars below risk_high (349 < 351.75), then 1 spike above risk_high
        for i in range(3):
            bars.append(_bar(349.0, date=dates[265 + i].strftime("%Y-%m-%d")))
        bars.append(_bar(360.0, open_=352.0, high=362.0, low=351.0,
                         date=dates[268].strftime("%Y-%m-%d")))

        n = len(bars)
        vix, hyg, rsp = self._aux_bars(dates, n)
        result = bt.run_backtest(bars, vix, hyg, rsp)
        stop_trades = [t for t in result["trade_log"] if t["exit_reason"] == "stop_risk_high"]
        assert len(stop_trades) >= 1, (
            f"stop_risk_high should fire; trades={result['trade_log']}"
        )

    def test_time_stop_triggers_after_max_hold(self):
        """After MAX_HOLD_BARS, position is closed with exit_reason=time_stop."""
        bars, dates = self._make_entry_scenario()
        # Add exactly MAX_HOLD_BARS bars at 349 (below risk_high=351.75 so stop never fires)
        for i in range(bt.MAX_HOLD_BARS):
            bars.append(_bar(349.0, date=dates[265 + i].strftime("%Y-%m-%d")))

        n = len(bars)
        vix, hyg, rsp = self._aux_bars(dates, n)
        result = bt.run_backtest(bars, vix, hyg, rsp)
        time_stop_trades = [t for t in result["trade_log"] if t["exit_reason"] == "time_stop"]
        assert len(time_stop_trades) >= 1, (
            f"time_stop must fire after MAX_HOLD_BARS={bt.MAX_HOLD_BARS}; "
            f"trades={result['trade_log']}"
        )

    def test_end_of_data_closes_position(self):
        """Open position at end of data is closed with exit_reason=end_of_data."""
        bars, dates = self._make_entry_scenario()
        # Only 2 post-entry bars at 349 (below risk_high=351.75) — well below MAX_HOLD_BARS
        for i in range(2):
            bars.append(_bar(349.0, date=dates[265 + i].strftime("%Y-%m-%d")))

        n = len(bars)
        vix, hyg, rsp = self._aux_bars(dates, n)
        result = bt.run_backtest(bars, vix, hyg, rsp)
        end_trades = [t for t in result["trade_log"] if t["exit_reason"] == "end_of_data"]
        assert len(end_trades) >= 1, (
            f"End-of-data must close any open position; trades={result['trade_log']}"
        )


# ---------------------------------------------------------------------------
# Tests: forward returns
# ---------------------------------------------------------------------------

class TestForwardReturns:
    """Uses the same reliable 260-flat+3-bounce+signal scenario."""

    def _build_scenario(self, n_future_bars=25):
        dates = pd.date_range("2015-01-02", periods=1000, freq="B")
        bars = []
        for i in range(260):
            bars.append(_bar(350.0, date=dates[i].strftime("%Y-%m-%d")))
        for i in range(3):
            bars.append(_bar(400.0, date=dates[260 + i].strftime("%Y-%m-%d")))
        bars.append(_bar(280.0, open_=355.0, high=405.0, low=279.0,
                         date=dates[263].strftime("%Y-%m-%d")))
        for i in range(n_future_bars):
            # Price continues falling — profitable for short
            close = 280.0 * (0.995 ** i)
            bars.append(_bar(close, date=dates[264 + i].strftime("%Y-%m-%d")))
        return bars, dates

    def _aux(self, dates, n):
        vix = [_bar(50.0, date=dates[i].strftime("%Y-%m-%d")) for i in range(n)]
        hyg = [_bar(85.0 if i < 230 else 70.0, date=dates[i].strftime("%Y-%m-%d")) for i in range(n)]
        rsp = [_bar(100.0 if i < 254 else 77.0, date=dates[i].strftime("%Y-%m-%d")) for i in range(n)]
        return vix, hyg, rsp

    def test_forward_return_keys_present(self):
        """forward_returns entries must have the 5d/10d/20d keys."""
        bars, dates = self._build_scenario(25)
        n = len(bars)
        vix, hyg, rsp = self._aux(dates, n)
        result = bt.run_backtest(bars, vix, hyg, rsp)
        for r in result["forward_returns"]:
            assert "fwd_5d_return_pct" in r
            assert "fwd_10d_return_pct" in r
            assert "fwd_20d_return_pct" in r

    def test_forward_return_positive_when_price_falls(self):
        """Forward return > 0 means price fell after the signal (short profits)."""
        bars, dates = self._build_scenario(25)
        n = len(bars)
        vix, hyg, rsp = self._aux(dates, n)
        result = bt.run_backtest(bars, vix, hyg, rsp)
        fwd = result["forward_returns"]
        if fwd:
            for r in fwd:
                if r["fwd_5d_return_pct"] is not None:
                    assert r["fwd_5d_return_pct"] > 0, "Short should profit when price falls"

    def test_forward_return_none_at_end_of_data(self):
        """Forward returns must be None when there are insufficient future bars."""
        bars, dates = self._build_scenario(3)
        n = len(bars)
        vix, hyg, rsp = self._aux(dates, n)
        result = bt.run_backtest(bars, vix, hyg, rsp)
        fwd = result["forward_returns"]
        if fwd:
            last = fwd[-1]
            assert last["fwd_20d_return_pct"] is None


# ---------------------------------------------------------------------------
# Tests: compute_metrics
# ---------------------------------------------------------------------------

class TestComputeMetrics:
    def _dummy_results(self):
        return {
            "signal_log": [
                {"date": "2020-03-09", "state": "entry_trigger_short",
                 "confidence": 0.85, "confirmations": 3, "close": 274.0,
                 "support_shelf": 290.0, "vix": 55.0, "below_200dma": True,
                 "shelf_break": True, "gap_down_open": False, "in_position": False},
                {"date": "2020-03-10", "state": "no_setup",
                 "confidence": 0.8, "confirmations": 0, "close": 300.0,
                 "support_shelf": 290.0, "vix": 20.0, "below_200dma": False,
                 "shelf_break": False, "gap_down_open": False, "in_position": False},
            ],
            "trade_log": [
                {
                    "entry_date": "2020-03-10",
                    "entry_price": 273.0,
                    "exit_date": "2020-03-20",
                    "exit_price": 230.0,
                    "exit_reason": "time_stop",
                    "bars_held": 10,
                    "return_pct": 15.75,
                    "signal_date": "2020-03-09",
                    "entry_support_shelf": 290.0,
                    "position_size": 0.33,
                },
            ],
            "forward_returns": [
                {
                    "signal_date": "2020-03-09",
                    "close_at_signal": 274.0,
                    "fwd_5d_return_pct": 8.5,
                    "fwd_10d_return_pct": 15.0,
                    "fwd_20d_return_pct": 20.0,
                }
            ],
        }

    def test_metrics_has_required_keys(self):
        results = self._dummy_results()
        metrics = bt.compute_metrics(results, [])
        assert "signal_frequency" in metrics
        assert "forward_return_stats" in metrics
        assert "trade_stats" in metrics
        assert "episode_hits" in metrics

    def test_signal_frequency_sum(self):
        results = self._dummy_results()
        metrics = bt.compute_metrics(results, [])
        total = sum(v["count"] for v in metrics["signal_frequency"].values())
        assert total == len(results["signal_log"])

    def test_trade_stats_win_rate(self):
        results = self._dummy_results()
        metrics = bt.compute_metrics(results, [])
        # One winning trade → 100% win rate
        assert metrics["trade_stats"]["win_rate_pct"] == pytest.approx(100.0)

    def test_episode_hits_structure(self):
        results = self._dummy_results()
        metrics = bt.compute_metrics(results, [])
        for ep_name in bt.EPISODES:
            assert ep_name in metrics["episode_hits"]
            ep = metrics["episode_hits"][ep_name]
            assert "stress_detected" in ep
            assert "entry_triggered" in ep
            assert "entry_trigger_days" in ep
            assert "setup_armed_days" in ep

    def test_forward_stat_5d_present(self):
        results = self._dummy_results()
        metrics = bt.compute_metrics(results, [])
        s5 = metrics["forward_return_stats"].get("5d", {})
        assert s5.get("n") == 1
        assert s5.get("mean") == pytest.approx(8.5)

    def test_max_drawdown_negative_or_zero(self):
        results = self._dummy_results()
        metrics = bt.compute_metrics(results, [])
        # With a winning trade, max drawdown should be 0 or very small
        # (drawdown on the equity curve is from peak → trough)
        dd = metrics["trade_stats"].get("max_drawdown_pct", 0)
        assert dd <= 0  # drawdown is always <= 0

    def test_empty_results_no_crash(self):
        results = {"signal_log": [], "trade_log": [], "forward_returns": []}
        metrics = bt.compute_metrics(results, [])
        assert metrics["total_bars_evaluated"] == 0
        assert metrics["trade_stats"] == {}


# ---------------------------------------------------------------------------
# Tests: slippage applied correctly
# ---------------------------------------------------------------------------

class TestSlippage:
    def test_entry_price_below_open(self):
        """Short entry price = next_bar.open * (1 - SLIPPAGE)."""
        dates = pd.date_range("2015-01-02", periods=1000, freq="B")
        bars = []
        for i in range(260):
            bars.append(_bar(350.0, date=dates[i].strftime("%Y-%m-%d")))
        for i in range(3):
            bars.append(_bar(400.0, date=dates[260 + i].strftime("%Y-%m-%d")))
        # Signal bar
        bars.append(_bar(280.0, open_=355.0, high=405.0, low=279.0,
                         date=dates[263].strftime("%Y-%m-%d")))
        # Entry bar: open=352 (above shelf=350) so execution guard allows entry;
        # close=349 (< risk_high=351.75) so stop does not fire on entry bar.
        next_open = 352.0
        bars.append(_bar(349.0, open_=next_open, date=dates[264].strftime("%Y-%m-%d")))
        for i in range(5):
            bars.append(_bar(349.0, date=dates[265 + i].strftime("%Y-%m-%d")))

        n = len(bars)
        vix = [_bar(50.0, date=dates[i].strftime("%Y-%m-%d")) for i in range(n)]
        hyg = [_bar(85.0 if i < 230 else 70.0, date=dates[i].strftime("%Y-%m-%d")) for i in range(n)]
        rsp = [_bar(100.0 if i < 254 else 77.0, date=dates[i].strftime("%Y-%m-%d")) for i in range(n)]

        result = bt.run_backtest(bars, vix, hyg, rsp)
        trades = result["trade_log"]
        assert len(trades) >= 1, "Expected at least one trade"
        expected_entry = next_open * (1 - bt.SLIPPAGE)
        assert trades[0]["entry_price"] == pytest.approx(expected_entry, rel=1e-4)


# ---------------------------------------------------------------------------
# Tests: write_results artifact consistency
# ---------------------------------------------------------------------------

class TestWriteResults:
    def _empty_results(self):
        return {"signal_log": [], "trade_log": [], "forward_returns": []}

    def _metrics(self):
        return {
            "total_bars_evaluated": 0,
            "signal_frequency": {},
            "forward_return_stats": {},
            "trade_stats": {},
            "episode_hits": {
                k: {
                    "period": f"{v[0]} – {v[1]}",
                    "stress_detected": False,
                    "entry_triggered": False,
                    "entry_trigger_days": [],
                    "setup_armed_days": [],
                }
                for k, v in bt.EPISODES.items()
            },
        }

    def test_trades_csv_written_when_empty(self, tmp_path, monkeypatch):
        """trades.csv must be written even when trade_log is empty."""
        monkeypatch.setattr(bt, "RESULTS_DIR", str(tmp_path))
        bt.write_results(self._empty_results(), self._metrics())
        trade_path = tmp_path / "trades.csv"
        assert trade_path.exists(), "trades.csv must be created even with no trades"
        content = trade_path.read_text()
        assert len(content.strip()) > 0, "trades.csv must contain at least a header"

    def test_trades_csv_overwrites_stale_file(self, tmp_path, monkeypatch):
        """trades.csv from a prior run must be replaced, not left stale."""
        monkeypatch.setattr(bt, "RESULTS_DIR", str(tmp_path))
        stale_path = tmp_path / "trades.csv"
        stale_path.write_text("stale,data\nrow1,row2\n")
        bt.write_results(self._empty_results(), self._metrics())
        content = stale_path.read_text()
        assert "stale" not in content, "Stale rows must be replaced on an empty rerun"
        # Should contain only the header row
        lines = [l for l in content.splitlines() if l.strip()]
        assert len(lines) == 1, f"Empty run should produce header-only CSV; got: {lines}"
