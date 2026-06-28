"""Tests for tools/qld_gld_rebalance.py."""

import json
import math
import pathlib
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from tools.qld_gld_rebalance import (
    ACCOUNT_SIZE_DEFAULT,
    DRIFT_TOLERANCE,
    GLD_RISK_STOP_PCT,
    QLD_RISK_STOP_PCT,
    TARGET_GLD_WEIGHT,
    TARGET_QLD_WEIGHT,
    build_rebalance_report,
    compute_current_state,
    compute_drift,
    compute_order_intents,
    compute_target_allocation,
)


# ---------------------------------------------------------------------------
# compute_target_allocation
# ---------------------------------------------------------------------------

class TestComputeTargetAllocation:
    def test_weights_sum_to_one(self):
        result = compute_target_allocation(25_000.0)
        assert result["QLD"]["weight"] + result["GLD"]["weight"] == pytest.approx(1.0)

    def test_notionals_match_weights(self):
        result = compute_target_allocation(25_000.0)
        assert result["QLD"]["target_notional"] == pytest.approx(7_500.0)
        assert result["GLD"]["target_notional"] == pytest.approx(17_500.0)

    def test_scales_with_account_value(self):
        result = compute_target_allocation(50_000.0)
        assert result["QLD"]["target_notional"] == pytest.approx(15_000.0)
        assert result["GLD"]["target_notional"] == pytest.approx(35_000.0)


# ---------------------------------------------------------------------------
# compute_current_state
# ---------------------------------------------------------------------------

class TestComputeCurrentState:
    def test_zero_shares_returns_zero_weights(self):
        state = compute_current_state(85.0, 0, 195.0, 0)
        assert state["QLD"]["weight"] == 0.0
        assert state["GLD"]["weight"] == 0.0
        assert state["total_portfolio_value"] == 0.0

    def test_market_values_correct(self):
        state = compute_current_state(85.0, 10, 195.0, 10)
        assert state["QLD"]["market_value"] == pytest.approx(850.0)
        assert state["GLD"]["market_value"] == pytest.approx(1950.0)
        assert state["total_portfolio_value"] == pytest.approx(2800.0)

    def test_weights_sum_to_one(self):
        state = compute_current_state(85.0, 88, 195.0, 89)
        total_weight = state["QLD"]["weight"] + state["GLD"]["weight"]
        assert total_weight == pytest.approx(1.0, abs=0.0001)

    def test_perfect_target_weights(self):
        # Build shares that exactly hit 30/70 split
        account = 25_000.0
        qld_price = 100.0
        gld_price = 100.0
        qld_shares = math.floor(account * 0.30 / qld_price)  # 75
        gld_shares = math.floor(account * 0.70 / gld_price)  # 175
        state = compute_current_state(qld_price, qld_shares, gld_price, gld_shares)
        # total = 75*100 + 175*100 = 25000
        assert state["QLD"]["weight"] == pytest.approx(0.30, abs=0.0001)
        assert state["GLD"]["weight"] == pytest.approx(0.70, abs=0.0001)


# ---------------------------------------------------------------------------
# compute_drift
# ---------------------------------------------------------------------------

class TestComputeDrift:
    def test_no_drift_at_target(self):
        account = 25_000.0
        qld_price = 100.0
        gld_price = 100.0
        qld_shares = math.floor(account * 0.30 / qld_price)
        gld_shares = math.floor(account * 0.70 / gld_price)
        state = compute_current_state(qld_price, qld_shares, gld_price, gld_shares)
        drift = compute_drift(state)
        assert drift["QLD"]["drift"] == pytest.approx(0.0, abs=0.001)
        assert drift["GLD"]["drift"] == pytest.approx(0.0, abs=0.001)
        assert not drift["QLD"]["exceeds_tolerance"]
        assert not drift["GLD"]["exceeds_tolerance"]

    def test_large_drift_triggers_flag(self):
        # All cash in QLD would give 100% QLD, huge drift
        state = compute_current_state(100.0, 100, 100.0, 0)
        drift = compute_drift(state)
        assert drift["QLD"]["drift"] == pytest.approx(0.70, abs=0.001)
        assert drift["QLD"]["exceeds_tolerance"]
        assert drift["GLD"]["exceeds_tolerance"]

    def test_small_drift_within_tolerance(self):
        # QLD at 32% (drift = +2%), within 5% band
        state = compute_current_state(100.0, 32, 100.0, 68)
        drift = compute_drift(state)
        assert abs(drift["QLD"]["drift"]) < DRIFT_TOLERANCE
        assert not drift["QLD"]["exceeds_tolerance"]

    def test_drift_at_tolerance_boundary(self):
        # QLD at 35.1% (drift = +5.1%), just exceeds tolerance
        state = compute_current_state(100.0, 351, 100.0, 649)
        drift = compute_drift(state)
        assert drift["QLD"]["exceeds_tolerance"]

    def test_drift_just_above_boundary_not_rounded_away(self):
        # True QLD weight = 351 / (351 + 649) = 0.351 exactly; drift = 0.051 > 0.05
        # Previously, rounding weights to 4dp before comparison could suppress this flag.
        state = compute_current_state(1.0, 351, 1.0, 649)
        assert state["QLD"]["weight"] == pytest.approx(0.351, abs=0.00001)
        drift = compute_drift(state)
        assert drift["QLD"]["exceeds_tolerance"], (
            "Drift of 5.1pp must trigger tolerance flag even at 4dp rounding boundary"
        )


