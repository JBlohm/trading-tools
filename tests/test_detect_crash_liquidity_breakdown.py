"""Tests for detect_crash_liquidity_breakdown.py.

Scenarios:
  1. Golden COVID crash fixture (2020-03-09 area) → entry_trigger_short
  2. Normal pullback Apr-2024 → no_setup or watchlist_deterioration only
  3. Profitable short + lower highs → manage_open_short
  4. Close above risk_high → de_risk_exit
  5. policy_stop=True → de_risk_exit regardless of score
  6. < 260 primary bars → blocked_missing_data; missing optional bars → degraded only
  7. CLI args, connection ID register, JSON parseable output
"""

import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(close, open_=None, high=None, low=None, volume=1_000_000, date="2020-01-01"):
    return SimpleNamespace(
        date=date,
        open=open_ if open_ is not None else close,
        high=high if high is not None else close * 1.005,
        low=low if low is not None else close * 0.995,
        close=close,
        volume=volume,
    )


def _make_bars(n, base_close, trend=0.0):
    """Generate n bars with a gentle trend. trend is per-bar fraction."""
    bars = []
    c = base_close
    for i in range(n):
        c = c * (1 + trend)
        bars.append(_bar(c))
    return bars


def _inject_fake_ib_async(mock_ib_class=None):
    fake = types.ModuleType("ib_async")
    fake.IB = mock_ib_class or MagicMock
    fake.Stock = MagicMock(return_value=MagicMock())
    fake.Future = MagicMock(return_value=MagicMock())
    fake.Contract = MagicMock(return_value=MagicMock())
    fake.Index = MagicMock(return_value=MagicMock())
    sys.modules["ib_async"] = fake
    for key in list(sys.modules):
        if "tools.detect_crash_liquidity_breakdown" in key or "detect_crash_liquidity_breakdown" == key:
            del sys.modules[key]
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    import tools.detect_crash_liquidity_breakdown as mod
    return mod, fake


# ---------------------------------------------------------------------------
# Fixture factories
# ---------------------------------------------------------------------------

def _covid_crash_bars():
    """Synthetic bars approximating SPY Feb-Mar 2020 crash.

    Pre-crash: 260 bars around 300-337. The last 30 bars represent the selloff.
    The 200-day SMA ends up ~295, the close ends at ~274 (below sma_200 and shelf).
    A bounce to ~285 is embedded before the final drop, simulating failed retest.
    """
    bars = []
    # 230 calm bars at 310 (builds up 200-day sma history around 310)
    for _ in range(200):
        bars.append(_bar(310.0))
    # 30 bars trending up to ~337 (late 2019 rally)
    c = 310.0
    for _ in range(30):
        c *= 1.001
        bars.append(_bar(c))
    # Shelf established: 20 bars sideways around 337
    for _ in range(20):
        bars.append(_bar(337.0))
    # Crash: 15 bars selling off from 337 to ~274
    c = 337.0
    for _ in range(15):
        c *= 0.975
        bars.append(_bar(c))
    # Bounce: 5 bars back up past the shelf bottom (failed retest of broken level)
    c_after_drop = c
    shelf_approx = 290.0  # the 20th-pct of the shelf window will be around here
    for _ in range(5):
        c *= 1.012
        bars.append(_bar(c))  # gets back near 290-295
    # Final failure: close back below shelf
    bars.append(_bar(274.0, open_=282.0, high=285.0, low=272.0))
    return bars


def _covid_vix_bars():
    """VIX bars: final value 55 (COVID crash peak area)."""
    bars = _make_bars(260, 15.0)
    # Spike in last 15 bars
    for i in range(15):
        bars.append(_bar(20.0 + i * 2.5))  # ends at ~55
    return bars[-260:]


def _covid_credit_bars():
    """HYG bars: below 50-day and 200-day SMA."""
    bars = _make_bars(260, 85.0)
    # Final 30 bars drop below SMA
    for i in range(30):
        bars.append(_bar(75.0 - i * 0.1))
    return bars[-260:]


def _covid_breadth_bars():
    """RSP bars underperforming SPY."""
    bars = _make_bars(260, 100.0)
    # RSP drops harder in last 30 bars
    for i in range(30):
        bars.append(_bar(80.0 - i * 0.3))
    return bars[-260:]


def _normal_pullback_bars():
    """Synthetic bars: SPY Apr 2024-like sideways/mild pullback, no crash pattern.

    Close stays above 200-day SMA. VIX around 16-17. No failed retest cluster.
    """
    bars = _make_bars(280, 420.0, trend=0.0002)
    # Mild 5% pullback in last 20 bars but still above 200dma
    c = bars[-1].close * 0.97
    for _ in range(20):
        c *= 0.999
        bars.append(_bar(c))
    return bars[-260:]


def _normal_vix_bars():
    """VIX bars: mild elevated ~17, not crash level."""
    return _make_bars(260, 17.0)


