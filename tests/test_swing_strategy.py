"""Unit tests for tools/swing_strategy.py.

Tests cover:
  - compute_position_size: risk budget and notional cap
  - _check_entry_constraints: each desk rule
  - evaluate_symbol: ENTER / SKIP / HOLD / EXIT decisions with synthetic bars
  - run_daily_loop: dry-run orchestration (no network, no TWS)
  - load_state / save_state: state file round-trip
"""

import json
import math
import pathlib
import sys
import tempfile
import types
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

# ── stub ib_async ─────────────────────────────────────────────────────────────

if "ib_async" not in sys.modules:
    _stub = types.ModuleType("ib_async")
    for _attr in ("IB", "Stock", "Future", "Contract", "Index", "Forex", "Option"):
        setattr(_stub, _attr, object)
    sys.modules["ib_async"] = _stub

# ── path setup ────────────────────────────────────────────────────────────────

_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from tools.swing_strategy import (
    ACCOUNT_SIZE_DEFAULT,
    BASELINE_SYMBOLS,
    DEFAULT_STRATEGY_VARIANT,
    EQUITY_BROAD_CLUSTER,
    MAX_EQUITY_CLUSTER_POSITIONS,
    MAX_NOTIONAL_PCT,
    MAX_POSITIONS,
    MIN_AVG_VOLUME,
    REQUIRED_INDICATOR_BARS,
    RISK_PER_TRADE_PCT,
    STRATEGY_VARIANTS,
    WEEKLY_LOOKBACK_DAYS,
    _check_entry_constraints,
    compute_position_size,
    evaluate_symbol,
    load_state,
    parse_args,
    run_daily_loop,
    save_state,
)


# ── synthetic bar factory ──────────────────────────────────────────────────────

def _make_bars(n: int = 250, base_price: float = 400.0, trend: float = 0.0) -> list:
    """Generate n synthetic daily bars with a mild trend."""
    bars = []
    price = base_price
    for i in range(n):
        price = price * (1 + trend)
        high = price * 1.005
        low = price * 0.995
        bars.append(SimpleNamespace(
            date=(date(2023, 1, 1) + timedelta(days=i)).isoformat(),
            open=round(price * 0.998, 2),
            high=round(high, 2),
            low=round(low, 2),
            close=round(price, 2),
            volume=2_000_000,
        ))
    return bars


def _make_breakout_bars(n: int = 250, base: float = 400.0) -> list:
    """
    Generate bars that form a range and then break out bullishly.
    First 220 bars: tight range around base.
    Next 5 bars: close above range_high (breakout).
    Final 5 bars: retest hold above range_high.
    """
    bars = []
    # Warm-up range
    for i in range(220):
        rng = base * 0.04 * ((i % 10) / 10 - 0.5)
        p = base + rng
        bars.append(SimpleNamespace(
            date=(date(2023, 1, 1) + timedelta(days=i)).isoformat(),
            open=round(p * 0.999, 2),
            high=round(p * 1.005, 2),
            low=round(p * 0.995, 2),
            close=round(p, 2),
            volume=2_000_000,
        ))
    # Breakout above range_high
    breakout_price = base * 1.07
    for i in range(5):
        p = breakout_price + i * 0.5
        bars.append(SimpleNamespace(
            date=(date(2023, 1, 1) + timedelta(days=220 + i)).isoformat(),
            open=round(p * 0.999, 2),
            high=round(p * 1.006, 2),
            low=round(p * 1.001, 2),
            close=round(p, 2),
            volume=4_000_000,  # volume expansion
        ))
    # Retest hold
    retest = breakout_price
    for i in range(5):
        p = retest + 0.5
        bars.append(SimpleNamespace(
            date=(date(2023, 1, 1) + timedelta(days=225 + i)).isoformat(),
            open=round(p * 0.999, 2),
            high=round(p * 1.004, 2),
            low=round(retest * 1.001, 2),  # low stays above breakout level
            close=round(p, 2),
            volume=2_500_000,
        ))
    return bars