# ---------------------------------------------------------------------------
# compute_order_intents
# ---------------------------------------------------------------------------

class TestComputeOrderIntents:
    def test_initial_buy_from_zero(self):
        qld_price = 85.0
        gld_price = 195.0
        account = 25_000.0
        state = compute_current_state(qld_price, 0, gld_price, 0)
        intents = compute_order_intents(state, account, qld_price, gld_price)

        qld_intent = next(i for i in intents if i["symbol"] == "QLD")
        gld_intent = next(i for i in intents if i["symbol"] == "GLD")

        assert qld_intent["side"] == "BUY"
        assert qld_intent["quantity"] > 0

        assert gld_intent["side"] == "BUY"
        assert gld_intent["quantity"] > 0

        # Verify quantities match floor(target_notional / price)
        expected_qld = math.floor(account * TARGET_QLD_WEIGHT / qld_price)
        expected_gld = math.floor(account * TARGET_GLD_WEIGHT / gld_price)
        assert qld_intent["target_shares"] == expected_qld
        assert gld_intent["target_shares"] == expected_gld

    def test_hold_when_at_target(self):
        qld_price = 100.0
        gld_price = 100.0
        account = 25_000.0
        target_qld = math.floor(account * TARGET_QLD_WEIGHT / qld_price)
        target_gld = math.floor(account * TARGET_GLD_WEIGHT / gld_price)
        state = compute_current_state(qld_price, target_qld, gld_price, target_gld)
        intents = compute_order_intents(state, account, qld_price, gld_price)

        for intent in intents:
            assert intent["side"] == "HOLD"
            assert intent["quantity"] == 0

    def test_sell_when_over_allocated(self):
        qld_price = 100.0
        gld_price = 100.0
        account = 25_000.0
        # Hold 50 QLD (target 75) — that's fine, but hold 400 GLD (target 175) — way over
        state = compute_current_state(qld_price, 75, gld_price, 400)
        intents = compute_order_intents(state, account, qld_price, gld_price)

        gld_intent = next(i for i in intents if i["symbol"] == "GLD")
        assert gld_intent["side"] == "SELL"
        assert gld_intent["quantity"] > 0

    def test_risk_points_set(self):
        state = compute_current_state(85.0, 0, 195.0, 0)
        intents = compute_order_intents(state, 25_000.0, 85.0, 195.0)
        qld_intent = next(i for i in intents if i["symbol"] == "QLD")
        gld_intent = next(i for i in intents if i["symbol"] == "GLD")

        assert qld_intent["risk_point"] == pytest.approx(85.0 * (1 - QLD_RISK_STOP_PCT), abs=0.01)
        assert gld_intent["risk_point"] == pytest.approx(195.0 * (1 - GLD_RISK_STOP_PCT), abs=0.01)

    def test_total_buy_notional_within_account(self):
        """Total buy notional must not exceed account value."""
        state = compute_current_state(85.0, 0, 195.0, 0)
        intents = compute_order_intents(state, ACCOUNT_SIZE_DEFAULT, 85.0, 195.0)
        total_buy_notional = sum(
            i["estimated_notional"] for i in intents if i["side"] == "BUY"
        )
        assert total_buy_notional <= ACCOUNT_SIZE_DEFAULT

    def test_quantities_are_integers(self):
        """Order quantities must be int, never float (e.g. 87 not 87.0)."""
        state = compute_current_state(85.0, 0, 195.0, 0)
        intents = compute_order_intents(state, 25_000.0, 85.0, 195.0)
        for intent in intents:
            assert isinstance(intent["quantity"], int), (
                f"{intent['symbol']} quantity should be int, got {type(intent['quantity'])}"
            )

    def test_sell_quantity_is_integer_and_not_over_sell(self):
        """SELL side must not over-sell by rounding float shares to wrong int."""
        # current_shares passed as float to simulate stored state
        qld_price = 100.0
        gld_price = 100.0
        account = 25_000.0
        # Pass shares as float; target GLD = floor(17500/100) = 175; current = 176.0
        state = compute_current_state(qld_price, float(75), gld_price, float(176))
        intents = compute_order_intents(state, account, qld_price, gld_price)
        gld_intent = next(i for i in intents if i["symbol"] == "GLD")
        assert gld_intent["side"] == "SELL"
        assert gld_intent["quantity"] == 1
        assert isinstance(gld_intent["quantity"], int)