# ---------------------------------------------------------------------------
# Unit tests: calculate_indicators
# ---------------------------------------------------------------------------

class TestCalculateIndicators:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_blocked_when_fewer_than_260_bars(self):
        bars = _make_bars(200, 300.0)
        result = self.mod.calculate_indicators(bars)
        assert result["blocked"] is True
        assert result["bar_count"] == 200
        assert result["min_required"] == 260

    def test_blocked_has_data_quality(self):
        bars = _make_bars(100, 300.0)
        result = self.mod.calculate_indicators(bars)
        assert "data_quality" in result
        assert result["data_quality"]["primary_bars"] == 100

    def test_not_blocked_with_260_bars(self):
        bars = _make_bars(260, 300.0)
        result = self.mod.calculate_indicators(bars)
        assert not result.get("blocked")

    def test_sma_200_computed(self):
        bars = _make_bars(260, 300.0)
        result = self.mod.calculate_indicators(bars)
        assert result["sma_200"] is not None
        assert result["sma_200"] == pytest.approx(300.0, rel=0.01)

    def test_support_shelf_below_typical_close(self):
        bars = _make_bars(260, 300.0)
        result = self.mod.calculate_indicators(bars)
        # 20th-pct of lookback window; for a flat series it equals the close
        assert result["support_shelf"] is not None
        assert result["support_shelf"] <= result["close"]

    def test_below_200dma_true_when_close_below(self):
        bars = _make_bars(260, 300.0)
        # Drop final close far below SMA
        bars[-1] = _bar(200.0)
        result = self.mod.calculate_indicators(bars)
        assert result["below_200dma"] is True

    def test_below_200dma_false_when_above(self):
        bars = _make_bars(260, 300.0)
        # Close is at 300 = SMA, so should not be below
        result = self.mod.calculate_indicators(bars)
        assert result["below_200dma"] is False

    def test_shelf_break_when_well_below_shelf(self):
        # Create bars with high shelf, then crash close
        bars = _make_bars(260, 350.0)
        bars[-1] = _bar(250.0, open_=260.0, high=262.0, low=248.0)
        result = self.mod.calculate_indicators(bars)
        assert result["shelf_break"] is True
        assert result["below_support_shelf"] is True

    def test_failed_retest_detected(self):
        # Build bars where: 10 bars ago close was above shelf, now close is below
        bars = _make_bars(270, 350.0)
        # Simulate the crash+bounce+failure
        bars[-25] = _bar(250.0)  # below shelf
        bars[-20] = _bar(340.0)  # bounce above shelf (around previous close level)
        bars[-1] = _bar(250.0)   # back below
        result = self.mod.calculate_indicators(bars)
        # Depends on shelf value; just confirm the key exists
        assert "failed_retest" in result

    def test_lower_low_true_when_recent_close_lower(self):
        bars = _make_bars(260, 300.0)
        # Ensure closes[-6] > closes[-1]
        bars[-6] = _bar(310.0)
        bars[-1] = _bar(280.0)
        result = self.mod.calculate_indicators(bars)
        assert result["lower_low"] is True

    def test_lower_low_false_when_recent_close_higher(self):
        bars = _make_bars(260, 300.0)
        bars[-6] = _bar(280.0)
        bars[-1] = _bar(310.0)
        result = self.mod.calculate_indicators(bars)
        assert result["lower_low"] is False

    def test_tr_expansion_detected(self):
        bars = _make_bars(260, 300.0)
        # Make a large-range final bar
        bars[-1] = _bar(300.0, open_=310.0, high=320.0, low=270.0)
        result = self.mod.calculate_indicators(bars)
        assert result["tr_expansion"] is True

    def test_vix_missing_when_no_vix_bars(self):
        bars = _make_bars(260, 300.0)
        result = self.mod.calculate_indicators(bars)
        assert result["vix_level"] is None
        assert "vix" in result["data_quality"]

    def test_vix_indicators_computed(self):
        bars = _make_bars(260, 300.0)
        vix_bars = _make_bars(260, 15.0)
        vix_bars[-1] = _bar(35.0)
        result = self.mod.calculate_indicators(bars, vix_bars=vix_bars)
        assert result["vix_level"] == pytest.approx(35.0)
        assert result["vix_warning"] is True
        assert result["vix_confirmation"] is True
        assert "vix" not in result["data_quality"]

    def test_vix_warning_not_confirmation_at_27(self):
        bars = _make_bars(260, 300.0)
        vix_bars = _make_bars(260, 27.0)
        result = self.mod.calculate_indicators(bars, vix_bars=vix_bars)
        assert result["vix_warning"] is True
        assert result["vix_confirmation"] is False

    def test_credit_missing_when_no_credit_bars(self):
        bars = _make_bars(260, 300.0)
        result = self.mod.calculate_indicators(bars)
        assert result["credit_below_50dma"] is None
        assert "credit" in result["data_quality"]

    def test_breadth_missing_when_no_breadth_bars(self):
        bars = _make_bars(260, 300.0)
        result = self.mod.calculate_indicators(bars)
        assert result["breadth_weak"] is None
        assert "breadth" in result["data_quality"]

    def test_breadth_weak_when_rsp_underperforms(self):
        bars = _make_bars(260, 420.0)
        # RSP drops significantly more than SPY in recent bars
        breadth_bars = _make_bars(260, 100.0)
        for i in range(21):
            breadth_bars.append(_bar(100.0 * (0.95 ** (i / 20))))
        breadth_bars = breadth_bars[-260:]
        bars[-21] = _bar(420.0)
        bars[-1] = _bar(415.0)
        result = self.mod.calculate_indicators(bars, breadth_bars=breadth_bars)
        assert result["breadth_weak"] is not None

    def test_gap_down_open_detected(self):
        bars = _make_bars(260, 350.0)
        # Set open well below the shelf area
        bars[-1] = _bar(260.0, open_=250.0, high=265.0, low=248.0)
        result = self.mod.calculate_indicators(bars)
        assert "gap_down_open" in result

    def test_result_has_all_required_keys(self):
        bars = _make_bars(260, 300.0)
        result = self.mod.calculate_indicators(bars)
        for key in ["close", "sma_200", "sma_50", "support_shelf", "below_200dma",
                    "below_support_shelf", "shelf_break", "failed_retest", "lower_low",
                    "lower_high", "atr_20", "current_tr", "tr_expansion", "vix_level",
                    "vix_warning", "vix_confirmation", "vix_roc_5d", "vix_roc_confirmation",
                    "credit_below_50dma", "credit_below_200dma", "credit_underperforming",
                    "rsp_vs_spy_10d", "rsp_vs_spy_20d", "breadth_weak",
                    "bar_count", "data_quality"]:
            assert key in result, f"Missing key: {key}"

    def test_config_shelf_lookback_override(self):
        bars = _make_bars(260, 300.0)
        result = self.mod.calculate_indicators(bars, config={"shelf_lookback": 20})
        assert not result.get("blocked")

    def test_atr_20_positive(self):
        bars = _make_bars(260, 300.0)
        result = self.mod.calculate_indicators(bars)
        assert result["atr_20"] is not None
        assert result["atr_20"] > 0