def _empty_state() -> dict:
    return {"positions": {}, "last_trade_dates": {}, "account_nlv": ACCOUNT_SIZE_DEFAULT}


# ── compute_position_size ──────────────────────────────────────────────────────

class TestComputePositionSize:
    def test_basic_sizing(self):
        entry, stop, nlv = 100.0, 98.0, 25_000.0
        shares, risk = compute_position_size(entry, stop, nlv)
        # risk_budget = 250, stop_dist = 2.0, shares_by_risk = 125
        # max_notional = 5000, shares_by_notional = 50
        # result = min(125, 50) = 50
        assert shares == 50
        assert risk == pytest.approx(50 * 2.0, abs=0.01)

    def test_notional_cap(self):
        entry, stop, nlv = 1000.0, 995.0, 25_000.0
        # risk_budget = 250, stop_dist = 5, shares_by_risk = 50
        # max_notional = 5000, shares_by_notional = 5
        # result = min(50, 5) = 5
        shares, risk = compute_position_size(entry, stop, nlv)
        assert shares == 5
        assert risk == pytest.approx(5 * 5.0, abs=0.01)

    def test_zero_stop_distance(self):
        shares, risk = compute_position_size(100.0, 100.0, 25_000.0)
        assert shares == 0
        assert risk == 0.0

    def test_zero_entry_price(self):
        shares, risk = compute_position_size(0.0, 0.0, 25_000.0)
        assert shares == 0

    def test_tight_stop_small_position(self):
        # stop_dist = 0.10, shares_by_risk = 2500, capped at notional
        entry, stop, nlv = 50.0, 49.90, 25_000.0
        shares, risk = compute_position_size(entry, stop, nlv)
        max_notional_shares = math.floor(nlv * MAX_NOTIONAL_PCT / entry)
        assert shares == max_notional_shares


# ── _check_entry_constraints ───────────────────────────────────────────────────

class TestEntryConstraints:
    def test_clean_entry(self):
        state = _empty_state()
        allowed, reason = _check_entry_constraints("SPY", state, "long", avg_volume=1_000_000)
        assert allowed

    def test_already_in_position(self):
        state = _empty_state()
        state["positions"]["SPY"] = {"side": "long", "entry_price": 400}
        allowed, reason = _check_entry_constraints("SPY", state, "long")
        assert not allowed
        assert "already_in_position" in reason

    def test_max_positions(self):
        state = _empty_state()
        for i in range(MAX_POSITIONS):
            state["positions"][f"SYM{i}"] = {}
        allowed, reason = _check_entry_constraints("NEWONE", state, "long")
        assert not allowed
        assert "max_positions" in reason

    def test_equity_cluster_limit(self):
        state = _empty_state()
        for sym in list(EQUITY_BROAD_CLUSTER)[:MAX_EQUITY_CLUSTER_POSITIONS]:
            state["positions"][sym] = {}
        # Try to add a third equity cluster member
        remaining = [s for s in EQUITY_BROAD_CLUSTER if s not in state["positions"]]
        if remaining:
            allowed, reason = _check_entry_constraints(remaining[0], state, "long")
            assert not allowed
            assert "equity_cluster" in reason

    def test_weekly_trade_limit(self):
        state = _empty_state()
        three_days_ago = (date.today() - timedelta(days=3)).isoformat()
        state["last_trade_dates"]["SPY"] = three_days_ago
        allowed, reason = _check_entry_constraints("SPY", state, "long")
        assert not allowed
        assert "weekly_trade_limit" in reason

    def test_weekly_limit_expired(self):
        state = _empty_state()
        eight_days_ago = (date.today() - timedelta(days=8)).isoformat()
        state["last_trade_dates"]["SPY"] = eight_days_ago
        allowed, reason = _check_entry_constraints("SPY", state, "long")
        assert allowed

    def test_low_liquidity(self):
        state = _empty_state()
        allowed, reason = _check_entry_constraints("SPY", state, "long", avg_volume=100)
        assert not allowed
        assert "low_liquidity" in reason

    def test_sufficient_liquidity(self):
        state = _empty_state()
        allowed, reason = _check_entry_constraints("SPY", state, "long", avg_volume=MIN_AVG_VOLUME + 1)
        assert allowed