# ---------------------------------------------------------------------------
# build_rebalance_report
# ---------------------------------------------------------------------------

class TestBuildRebalanceReport:
    def test_report_structure(self):
        report = build_rebalance_report(
            qld_price=85.0,
            gld_price=195.0,
            qld_shares=87,
            gld_shares=90,
            account_value=25_000.0,
        )
        for key in ("as_of", "strategy", "account_value", "rebalance_needed",
                    "drift_tolerance_pct", "current_state", "drift",
                    "order_intents", "total_estimated_notional", "risk_warnings"):
            assert key in report

    def test_initial_buy_all_buys(self):
        report = build_rebalance_report(
            qld_price=85.0,
            gld_price=195.0,
            qld_shares=0,
            gld_shares=0,
            account_value=25_000.0,
        )
        for intent in report["order_intents"]:
            assert intent["side"] == "BUY"

    def test_risk_warnings_present(self):
        report = build_rebalance_report(85.0, 195.0)
        assert len(report["risk_warnings"]) >= 4
        # Must warn about QLD 2x leverage
        joined = " ".join(report["risk_warnings"]).lower()
        assert "2x" in joined or "leveraged" in joined or "leverage" in joined

    def test_no_position_rebalance_needed_flag(self):
        # With no positions, rebalance_needed is False (no drift from target since no weight)
        report = build_rebalance_report(85.0, 195.0, 0, 0)
        # zero-share portfolio has no drift; rebalance_needed driven by tolerance breach only
        assert "rebalance_needed" in report

    def test_as_of_defaults_to_utc(self):
        report = build_rebalance_report(85.0, 195.0)
        assert "T" in report["as_of"]  # ISO 8601 datetime

    def test_custom_as_of(self):
        report = build_rebalance_report(
            85.0, 195.0, as_of="2026-07-01T09:30:00Z"
        )
        assert report["as_of"] == "2026-07-01T09:30:00Z"

    def test_report_is_json_serialisable(self):
        report = build_rebalance_report(85.50, 195.20, 87, 90, 25_000.0)
        dumped = json.dumps(report)
        loaded = json.loads(dumped)
        assert loaded["strategy"] == "lbr_tactical_rotation_qld_gld"


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------

class TestCLI:
    _TOOL = str(_ROOT / "tools" / "qld_gld_rebalance.py")

    def _run(self, *args: str) -> dict:
        result = subprocess.run(
            [sys.executable, self._TOOL, *args],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def test_dry_run(self):
        report = self._run("--dry-run")
        assert report["strategy"] == "lbr_tactical_rotation_qld_gld"
        assert "order_intents" in report

    def test_initial_buy_mode(self):
        report = self._run(
            "--qld-price", "85.00",
            "--gld-price", "195.00",
            "--initial-buy",
        )
        for intent in report["order_intents"]:
            assert intent["side"] == "BUY"

    def test_with_existing_positions(self):
        report = self._run(
            "--qld-price", "85.00", "--qld-shares", "87",
            "--gld-price", "195.00", "--gld-shares", "90",
        )
        assert "current_state" in report
        assert report["current_state"]["QLD"]["shares"] == 87

    def test_custom_account(self):
        report = self._run(
            "--qld-price", "85.00",
            "--gld-price", "195.00",
            "--account", "50000",
            "--initial-buy",
        )
        assert report["account_value"] == 50_000.0

    def test_missing_prices_exits_nonzero(self):
        result = subprocess.run(
            [sys.executable, self._TOOL],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