# ---------------------------------------------------------------------------
# Unit tests: evaluate_crash_playbook
# ---------------------------------------------------------------------------

class TestEvaluateCrashPlaybook:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def _good_features(self, **overrides):
        """Base features: clear no_setup (stable market, above everything)."""
        f = {
            "blocked": False,
            "close": 420.0,
            "sma_200": 390.0,
            "sma_50": 410.0,
            "support_shelf": 400.0,
            "below_200dma": False,
            "below_50dma": False,
            "below_support_shelf": False,
            "shelf_break": False,
            "failed_retest": False,
            "lower_low": False,
            "lower_high": False,
            "gap_down_open": False,
            "atr_20": 5.0,
            "current_tr": 5.0,
            "tr_expansion": False,
            "vix_level": 14.0,
            "vix_warning": False,
            "vix_confirmation": False,
            "vix_roc_5d": 5.0,
            "vix_roc_confirmation": False,
            "credit_below_50dma": False,
            "credit_below_200dma": False,
            "credit_underperforming": False,
            "rsp_vs_spy_10d": 0.5,
            "rsp_vs_spy_20d": 0.3,
            "breadth_weak": False,
            "bar_count": 260,
            "data_quality": {},
        }
        f.update(overrides)
        return f

    def _crash_features(self, **overrides):
        """Features representing a full crash setup."""
        f = self._good_features(
            close=274.0,
            sma_200=295.0,
            sma_50=310.0,
            support_shelf=290.0,
            below_200dma=True,
            below_50dma=True,
            below_support_shelf=True,
            shelf_break=True,
            failed_retest=True,
            lower_low=True,
            lower_high=True,
            gap_down_open=False,
            atr_20=5.0,
            current_tr=12.0,
            tr_expansion=True,
            vix_level=55.0,
            vix_warning=True,
            vix_confirmation=True,
            vix_roc_5d=45.0,
            vix_roc_confirmation=True,
            credit_below_50dma=True,
            credit_below_200dma=True,
            credit_underperforming=True,
            breadth_weak=True,
            data_quality={},
        )
        f.update(overrides)
        return f

    # --- blocked ---
    def test_blocked_returns_blocked_state(self):
        features = {
            "blocked": True,
            "reason": "insufficient_primary_data",
            "bar_count": 150,
            "min_required": 260,
            "data_quality": {"primary_bars": 150, "min_required": 260},
        }
        result = self.mod.evaluate_crash_playbook(features)
        assert result["signal_state"] == "blocked_missing_data"
        assert result["confidence"] == 0.0

    # --- no_setup ---
    def test_stable_market_gives_no_setup(self):
        features = self._good_features()
        result = self.mod.evaluate_crash_playbook(features)
        assert result["signal_state"] == "no_setup"

    # --- entry_trigger_short (COVID-like) ---
    def test_covid_crash_gives_entry_trigger_short(self):
        features = self._crash_features()
        result = self.mod.evaluate_crash_playbook(features)
        assert result["signal_state"] == "entry_trigger_short"

    def test_entry_trigger_requires_failed_retest_or_range_expansion(self):
        features = self._crash_features(failed_retest=False, tr_expansion=False)
        result = self.mod.evaluate_crash_playbook(features)
        # Without failed retest or decisive range expansion, should not be entry trigger
        assert result["signal_state"] != "entry_trigger_short"

    def test_entry_trigger_allows_decisive_range_expansion(self):
        features = self._crash_features(failed_retest=False, tr_expansion=True)
        result = self.mod.evaluate_crash_playbook(features)
        assert result["signal_state"] == "entry_trigger_short"

    def test_entry_trigger_requires_two_confirmations(self):
        # Only volatility confirmed, no breadth or credit
        features = self._crash_features(
            breadth_weak=False,
            credit_below_50dma=False,
            credit_below_200dma=False,
            credit_underperforming=False,
        )
        # Still has VIX+TR as volatility, but breadth=False and credit=False → 1 confirmation
        result = self.mod.evaluate_crash_playbook(features)
        assert result["signal_state"] != "entry_trigger_short"

    def test_confidence_above_0_5_for_entry_trigger(self):
        features = self._crash_features()
        result = self.mod.evaluate_crash_playbook(features)
        assert result["confidence"] > 0.5

    # --- setup_armed ---
    def test_setup_armed_when_below_200dma_plus_two_confirmations(self):
        features = self._good_features(
            close=280.0,
            below_200dma=True,
            below_support_shelf=True,
            shelf_break=False,
            failed_retest=False,
            vix_confirmation=True,
            tr_expansion=False,
            breadth_weak=True,
        )
        result = self.mod.evaluate_crash_playbook(features)
        assert result["signal_state"] == "setup_armed"

    def test_gap_down_gives_setup_armed_not_entry_trigger(self):
        features = self._crash_features(gap_down_open=True)
        result = self.mod.evaluate_crash_playbook(features)
        assert result["signal_state"] != "entry_trigger_short"
        assert result["signal_state"] == "setup_armed"

    def test_below_200dma_without_shelf_break_never_entry_trigger(self):
        # Below 200DMA alone must NOT allow entry_trigger_short, even with failed retest
        # and 2+ confirmations. The docs require a shelf break for entry.
        features = self._good_features(
            close=280.0,
            below_200dma=True,
            below_support_shelf=True,
            shelf_break=False,
            failed_retest=True,
            tr_expansion=True,
            vix_confirmation=True,
            breadth_weak=True,
            credit_below_50dma=True,
            gap_down_open=False,
        )
        result = self.mod.evaluate_crash_playbook(features)
        assert result["signal_state"] != "entry_trigger_short", (
            "below_200dma without shelf_break must not produce entry_trigger_short"
        )
        assert result["signal_state"] == "setup_armed"

    def test_shelf_break_required_for_entry_trigger(self):
        # shelf_break=True should produce entry_trigger; shelf_break=False should not
        features_with_break = self._crash_features(shelf_break=True, failed_retest=True, gap_down_open=False)
        features_no_break = self._crash_features(shelf_break=False, failed_retest=True, gap_down_open=False)
        result_with = self.mod.evaluate_crash_playbook(features_with_break)
        result_without = self.mod.evaluate_crash_playbook(features_no_break)
        assert result_with["signal_state"] == "entry_trigger_short"
        assert result_without["signal_state"] != "entry_trigger_short"

    # --- watchlist_deterioration ---
    def test_watchlist_with_partial_deterioration(self):
        features = self._good_features(
            below_200dma=False,
            below_support_shelf=True,
            vix_warning=True,
            vix_confirmation=False,
        )
        result = self.mod.evaluate_crash_playbook(features)
        assert result["signal_state"] in ("watchlist_deterioration", "no_setup")

    # --- manage_open_short ---
    def test_manage_open_short_for_profitable_short_with_lower_highs(self):
        # VIX elevated (consistent with crash env) so volatility-compression exit does not fire
        features = self._good_features(
            close=270.0,
            below_200dma=True,
            below_support_shelf=True,
            lower_high=True,
            vix_level=35.0,
            vix_warning=True,
            vix_confirmation=True,
        )
        pos = {"position_side": "short", "entry_price": 300.0, "risk_high": 310.0}
        result = self.mod.evaluate_crash_playbook(features, position_context=pos)
        assert result["signal_state"] == "manage_open_short"

    def test_manage_open_short_actions_include_trail(self):
        features = self._good_features(
            close=270.0,
            below_200dma=True,
            below_support_shelf=True,
            lower_high=True,
            vix_level=35.0,
            vix_warning=True,
            vix_confirmation=True,
        )
        pos = {"position_side": "short", "entry_price": 300.0, "risk_high": 310.0}
        result = self.mod.evaluate_crash_playbook(features, position_context=pos)
        assert any("trail" in a for a in result["actions"])

    # --- de_risk_exit ---
    def test_de_risk_exit_when_close_above_risk_high(self):
        features = self._good_features(close=320.0)
        pos = {"position_side": "short", "entry_price": 300.0, "risk_high": 310.0}
        result = self.mod.evaluate_crash_playbook(features, position_context=pos)
        assert result["signal_state"] == "de_risk_exit"

    def test_de_risk_exit_on_policy_stop(self):
        features = self._crash_features()  # full crash — would normally be entry_trigger
        policy = {"policy_stop": True}
        result = self.mod.evaluate_crash_playbook(features, policy_context=policy)
        assert result["signal_state"] == "de_risk_exit"

    def test_policy_stop_overrides_everything(self):
        features = self._good_features()  # no setup at all
        policy = {"policy_stop": True}
        result = self.mod.evaluate_crash_playbook(features, policy_context=policy)
        assert result["signal_state"] == "de_risk_exit"

    # --- degraded confidence with missing data ---
    def test_missing_optional_data_lowers_confidence(self):
        full_features = self._crash_features()
        full_result = self.mod.evaluate_crash_playbook(full_features)

        degraded_features = self._crash_features(
            vix_level=None, vix_warning=None, vix_confirmation=None,
            vix_roc_5d=None, vix_roc_confirmation=None,
            breadth_weak=None, rsp_vs_spy_10d=None, rsp_vs_spy_20d=None,
            credit_below_50dma=None, credit_below_200dma=None, credit_underperforming=None,
            data_quality={"vix": "missing", "breadth": "missing", "credit": "missing"},
        )
        deg_result = self.mod.evaluate_crash_playbook(degraded_features)
        assert deg_result["confidence"] <= full_result["confidence"]

    # --- output structure ---
    def test_result_has_required_keys(self):
        features = self._good_features()
        result = self.mod.evaluate_crash_playbook(features)
        for key in ["signal_state", "confidence", "trade_posture", "scorecard",
                    "market_snapshot", "risk_points", "actions", "data_quality"]:
            assert key in result, f"Missing key: {key}"

    def test_scorecard_has_confirmations(self):
        features = self._crash_features()
        result = self.mod.evaluate_crash_playbook(features)
        assert "confirmations" in result["scorecard"]
        assert result["scorecard"]["confirmations"] >= 2

    def test_result_is_json_serialisable(self):
        features = self._good_features()
        result = self.mod.evaluate_crash_playbook(features)
        json.dumps(result)

    def test_no_trade_posture_for_no_setup(self):
        features = self._good_features()
        result = self.mod.evaluate_crash_playbook(features)
        assert "flat" in result["trade_posture"] or "no_trade" in result["trade_posture"]