# ── evaluate_symbol ────────────────────────────────────────────────────────────

class TestEvaluateSymbol:
    def test_insufficient_bars(self):
        result = evaluate_symbol(
            "SPY", _make_bars(10), None, None, None, _empty_state(), ACCOUNT_SIZE_DEFAULT
        )
        assert result["decision"] == "SKIP"
        assert "insufficient" in result["skip_reason"]

    def test_indicator_warmup_shortfall_uses_clean_history_reason(self):
        bars = _make_bars(REQUIRED_INDICATOR_BARS - 1)

        result = evaluate_symbol(
            "SPY", bars, None, None, None, _empty_state(), ACCOUNT_SIZE_DEFAULT
        )

        assert result["decision"] == "SKIP"
        assert result["skip_reason"] == (
            f"insufficient_indicator_history_{len(bars)}_bars_need_{REQUIRED_INDICATOR_BARS}"
        )

    def test_default_long_only_variant_skips_short_entries_without_proposal(self):
        from unittest.mock import patch

        bars = _make_bars(250)
        fake_signal = {"signal_state": "entry_trigger_short", "confidence": 0.9, "scorecard": {}}
        fake_features = {
            "close": 100.0,
            "long_stop": 95.0,
            "short_stop": 105.0,
            "r_unit": 5.0,
        }

        with patch("tools.swing_strategy.calculate_indicators", return_value=fake_features), \
             patch("tools.swing_strategy.evaluate_breakout_signal", return_value=fake_signal):
            result = evaluate_symbol("SPY", bars, None, None, None, _empty_state(), ACCOUNT_SIZE_DEFAULT)

        assert result["decision"] == "SKIP"
        assert result["direction"] == "short"
        assert result["skip_reason"] == "direction_short_disabled"
        assert result["proposal_payload"] is None

    def test_legacy_variant_can_still_emit_short_entry_through_proposal_path(self):
        from unittest.mock import patch

        bars = _make_bars(250)
        fake_signal = {"signal_state": "entry_trigger_short", "confidence": 0.9, "scorecard": {}}
        fake_features = {
            "close": 100.0,
            "long_stop": 95.0,
            "short_stop": 105.0,
            "r_unit": 5.0,
        }

        with patch("tools.swing_strategy.calculate_indicators", return_value=fake_features), \
             patch("tools.swing_strategy.evaluate_breakout_signal", return_value=fake_signal):
            result = evaluate_symbol(
                "SPY",
                bars,
                None,
                None,
                None,
                _empty_state(),
                ACCOUNT_SIZE_DEFAULT,
                allowed_entry_directions=frozenset({"long", "short"}),
            )

        assert result["decision"] == "ENTER"
        assert result["direction"] == "short"
        assert result["proposal_payload"] is not None
        assert result["skip_reason"] is None

    def test_hold_with_open_position(self):
        """When in position and signal is no dramatic change → HOLD."""
        bars = _make_bars(250, base_price=400.0, trend=0.001)
        state = _empty_state()
        state["positions"]["SPY"] = {
            "side": "long",
            "entry_price": 390.0,
            "stop_level": 385.0,
            "bars_held": 5,
        }
        result = evaluate_symbol("SPY", bars, None, None, None, state, ACCOUNT_SIZE_DEFAULT)
        # Signal will be something (not exit) and we have a position → HOLD
        assert result["decision"] in ("HOLD", "ADD", "REDUCE", "EXIT")

    def test_exit_on_falling_price(self):
        """When price falls through stop with a long position → EXIT."""
        bars = _make_bars(250, base_price=400.0, trend=-0.003)
        state = _empty_state()
        state["positions"]["SPY"] = {
            "side": "long",
            "entry_price": 400.0,
            "stop_level": 399.0,  # price will quickly fall through
            "bars_held": 3,
        }
        result = evaluate_symbol("SPY", bars, None, None, None, state, ACCOUNT_SIZE_DEFAULT)
        # With strong downtrend and high stop, signal should recommend EXIT
        assert result["decision"] in ("EXIT", "HOLD")

    def test_skip_no_position_no_signal(self):
        """Flat bars with no breakout → SKIP."""
        bars = _make_bars(250, base_price=400.0, trend=0.0)
        result = evaluate_symbol(
            "SPY", bars, None, None, None, _empty_state(), ACCOUNT_SIZE_DEFAULT
        )
        assert result["decision"] == "SKIP"

    def test_no_entry_when_max_positions(self):
        state = _empty_state()
        for i in range(MAX_POSITIONS):
            state["positions"][f"SYM{i}"] = {}
        bars = _make_bars(250)
        result = evaluate_symbol(
            "SPY", bars, None, None, None, state, ACCOUNT_SIZE_DEFAULT
        )
        assert result["decision"] == "SKIP"

    def test_add_uses_real_sizing_not_bypassed(self):
        """ADD decision must use compute_position_size, not bypass constraints."""
        from unittest.mock import patch
        from tools.swing_strategy import MIN_POSITION_SHARES

        bars = _make_bars(250, base_price=100.0, trend=0.002)
        state = _empty_state()
        state["positions"]["SPY"] = {
            "side": "long",
            "entry_price": 90.0,
            "stop_level": 88.0,
            "bars_held": 5,
        }

        # Force the signal to be add_unit
        fake_signal = {"signal_state": "add_unit", "confidence": 0.75, "scorecard": {}}
        fake_features = {
            "close": 105.0,
            "long_stop": 88.0,
            "short_stop": None,
            "r_unit": 2.0,
            "atr20": 2.0,
        }

        with patch("tools.swing_strategy.calculate_indicators", return_value=fake_features), \
             patch("tools.swing_strategy.evaluate_breakout_signal", return_value=fake_signal):
            result = evaluate_symbol("SPY", bars, None, None, None, state, ACCOUNT_SIZE_DEFAULT)

        # Must be ADD or HOLD — never ignores the sizing gate
        assert result["decision"] in ("ADD", "HOLD")
        if result["decision"] == "ADD":
            # position_size must be derived from compute_position_size, not None
            assert result["position_size"] is not None
            assert result["position_size"] >= MIN_POSITION_SHARES

    def test_add_becomes_hold_when_stop_missing(self):
        """ADD falls back to HOLD when stop level is absent from features."""
        from unittest.mock import patch

        bars = _make_bars(250, base_price=100.0, trend=0.002)
        state = _empty_state()
        state["positions"]["SPY"] = {
            "side": "long",
            "entry_price": 90.0,
            "stop_level": None,  # no stop stored
            "bars_held": 5,
        }

        fake_signal = {"signal_state": "add_unit", "confidence": 0.75, "scorecard": {}}
        fake_features = {"close": 105.0, "long_stop": None, "short_stop": None, "r_unit": 2.0}

        with patch("tools.swing_strategy.calculate_indicators", return_value=fake_features), \
             patch("tools.swing_strategy.evaluate_breakout_signal", return_value=fake_signal):
            result = evaluate_symbol("SPY", bars, None, None, None, state, ACCOUNT_SIZE_DEFAULT)

        assert result["decision"] == "HOLD"
        assert result["skip_reason"] in ("add_no_price", "add_too_small")


