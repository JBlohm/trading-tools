"""
Tests for backtest_short_instruments_comparison.py.

Test coverage:
  1.  run_comparison: returns required top-level keys
  2.  run_comparison: all configured instruments appear in comparison_trades
  3.  run_comparison: long direction trade is positive when instrument rises
  4.  run_comparison: short direction trade is positive when instrument falls
  5.  run_comparison: trade entry_date is always after signal_date (no look-ahead)
  6.  run_comparison: missing alt instrument bar is skipped gracefully
  7.  run_comparison: VXX rows include vxx_gross_decay_pct and vxx_decay_adj_return_pct
  8.  run_comparison: episode_coverage keys match SHORT_EPISODES
  9.  compute_sector_rotation_spread: returns correct spread return
  10. compute_sector_rotation_spread: requires all three legs to compute a row
  11. compute_sector_rotation_spread: empty when any leg is missing all trades
  12. compute_comparison_metrics: all instruments have required keys
  13. compute_comparison_metrics: win_rate_pct is 100% when all trades profitable
  14. compute_comparison_metrics: Sector_Rotation metric computed
  15. compute_comparison_metrics: VXX_decay_adj metric present when VXX has trades
  16. rank_instruments: excludes VXX_decay_adj from rankings
  17. rank_instruments: higher Sharpe ranked first
  18. _nearest_bar: returns None when offset exceeds max_offset_days
  19. _nearest_bar: returns correct bar when exact date matches
  20. write_comparison_report: creates file on disk with expected content
"""

import os
import sys
import tempfile
import types
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import List

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Path setup — mirrors backtest_short_instruments_comparison.py
# ---------------------------------------------------------------------------
_BT_DIR = os.path.dirname(__file__)
_ROOT = os.path.dirname(os.path.dirname(_BT_DIR))
sys.path.insert(0, _ROOT)
sys.path.insert(0, _BT_DIR)

if "ib_async" not in sys.modules:
    _stub = types.ModuleType("ib_async")
    for _attr in ("IB", "Stock", "Future", "Contract", "Index", "Forex", "Option"):
        setattr(_stub, _attr, object)
    sys.modules["ib_async"] = _stub

import backtest_ptj_macro_breakout as bt
import backtest_short_instruments_comparison as cmp


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _bar(close: float, open_: float = None, high: float = None,
         low: float = None, volume: float = 1_000_000, date: str = "2020-01-02") -> SimpleNamespace:
    c = close
    return SimpleNamespace(
        date=date,
        open=open_ if open_ is not None else c,
        high=high if high is not None else c * 1.005,
        low=low if low is not None else c * 0.995,
        close=c,
        volume=volume,
    )


def _make_bars(n: int, base_close: float = 100.0, trend: float = 0.0,
               start: str = "2015-01-02", volume: float = 1_000_000) -> List[SimpleNamespace]:
    bars = []
    c = base_close
    date = datetime.strptime(start, "%Y-%m-%d")
    while len(bars) < n:
        if date.weekday() < 5:
            bars.append(_bar(c, volume=volume, date=date.strftime("%Y-%m-%d")))
            c = c * (1 + trend)
        date += timedelta(days=1)
    return bars


def _flat_bars(n: int, close: float = 100.0, start: str = "2015-01-02") -> List[SimpleNamespace]:
    return _make_bars(n, base_close=close, trend=0.0, start=start)


TOTAL = bt.WARMUP_BARS + 100


def _build_spy_and_alts(n: int = TOTAL, close: float = 100.0):
    """Build a minimal matching set of SPY and alt bars for backtest."""
    bars = _flat_bars(n, close=close)
    return bars


def _build_alt_map(spy_bars, long_close: float = 50.0, short_close: float = 200.0,
                   trend: float = 0.0) -> dict:
    """
    Build alt_bars_map with the same dates as spy_bars.
    All instruments get flat bars at given closes.
    """
    n = len(spy_bars)
    start = spy_bars[0].date

    def _aligned_bars(c, t):
        bars = []
        for b in spy_bars:
            bars.append(SimpleNamespace(
                date=b.date,
                open=c * (1 + t),
                high=c * (1 + t) * 1.005,
                low=c * (1 + t) * 0.995,
                close=c,
                volume=1_000_000,
            ))
            c = c * (1 + t)
        return bars

    return {
        "SH":        {"bars": _aligned_bars(50.0, trend),  "direction": "long"},
        "TLT_short": {"bars": _aligned_bars(100.0, trend), "direction": "short"},
        "HYG_short": {"bars": _aligned_bars(80.0, trend),  "direction": "short"},
        "VXX":       {"bars": _aligned_bars(20.0, trend),  "direction": "long"},
        "XLU":       {"bars": _aligned_bars(60.0, trend),  "direction": "long"},
        "XLV":       {"bars": _aligned_bars(70.0, trend),  "direction": "long"},
        "XLY_short": {"bars": _aligned_bars(90.0, trend),  "direction": "short"},
    }