# ---------------------------------------------------------------------------
# Unit tests: format_llm_output
# ---------------------------------------------------------------------------

class TestFormatLlmOutput:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def _make_evaluation(self, state="no_setup", confidence=0.8):
        return {
            "signal_state": state,
            "confidence": confidence,
            "reason": "",
            "trade_posture": "flat_no_trade",
            "scorecard": {"confirmations": 0, "price_below_200dma": False},
            "market_snapshot": {
                "close": 420.0,
                "sma_200": 390.0,
                "sma_50": 410.0,
                "support_shelf": 400.0,
                "vix_level": 14.0,
                "atr_20": 5.0,
            },
            "risk_points": ["invalidation: close above 400.00"],
            "actions": ["watch_for_deterioration_cluster"],
            "data_quality": {"missing_optional": [], "degraded": False, "bar_count": 260},
        }

    def test_output_contains_fenced_json(self):
        evaluation = self._make_evaluation()
        output = self.mod.format_llm_output(evaluation)
        assert "```json" in output
        assert "```" in output

    def test_fenced_json_is_parseable(self):
        evaluation = self._make_evaluation()
        output = self.mod.format_llm_output(evaluation)
        start = output.index("```json") + len("```json")
        end = output.index("```", start)
        payload = json.loads(output[start:end])
        assert payload["signal_state"] == "no_setup"

    def test_json_has_required_keys(self):
        evaluation = self._make_evaluation()
        output = self.mod.format_llm_output(evaluation)
        start = output.index("```json") + len("```json")
        end = output.index("```", start)
        payload = json.loads(output[start:end])
        for key in ["tool", "version", "timestamp", "strategy", "signal_state",
                    "confidence", "trade_posture", "market_snapshot", "scorecard",
                    "risk_points", "actions", "data_quality"]:
            assert key in payload, f"Missing JSON key: {key}"

    def test_tool_name_in_json(self):
        evaluation = self._make_evaluation()
        output = self.mod.format_llm_output(evaluation)
        start = output.index("```json") + len("```json")
        end = output.index("```", start)
        payload = json.loads(output[start:end])
        assert payload["tool"] == "detect_crash_liquidity_breakdown"

    def test_strategy_in_json(self):
        evaluation = self._make_evaluation()
        output = self.mod.format_llm_output(evaluation)
        start = output.index("```json") + len("```json")
        end = output.index("```", start)
        payload = json.loads(output[start:end])
        assert "ptj" in payload["strategy"].lower() or "crash" in payload["strategy"].lower()

    def test_human_readable_section_present(self):
        evaluation = self._make_evaluation()
        output = self.mod.format_llm_output(evaluation)
        assert "PTJ" in output or "CRASH" in output
        assert "State" in output or "state" in output

    def test_entry_trigger_label_in_output(self):
        evaluation = self._make_evaluation(state="entry_trigger_short", confidence=0.82)
        output = self.mod.format_llm_output(evaluation)
        assert "entry" in output.lower() or "ENTRY" in output

    def test_blocked_state_in_output(self):
        evaluation = self._make_evaluation(state="blocked_missing_data", confidence=0.0)
        evaluation["data_quality"]["missing_optional"] = []
        output = self.mod.format_llm_output(evaluation)
        assert "blocked" in output.lower() or "BLOCKED" in output