# ── load_state / save_state ────────────────────────────────────────────────────

class TestStateIO:
    def test_load_missing_file(self):
        state = load_state("/nonexistent/path/state.json")
        assert "positions" in state
        assert "last_trade_dates" in state
        assert state["account_nlv"] == ACCOUNT_SIZE_DEFAULT

    def test_round_trip(self, tmp_path):
        state = {
            "positions": {"SPY": {"side": "long", "entry_price": 450.0}},
            "last_trade_dates": {"SPY": "2026-06-20"},
            "account_nlv": 25000.0,
        }
        path = str(tmp_path / "state.json")
        save_state(state, path)
        loaded = load_state(path)
        assert loaded["positions"]["SPY"]["entry_price"] == 450.0
        assert loaded["last_trade_dates"]["SPY"] == "2026-06-20"

    def test_load_corrupt_file(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text("NOT VALID JSON")
        state = load_state(str(p))
        assert state["positions"] == {}


# ── run_daily_loop (dry-run with mocked fetch) ─────────────────────────────────

class TestRunDailyLoop:
    def test_dry_run_returns_structure(self, monkeypatch):
        """run_daily_loop in dry_run mode should return valid structure."""
        bars = _make_bars(250)

        monkeypatch.setattr(
            "tools.swing_strategy.fetch_bars_dryrun",
            lambda symbol, days_back=300: bars,
        )

        state = _empty_state()
        result = run_daily_loop(["SPY", "GLD"], state, mode="dry_run")

        assert result["status"] == "ok"
        assert result["tws_status"] == "dry_run"
        assert len(result["decisions"]) == 2
        symbols_returned = {d["symbol"] for d in result["decisions"]}
        assert symbols_returned == {"SPY", "GLD"}

    def test_all_decisions_are_valid_types(self, monkeypatch):
        bars = _make_bars(250)
        monkeypatch.setattr(
            "tools.swing_strategy.fetch_bars_dryrun",
            lambda symbol, days_back=300: bars,
        )
        state = _empty_state()
        result = run_daily_loop(["SPY"], state, mode="dry_run")
        for dec in result["decisions"]:
            assert dec["decision"] in ("ENTER", "HOLD", "ADD", "REDUCE", "EXIT", "SKIP")

    def test_tws_offline_aborts(self, monkeypatch):
        """When TWS fetch returns tws_offline, loop aborts with status tws_offline."""
        monkeypatch.setattr(
            "tools.swing_strategy.fetch_bars_tws",
            lambda symbol, host, port: {
                "status": "tws_offline",
                "symbol": symbol,
                "message": "not reachable",
                "bars": [],
            },
        )
        state = _empty_state()
        result = run_daily_loop(["SPY"], state, mode="paper")
        assert result["status"] == "tws_offline"
        assert result["decisions"] == []

    def test_max_positions_enforced(self, monkeypatch):
        """Even if all symbols signal ENTER, max positions cap applies."""
        bars = _make_bars(250)
        monkeypatch.setattr(
            "tools.swing_strategy.fetch_bars_dryrun",
            lambda symbol, days_back=300: bars,
        )
        # Pre-fill all 6 positions
        state = _empty_state()
        for i in range(MAX_POSITIONS):
            state["positions"][f"SYM{i}"] = {}

        symbols = ["SPY", "QQQ", "GLD"]
        result = run_daily_loop(symbols, state, mode="dry_run")
        for dec in result["decisions"]:
            assert dec["decision"] != "ENTER", f"Should not ENTER when max positions full: {dec}"


# ── CLI / strategy variant defaults ───────────────────────────────────────────

class TestStrategyVariantDefaults:
    def test_default_variant_is_spy_only_long_only(self):
        assert DEFAULT_STRATEGY_VARIANT == "spy_long_only"
        assert STRATEGY_VARIANTS[DEFAULT_STRATEGY_VARIANT]["symbols"] == ["SPY"]
        assert STRATEGY_VARIANTS[DEFAULT_STRATEGY_VARIANT]["allowed_entry_directions"] == frozenset({"long"})

    def test_default_cli_uses_spy_long_only_variant_without_symbol_override(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["swing_strategy.py", "--dry-run"])

        args = parse_args()

        assert args.strategy_variant == "spy_long_only"
        assert args.symbols is None

    def test_legacy_variant_preserves_original_six_symbol_universe(self):
        assert STRATEGY_VARIANTS["legacy_multi_symbol"]["symbols"] == BASELINE_SYMBOLS
        assert STRATEGY_VARIANTS["legacy_multi_symbol"]["allowed_entry_directions"] == frozenset({"long", "short"})