# ---------------------------------------------------------------------------
# 1. run_comparison returns required top-level keys
# ---------------------------------------------------------------------------

class TestRunComparisonKeys:
    def test_top_level_keys_present(self):
        spy = _flat_bars(TOTAL)
        alts = _build_alt_map(spy)
        result = cmp.run_comparison(spy, [], [], [], alts)
        for key in ("spy_signal_log", "spy_short_trades", "comparison_trades", "episode_coverage"):
            assert key in result, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# 2. run_comparison: all configured instruments in comparison_trades
# ---------------------------------------------------------------------------

class TestRunComparisonInstruments:
    def test_all_instruments_present(self):
        spy = _flat_bars(TOTAL)
        alts = _build_alt_map(spy)
        result = cmp.run_comparison(spy, [], [], [], alts)
        for name in alts:
            assert name in result["comparison_trades"], f"{name} missing from comparison_trades"

    def test_comparison_trades_is_list(self):
        spy = _flat_bars(TOTAL)
        alts = _build_alt_map(spy)
        result = cmp.run_comparison(spy, [], [], [], alts)
        for name in alts:
            assert isinstance(result["comparison_trades"][name], list)


# ---------------------------------------------------------------------------
# 3 & 4. Long / short direction returns
# ---------------------------------------------------------------------------

class TestDirectionReturns:
    def _trades_with_rising_alts(self, direction: str):
        """
        Return comparison_trades for an instrument where the alt instrument
        rises 10% during the trade. For 'long', this should be positive.
        For 'short', this should be negative.
        """
        spy = _flat_bars(TOTAL)
        # Build alt that rises steadily
        alt_bars = []
        c = 100.0
        for b in spy:
            c_new = c * 1.001  # 0.1% per bar
            alt_bars.append(SimpleNamespace(
                date=b.date, open=c, high=c * 1.005, low=c * 0.995, close=c_new, volume=1e6
            ))
            c = c_new

        alts = {"TEST": {"bars": alt_bars, "direction": direction}}
        result = cmp.run_comparison(spy, [], [], [], alts)
        return result["comparison_trades"]["TEST"]

    def test_long_direction_profits_when_alt_rises(self):
        trades = self._trades_with_rising_alts("long")
        if not trades:
            pytest.skip("No short signals generated (flat spy bars); direction test N/A")
        # All trades should be positive since alt is always rising
        for t in trades:
            assert t["return_pct"] > 0, f"Expected positive return for long on rising alt; got {t['return_pct']}"

    def test_short_direction_loses_when_alt_rises(self):
        trades = self._trades_with_rising_alts("short")
        if not trades:
            pytest.skip("No short signals generated (flat spy bars); direction test N/A")
        for t in trades:
            assert t["return_pct"] < 0, f"Expected negative return for short on rising alt; got {t['return_pct']}"


# ---------------------------------------------------------------------------
# 5. No look-ahead: entry_date > signal_date
# ---------------------------------------------------------------------------

class TestNoLookAhead:
    def test_entry_date_after_signal_date(self):
        spy = _flat_bars(TOTAL)
        alts = _build_alt_map(spy)
        result = cmp.run_comparison(spy, [], [], [], alts)
        for name, trades in result["comparison_trades"].items():
            for t in trades:
                assert t["entry_date"] > t["signal_date"], (
                    f"Look-ahead in {name}: entry {t['entry_date']} <= signal {t['signal_date']}"
                )


# ---------------------------------------------------------------------------
# 6. Missing alt instrument bar is skipped gracefully
# ---------------------------------------------------------------------------