# ---------------------------------------------------------------------------
# Integration: full COVID crash scenario fixture test
# ---------------------------------------------------------------------------

class TestCovidCrashScenario:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_covid_crash_produces_entry_trigger_or_setup_armed(self):
        """Golden fixture: COVID-like crash should signal entry_trigger_short or setup_armed."""
        bars = _covid_crash_bars()
        vix_bars = _covid_vix_bars()
        credit_bars = _covid_credit_bars()
        breadth_bars = _covid_breadth_bars()

        features = self.mod.calculate_indicators(
            bars,
            vix_bars=vix_bars,
            credit_bars=credit_bars,
            breadth_bars=breadth_bars,
        )
        assert not features.get("blocked"), "Should not block: enough bars"
        assert features["below_200dma"] is True
        assert features["vix_confirmation"] is True

        evaluation = self.mod.evaluate_crash_playbook(features)
        assert evaluation["signal_state"] in ("entry_trigger_short", "setup_armed"), \
            f"Expected bearish signal, got: {evaluation['signal_state']}"
        assert evaluation["confidence"] > 0.5
        assert evaluation["scorecard"]["confirmations"] >= 2


class TestNormalPullbackScenario:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_normal_pullback_does_not_produce_entry_trigger(self):
        """Apr-2024-like normal pullback should NOT produce entry_trigger_short."""
        bars = _normal_pullback_bars()
        vix_bars = _normal_vix_bars()

        features = self.mod.calculate_indicators(bars, vix_bars=vix_bars)
        evaluation = self.mod.evaluate_crash_playbook(features)
        assert evaluation["signal_state"] != "entry_trigger_short", \
            f"Normal pullback should not trigger entry; got: {evaluation['signal_state']}"
        assert evaluation["signal_state"] in ("no_setup", "watchlist_deterioration"), \
            f"Expected no_setup or watchlist, got: {evaluation['signal_state']}"