class TestMissingBarGrace:
    def test_no_crash_when_alt_bars_empty(self):
        spy = _flat_bars(TOTAL)
        alts = {"EMPTY": {"bars": [], "direction": "long"}}
        result = cmp.run_comparison(spy, [], [], [], alts)
        assert result["comparison_trades"]["EMPTY"] == []

    def test_partial_bar_coverage_handled(self):
        spy = _flat_bars(TOTAL)
        # Provide only half the bars for alt instrument
        half = _flat_bars(TOTAL // 2)
        alts = {"HALF": {"bars": half, "direction": "long"}}
        result = cmp.run_comparison(spy, [], [], [], alts)
        # Should complete without exception
        assert "HALF" in result["comparison_trades"]


# ---------------------------------------------------------------------------
# 7. VXX rows include decay fields
# ---------------------------------------------------------------------------

class TestVxxDecayFields:
    def _get_vxx_trades(self):
        spy = _flat_bars(TOTAL)
        alts = {"VXX": {"bars": _flat_bars(TOTAL, close=20.0), "direction": "long"}}
        result = cmp.run_comparison(spy, [], [], [], alts)
        return result["comparison_trades"]["VXX"]

    def test_vxx_trades_have_decay_fields(self):
        trades = self._get_vxx_trades()
        if not trades:
            pytest.skip("No VXX trades; decay field test N/A")
        for t in trades:
            assert "vxx_gross_decay_pct" in t, "Missing vxx_gross_decay_pct"
            assert "vxx_decay_adj_return_pct" in t, "Missing vxx_decay_adj_return_pct"

    def test_vxx_decay_proportional_to_bars_held(self):
        trades = self._get_vxx_trades()
        if not trades:
            pytest.skip("No VXX trades")
        for t in trades:
            expected_decay = t["bars_held"] * cmp.VXX_DECAY_PER_BAR * 100
            assert abs(t["vxx_gross_decay_pct"] - expected_decay) < 0.01, (
                f"Decay mismatch: got {t['vxx_gross_decay_pct']}, expected {expected_decay}"
            )

    def test_vxx_decay_adj_equals_raw_minus_decay(self):
        trades = self._get_vxx_trades()
        if not trades:
            pytest.skip("No VXX trades")
        for t in trades:
            expected = t["return_pct"] - t["vxx_gross_decay_pct"]
            assert abs(t["vxx_decay_adj_return_pct"] - expected) < 0.001


# ---------------------------------------------------------------------------
# 8. episode_coverage keys match SHORT_EPISODES
# ---------------------------------------------------------------------------

class TestEpisodeCoverage:
    def test_episode_keys_match_config(self):
        spy = _flat_bars(TOTAL)
        alts = _build_alt_map(spy)
        result = cmp.run_comparison(spy, [], [], [], alts)
        for ep in cmp.SHORT_EPISODES:
            assert ep in result["episode_coverage"], f"Episode {ep!r} missing"

    def test_episode_coverage_has_required_fields(self):
        spy = _flat_bars(TOTAL)
        alts = _build_alt_map(spy)
        result = cmp.run_comparison(spy, [], [], [], alts)
        for ep, ev in result["episode_coverage"].items():
            for field in ("period", "entry_trigger_days", "candidate_days",
                          "entry_triggered", "trades_executed"):
                assert field in ev, f"Episode {ep!r} missing field {field!r}"


# ---------------------------------------------------------------------------
# 9. compute_sector_rotation_spread: correct spread return
# ---------------------------------------------------------------------------

class TestSectorRotationSpread:
    def _build_comparison_trades(self, xlu_ret, xlv_ret, xly_short_ret):
        """Build minimal comparison_trades with one trade each."""
        entry = "2020-03-02"
        trade = {
            "signal_date": "2020-02-28",
            "entry_date": entry,
            "exit_date": "2020-03-20",
            "bars_held": 14,
            "exit_reason": "time_stop",
            "spy_return_pct": -8.0,
        }
        return {
            "XLU":       [{**trade, "return_pct": xlu_ret}],
            "XLV":       [{**trade, "return_pct": xlv_ret}],
            "XLY_short": [{**trade, "return_pct": xly_short_ret}],
        }

    def test_spread_return_is_average_of_all_legs(self):
        ct = self._build_comparison_trades(xlu_ret=4.0, xlv_ret=2.0, xly_short_ret=6.0)
        spread = cmp.compute_sector_rotation_spread(ct)
        assert len(spread) == 1
        expected = ((4.0 + 2.0) / 2 + 6.0) / 2
        assert abs(spread[0]["return_pct"] - expected) < 0.001

    def test_spread_includes_component_returns(self):
        ct = self._build_comparison_trades(xlu_ret=3.0, xlv_ret=1.0, xly_short_ret=5.0)
        spread = cmp.compute_sector_rotation_spread(ct)
        assert "xlu_return_pct" in spread[0]
        assert "xlv_return_pct" in spread[0]
        assert "xly_short_return_pct" in spread[0]


# ---------------------------------------------------------------------------
# 10. Spread requires all three legs
# ---------------------------------------------------------------------------

class TestSectorRotationRequiresAllLegs:
    def test_missing_xlv_produces_no_spread(self):
        ct = {
            "XLU": [{"entry_date": "2020-03-02", "return_pct": 4.0, "signal_date": "2020-02-28",
                      "exit_date": "2020-03-20", "bars_held": 14, "exit_reason": "time_stop",
                      "spy_return_pct": -8.0}],
            "XLV": [],
            "XLY_short": [{"entry_date": "2020-03-02", "return_pct": 6.0, "signal_date": "2020-02-28",
                            "exit_date": "2020-03-20", "bars_held": 14, "exit_reason": "time_stop",
                            "spy_return_pct": -8.0}],
        }
        spread = cmp.compute_sector_rotation_spread(ct)
        assert spread == []


# ---------------------------------------------------------------------------
# 11. compute_sector_rotation_spread: empty when any leg has no trades
# ---------------------------------------------------------------------------

class TestSectorRotationEmpty:
    def test_empty_when_no_shared_entry_dates(self):
        ct = {
            "XLU": [{"entry_date": "2020-03-02", "return_pct": 4.0, "signal_date": "2020-02-28",
                      "exit_date": "2020-03-20", "bars_held": 14, "exit_reason": "time_stop",
                      "spy_return_pct": -8.0}],
            "XLV": [{"entry_date": "2021-01-05", "return_pct": 2.0, "signal_date": "2021-01-04",
                      "exit_date": "2021-01-25", "bars_held": 14, "exit_reason": "time_stop",
                      "spy_return_pct": -3.0}],
            "XLY_short": [{"entry_date": "2020-03-02", "return_pct": 6.0, "signal_date": "2020-02-28",
                            "exit_date": "2020-03-20", "bars_held": 14, "exit_reason": "time_stop",
                            "spy_return_pct": -8.0}],
        }
        spread = cmp.compute_sector_rotation_spread(ct)
        assert spread == []


# ---------------------------------------------------------------------------
# 12. compute_comparison_metrics: required keys per instrument
# ---------------------------------------------------------------------------

class TestComputeMetricsKeys:
    def _dummy_comparison_trades(self):
        trade = {
            "entry_date": "2020-03-02",
            "exit_date": "2020-03-16",
            "signal_date": "2020-02-28",
            "bars_held": 10,
            "exit_reason": "time_stop",
            "return_pct": 5.0,
            "spy_return_pct": -8.0,
        }
        ct = {name: [dict(trade)] for name in cmp.INSTRUMENTS}
        ct["VXX"][0]["vxx_gross_decay_pct"] = 4.0
        ct["VXX"][0]["vxx_decay_adj_return_pct"] = 1.0
        return ct

    def test_all_metrics_keys_present(self):
        ct = self._dummy_comparison_trades()
        sr = cmp.compute_sector_rotation_spread(ct)
        metrics = cmp.compute_comparison_metrics(ct, sr)
        for name in cmp.INSTRUMENTS:
            assert name in metrics, f"{name} missing from metrics"
        assert "Sector_Rotation" in metrics

    def test_each_instrument_has_total_trades(self):
        ct = self._dummy_comparison_trades()
        sr = cmp.compute_sector_rotation_spread(ct)
        metrics = cmp.compute_comparison_metrics(ct, sr)
        for name in cmp.INSTRUMENTS:
            assert "total_trades" in metrics[name], f"{name} missing total_trades"


# ---------------------------------------------------------------------------
# 13. compute_comparison_metrics: win_rate_pct 100% when all profitable
# ---------------------------------------------------------------------------

class TestWinRateAllProfitable:
    def test_win_rate_100_when_all_positive(self):
        trades = [
            {"entry_date": f"2020-0{i+1}-01", "exit_date": f"2020-0{i+1}-15",
             "signal_date": f"2020-0{i+1}-01", "bars_held": 10, "exit_reason": "time_stop",
             "return_pct": float(i + 1), "spy_return_pct": -5.0}
            for i in range(3)
        ]
        ct = {"SH": trades}
        metrics = cmp.compute_comparison_metrics(ct, [])
        assert metrics["SH"]["win_rate_pct"] == 100.0

    def test_win_rate_0_when_all_negative(self):
        trades = [
            {"entry_date": f"2020-0{i+1}-01", "exit_date": f"2020-0{i+1}-15",
             "signal_date": f"2020-0{i+1}-01", "bars_held": 10, "exit_reason": "time_stop",
             "return_pct": -(float(i + 1)), "spy_return_pct": 2.0}
            for i in range(3)
        ]
        ct = {"SH": trades}
        metrics = cmp.compute_comparison_metrics(ct, [])
        assert metrics["SH"]["win_rate_pct"] == 0.0


# ---------------------------------------------------------------------------
# 14. compute_comparison_metrics: Sector_Rotation metric computed
# ---------------------------------------------------------------------------

class TestSectorRotationMetric:
    def test_sector_rotation_in_metrics(self):
        sr_trades = [
            {"entry_date": "2020-03-02", "exit_date": "2020-03-16", "bars_held": 10,
             "return_pct": 3.5, "xlu_return_pct": 2.0, "xlv_return_pct": 3.0,
             "xly_short_return_pct": 5.5, "signal_date": "2020-02-28",
             "exit_reason": "time_stop", "spy_return_pct": -8.0},
        ]
        metrics = cmp.compute_comparison_metrics({}, sr_trades)
        assert "Sector_Rotation" in metrics
        assert metrics["Sector_Rotation"]["total_trades"] == 1
        assert metrics["Sector_Rotation"]["win_rate_pct"] == 100.0


# ---------------------------------------------------------------------------
# 15. VXX_decay_adj present when VXX has trades
# ---------------------------------------------------------------------------

class TestVxxDecayAdjMetric:
    def test_vxx_decay_adj_present_with_trades(self):
        trade = {
            "entry_date": "2020-03-02", "exit_date": "2020-03-16",
            "signal_date": "2020-02-28", "bars_held": 10,
            "exit_reason": "time_stop", "return_pct": 8.0,
            "spy_return_pct": -8.0,
            "vxx_gross_decay_pct": 4.0, "vxx_decay_adj_return_pct": 4.0,
        }
        ct = {"VXX": [trade]}
        metrics = cmp.compute_comparison_metrics(ct, [])
        assert "VXX_decay_adj" in metrics

    def test_vxx_decay_adj_absent_when_no_vxx_trades(self):
        metrics = cmp.compute_comparison_metrics({"VXX": []}, [])
        assert "VXX_decay_adj" not in metrics


# ---------------------------------------------------------------------------
# 16. rank_instruments excludes VXX_decay_adj
# ---------------------------------------------------------------------------

class TestRankInstruments:
    def test_vxx_decay_adj_excluded_from_ranking(self):
        metrics = {
            "SH": {"total_trades": 5, "win_rate_pct": 60.0, "avg_return_pct": 2.0,
                   "sharpe_ratio": 1.5, "max_drawdown_pct": -5.0},
            "VXX": {"total_trades": 3, "win_rate_pct": 50.0, "avg_return_pct": 1.0,
                    "sharpe_ratio": 0.8, "max_drawdown_pct": -10.0},
            "VXX_decay_adj": {"avg_return_pct": -1.0, "win_rate_pct": 33.0,
                              "note": "informational"},
        }
        ranked = cmp.rank_instruments(metrics)
        names = [r[0] for r in ranked]
        assert "VXX_decay_adj" not in names
        assert "SH" in names
        assert "VXX" in names


# ---------------------------------------------------------------------------
# 17. rank_instruments: higher Sharpe ranked first
# ---------------------------------------------------------------------------

class TestRankOrder:
    def test_higher_sharpe_ranked_first(self):
        metrics = {
            "LOW":  {"total_trades": 5, "win_rate_pct": 40.0, "avg_return_pct": 1.0,
                     "sharpe_ratio": 0.3, "max_drawdown_pct": -8.0},
            "HIGH": {"total_trades": 5, "win_rate_pct": 70.0, "avg_return_pct": 4.0,
                     "sharpe_ratio": 2.1, "max_drawdown_pct": -3.0},
            "MID":  {"total_trades": 5, "win_rate_pct": 55.0, "avg_return_pct": 2.5,
                     "sharpe_ratio": 1.0, "max_drawdown_pct": -5.0},
        }
        ranked = cmp.rank_instruments(metrics)
        names = [r[0] for r in ranked]
        assert names.index("HIGH") < names.index("MID") < names.index("LOW")


# ---------------------------------------------------------------------------
# 18 & 19. _nearest_bar helper
# ---------------------------------------------------------------------------

class TestNearestBar:
    def _bars(self):
        return [
            _bar(100.0, date="2020-03-02"),
            _bar(101.0, date="2020-03-03"),
            _bar(102.0, date="2020-03-04"),
        ]

    def test_exact_date_match(self):
        bars = self._bars()
        result = cmp._nearest_bar(bars, "2020-03-03")
        assert result is not None
        assert result.date == "2020-03-03"

    def test_returns_none_when_offset_exceeds_max(self):
        bars = self._bars()
        result = cmp._nearest_bar(bars, "2020-04-01", max_offset_days=5)
        assert result is None

    def test_returns_none_for_empty_bars(self):
        assert cmp._nearest_bar([], "2020-03-03") is None

    def test_returns_closest_within_offset(self):
        bars = self._bars()
        result = cmp._nearest_bar(bars, "2020-03-05", max_offset_days=5)
        assert result is not None
        assert result.date == "2020-03-04"


# ---------------------------------------------------------------------------
# 20. write_comparison_report creates file with expected content
# ---------------------------------------------------------------------------

class TestWriteComparisonReport:
    def _build_minimal_results(self):
        spy_short_trades = [
            {"signal_date": "2020-02-28", "entry_date": "2020-03-02",
             "exit_date": "2020-03-20", "bars_held": 14,
             "exit_reason": "time_stop", "return_pct": -5.0, "side": "short",
             "entry_price": 330.0, "exit_price": 320.0}
        ]
        comparison_trades = {
            name: [{
                "signal_date": "2020-02-28",
                "entry_date": "2020-03-02",
                "exit_date": "2020-03-20",
                "bars_held": 14,
                "exit_reason": "time_stop",
                "return_pct": 8.0,
                "spy_return_pct": -5.0,
                "entry_price": 50.0,
                "exit_price": 54.0,
            }]
            for name in cmp.INSTRUMENTS
        }
        comparison_trades["VXX"][0]["vxx_gross_decay_pct"] = 5.6
        comparison_trades["VXX"][0]["vxx_decay_adj_return_pct"] = 2.4
        episode_coverage = {
            ep: {"period": f"{s} – {e}", "entry_trigger_days": 0,
                 "candidate_days": 0, "entry_triggered": False, "trades_executed": 0}
            for ep, (s, e) in cmp.SHORT_EPISODES.items()
        }
        return {
            "spy_signal_log": [],
            "spy_short_trades": spy_short_trades,
            "comparison_trades": comparison_trades,
            "episode_coverage": episode_coverage,
        }

    def test_report_file_created(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cmp, "RESULTS_DIR", str(tmp_path))
        results = self._build_minimal_results()
        sr = cmp.compute_sector_rotation_spread(results["comparison_trades"])
        metrics = cmp.compute_comparison_metrics(results["comparison_trades"], sr)
        path = cmp.write_comparison_report(results, metrics, sr)
        assert os.path.exists(path), f"Report not created at {path}"

    def test_report_contains_key_sections(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cmp, "RESULTS_DIR", str(tmp_path))
        results = self._build_minimal_results()
        sr = cmp.compute_sector_rotation_spread(results["comparison_trades"])
        metrics = cmp.compute_comparison_metrics(results["comparison_trades"], sr)
        path = cmp.write_comparison_report(results, metrics, sr)
        with open(path) as f:
            content = f.read()
        for section in (
            "Baseline",
            "Instrument Comparison Results",
            "VIX / Volatility ETP Decay",
            "Episode Coverage",
            "Pitfalls Checklist",
            "Recommendations",
        ):
            assert section in content, f"Expected section {section!r} not found in report"

    def test_report_contains_vxx_decay_note(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cmp, "RESULTS_DIR", str(tmp_path))
        results = self._build_minimal_results()
        sr = cmp.compute_sector_rotation_spread(results["comparison_trades"])
        metrics = cmp.compute_comparison_metrics(results["comparison_trades"], sr)
        path = cmp.write_comparison_report(results, metrics, sr)
        with open(path) as f:
            content = f.read()
        assert "contango" in content.lower() or "decay" in content.lower()