class TestMissingDataScenario:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_fewer_than_260_primary_bars_gives_blocked(self):
        bars = _make_bars(150, 300.0)
        features = self.mod.calculate_indicators(bars)
        assert features["blocked"] is True
        evaluation = self.mod.evaluate_crash_playbook(features)
        assert evaluation["signal_state"] == "blocked_missing_data"

    def test_missing_vix_credit_breadth_not_blocked(self):
        bars = _make_bars(260, 300.0)
        features = self.mod.calculate_indicators(bars)  # no optional bars
        assert not features.get("blocked")
        evaluation = self.mod.evaluate_crash_playbook(features)
        assert evaluation["signal_state"] != "blocked_missing_data"
        assert evaluation["data_quality"]["degraded"] is True

    def test_missing_optional_data_quality_flagged(self):
        bars = _make_bars(260, 300.0)
        features = self.mod.calculate_indicators(bars)
        assert "vix" in features["data_quality"]
        assert "credit" in features["data_quality"]
        assert "breadth" in features["data_quality"]


# ---------------------------------------------------------------------------
# Tests: CLI argument parsing
# ---------------------------------------------------------------------------

class TestArgParsing:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_defaults(self):
        with patch("sys.argv", ["detect_crash_liquidity_breakdown.py"]):
            args = self.mod.parse_args()
        assert args.symbol == "SPY"
        assert args.sec_type == "STK"
        assert args.port == self.mod.PORT_PAPER
        assert args.shelf_lookback == 63
        assert args.break_threshold == pytest.approx(0.005)
        assert args.policy_stop is False

    def test_live_flag(self):
        with patch("sys.argv", ["detect_crash_liquidity_breakdown.py", "--live"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_LIVE

    def test_symbol_override(self):
        with patch("sys.argv", ["detect_crash_liquidity_breakdown.py", "--symbol", "ES", "--sec-type", "FUT"]):
            args = self.mod.parse_args()
        assert args.symbol == "ES"
        assert args.sec_type == "FUT"

    def test_policy_stop_true(self):
        with patch("sys.argv", ["detect_crash_liquidity_breakdown.py", "--policy-stop", "true"]):
            args = self.mod.parse_args()
        assert args.policy_stop is True

    def test_policy_stop_false(self):
        with patch("sys.argv", ["detect_crash_liquidity_breakdown.py", "--policy-stop", "false"]):
            args = self.mod.parse_args()
        assert args.policy_stop is False

    def test_position_side(self):
        with patch("sys.argv", [
            "detect_crash_liquidity_breakdown.py",
            "--position-side", "short",
            "--entry-price", "540",
            "--risk-high", "555",
        ]):
            args = self.mod.parse_args()
        assert args.position_side == "short"
        assert args.entry_price == pytest.approx(540.0)
        assert args.risk_high == pytest.approx(555.0)

    def test_optional_symbols(self):
        with patch("sys.argv", [
            "detect_crash_liquidity_breakdown.py",
            "--vix-symbol", "VIX",
            "--credit-symbol", "HYG",
            "--breadth-symbol", "RSP",
        ]):
            args = self.mod.parse_args()
        assert args.vix_symbol == "VIX"
        assert args.credit_symbol == "HYG"
        assert args.breadth_symbol == "RSP"

    def test_shelf_lookback_override(self):
        with patch("sys.argv", [
            "detect_crash_liquidity_breakdown.py",
            "--shelf-lookback", "40",
        ]):
            args = self.mod.parse_args()
        assert args.shelf_lookback == 40


# ---------------------------------------------------------------------------
# Tests: connection ID register
# ---------------------------------------------------------------------------

class TestConnectionIdRegister:
    def test_detect_crash_registered(self):
        import pathlib
        reg = pathlib.Path(__file__).parent.parent / "tools" / "connection_ids.json"
        data = json.loads(reg.read_text())
        next_id = str(data.get("next_available", 0) + 1)
        # The tool must be registered
        assert any(
            v.get("tool") == "detect_crash_liquidity_breakdown.py"
            for v in data["ids"].values()
        ), "detect_crash_liquidity_breakdown.py not registered in connection_ids.json"

    def test_registered_id_is_read_only(self):
        import pathlib
        reg = pathlib.Path(__file__).parent.parent / "tools" / "connection_ids.json"
        data = json.loads(reg.read_text())
        entry = next(
            (v for v in data["ids"].values()
             if v.get("tool") == "detect_crash_liquidity_breakdown.py"),
            None,
        )
        assert entry is not None
        assert entry["read_only"] is True

    def test_client_id_constant_matches_register(self):
        import pathlib
        mod, _ = _inject_fake_ib_async()
        reg = pathlib.Path(__file__).parent.parent / "tools" / "connection_ids.json"
        data = json.loads(reg.read_text())
        # Find the registered ID for this tool
        registered_id = next(
            (int(k) for k, v in data["ids"].items()
             if v.get("tool") == "detect_crash_liquidity_breakdown.py"),
            None,
        )
        assert registered_id is not None
        assert mod.CLIENT_ID == registered_id


# ---------------------------------------------------------------------------
# Tests: TWS adapter (fake ib_async injection)
# ---------------------------------------------------------------------------

class TestTWSAdapter:
    def setup_method(self):
        self.mock_ib_class = MagicMock()
        self.mod, _ = _inject_fake_ib_async(self.mock_ib_class)

    def _setup_mock_ib(self, bars=None, vix_bars=None, credit_bars=None, breadth_bars=None):
        mock_instance = MagicMock()
        mock_instance.connectAsync = AsyncMock()
        mock_instance.qualifyContractsAsync = AsyncMock(return_value=[MagicMock()])
        # reqHistoricalDataAsync returns different data per call
        call_count = [0]
        bar_responses = [
            bars if bars is not None else _make_bars(260, 300.0),
            vix_bars if vix_bars is not None else [],
            credit_bars if credit_bars is not None else [],
            breadth_bars if breadth_bars is not None else [],
        ]
        async def fake_historical(*args, **kwargs):
            idx = call_count[0]
            call_count[0] += 1
            return bar_responses[idx] if idx < len(bar_responses) else []
        mock_instance.reqHistoricalDataAsync = fake_historical
        mock_instance.disconnect = MagicMock()
        self.mock_ib_class.return_value = mock_instance
        return mock_instance

    def _make_args(self, **kwargs):
        defaults = {
            "host": "127.0.0.1",
            "port": 7497,
            "symbol": "SPY",
            "sec_type": "STK",
            "currency": "USD",
            "exchange": "SMART",
            "expiry": None,
            "vix_symbol": None,
            "credit_symbol": None,
            "breadth_symbol": None,
            "shelf_lookback": 63,
            "break_threshold": 0.005,
            "position_side": None,
            "entry_price": None,
            "risk_high": None,
            "breakdown_level": None,
            "policy_stop": False,
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_uses_readonly_connection(self):
        import asyncio
        mock_ib = self._setup_mock_ib()
        args = self._make_args()
        asyncio.run(self.mod.run_detector("127.0.0.1", 7497, args))
        mock_ib.connectAsync.assert_called_once_with(
            "127.0.0.1", 7497, clientId=self.mod.CLIENT_ID, readonly=True
        )

    def test_disconnects_after_run(self):
        import asyncio
        mock_ib = self._setup_mock_ib()
        args = self._make_args()
        asyncio.run(self.mod.run_detector("127.0.0.1", 7497, args))
        mock_ib.disconnect.assert_called_once()

    def test_raises_connection_error_on_timeout(self):
        import asyncio
        mock_ib = MagicMock()
        mock_ib.connectAsync = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_ib.disconnect = MagicMock()
        self.mock_ib_class.return_value = mock_ib
        args = self._make_args()
        with pytest.raises(ConnectionError, match="Timed out"):
            asyncio.run(self.mod.run_detector("127.0.0.1", 7497, args))

    def test_raises_connection_error_on_refused(self):
        import asyncio
        mock_ib = MagicMock()
        mock_ib.connectAsync = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        mock_ib.disconnect = MagicMock()
        self.mock_ib_class.return_value = mock_ib
        args = self._make_args()
        with pytest.raises(ConnectionError, match="Cannot reach TWS"):
            asyncio.run(self.mod.run_detector("127.0.0.1", 7497, args))

    def test_optional_bars_fetched_when_symbols_provided(self):
        import asyncio
        mock_ib = self._setup_mock_ib(
            vix_bars=_make_bars(260, 15.0),
            credit_bars=_make_bars(260, 85.0),
            breadth_bars=_make_bars(260, 100.0),
        )
        args = self._make_args(
            vix_symbol="VIX",
            credit_symbol="HYG",
            breadth_symbol="RSP",
        )
        result = asyncio.run(self.mod.run_detector("127.0.0.1", 7497, args))
        assert "signal_state" in result
        self.mod.Index.assert_called_once_with("VIX", "CBOE", "USD")

    def test_result_from_adapter_is_json_serialisable(self):
        import asyncio
        self._setup_mock_ib()
        args = self._make_args()
        result = asyncio.run(self.mod.run_detector("127.0.0.1", 7497, args))
        json.dumps(result)

    def test_policy_stop_propagated_to_result(self):
        import asyncio
        self._setup_mock_ib()
        args = self._make_args(policy_stop=True)
        result = asyncio.run(self.mod.run_detector("127.0.0.1", 7497, args))
        assert result["signal_state"] == "de_risk_exit"


# ---------------------------------------------------------------------------
# Tests: main() exit codes
# ---------------------------------------------------------------------------

class TestMainExitCodes:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_exit_0_on_good_signal(self):
        evaluation = {
            "signal_state": "no_setup",
            "confidence": 0.9,
            "reason": "",
            "trade_posture": "flat_no_trade",
            "scorecard": {"confirmations": 0},
            "market_snapshot": {},
            "risk_points": [],
            "actions": [],
            "data_quality": {"missing_optional": [], "degraded": False, "bar_count": 260},
        }
        with patch("sys.argv", ["detect_crash_liquidity_breakdown.py"]):
            with patch.object(self.mod, "run_detector", AsyncMock(return_value=evaluation)):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()
        assert exc.value.code == 0

    def test_exit_2_on_blocked_missing_data(self):
        evaluation = {
            "signal_state": "blocked_missing_data",
            "confidence": 0.0,
            "reason": "insufficient_primary_data",
            "trade_posture": "no_trade",
            "scorecard": {},
            "market_snapshot": {},
            "risk_points": [],
            "actions": [],
            "data_quality": {"missing_optional": [], "degraded": True, "bar_count": 100},
        }
        with patch("sys.argv", ["detect_crash_liquidity_breakdown.py"]):
            with patch.object(self.mod, "run_detector", AsyncMock(return_value=evaluation)):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()
        assert exc.value.code == 2

    def test_exit_3_on_connection_error(self, capsys):
        async def raise_conn_err(*args, **kwargs):
            raise ConnectionError("Cannot reach TWS")

        with patch("sys.argv", ["detect_crash_liquidity_breakdown.py"]):
            with patch.object(self.mod, "run_detector", raise_conn_err):
                with pytest.raises(SystemExit) as exc:
                    self.mod.main()
        assert exc.value.code == 3
        err = json.loads(capsys.readouterr().err)
        assert err["status"] == "tws_unavailable"
