#!/usr/bin/env python3
"""
exit_model_comparison.py — Trade management comparison for PTJ Macro Breakout.

Evaluates three exit models side-by-side over the same 2015-2025 SPY backtest,
using the same signal engine as backtest_ptj_macro_breakout.py.

Exit models:
  1. baseline:    Fixed 20-bar time-stop, structural stop, 2.5R partial profit
  2. trailing:    Trail stop to lowest low of last 3 bars (for longs) once +1R in profit
  3. event_risk:  Halve size 1 bar before FOMC/CPI/NFP; exit if >1.5R adverse at event

Results written to results/exit_model_comparison.md.
"""

import os
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_BT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_BT_DIR))
sys.path.insert(0, _ROOT)
sys.path.insert(0, _BT_DIR)

if "ib_async" not in sys.modules:
    _stub = types.ModuleType("ib_async")
    for _attr in ("IB", "Stock", "Future", "Contract", "Index", "Forex", "Option"):
        setattr(_stub, _attr, object)
    sys.modules["ib_async"] = _stub

from tools.detect_macro_breakout import calculate_indicators, evaluate_breakout_signal
import backtest_ptj_macro_breakout as bt

RESULTS_DIR = os.path.join(_BT_DIR, "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

TRAILING_MAX_HOLD = 40   # safety time-stop for trailing model when trailing never activates
EVENT_ADVERSE_R = 1.5    # exit threshold for event-risk model

# ---------------------------------------------------------------------------
# Event date generation
# ---------------------------------------------------------------------------

# Approximate FOMC meeting end dates 2015-2025 (published in advance by the Fed)
_FOMC_DATES = [
    "2015-01-28", "2015-03-18", "2015-04-29", "2015-06-17",
    "2015-07-29", "2015-09-17", "2015-10-28", "2015-12-16",
    "2016-01-27", "2016-03-16", "2016-04-27", "2016-06-15",
    "2016-07-27", "2016-09-21", "2016-11-02", "2016-12-14",
    "2017-02-01", "2017-03-15", "2017-05-03", "2017-06-14",
    "2017-07-26", "2017-09-20", "2017-11-01", "2017-12-13",
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13",
    "2018-08-01", "2018-09-26", "2018-11-08", "2018-12-19",
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19",
    "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
    "2020-01-29", "2020-03-03", "2020-03-15", "2020-04-29",
    "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16",
    "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
    "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
    "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
]


def _generate_cpi_dates(start_year: int, end_year: int) -> list:
    """Approximate CPI release dates: around the 11th of each month (next business day if weekend)."""
    dates = []
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            d = datetime(year, month, 11)
            while d.weekday() >= 5:
                d += timedelta(days=1)
            dates.append(d.strftime("%Y-%m-%d"))
    return dates


def _generate_nfp_dates(start_year: int, end_year: int) -> list:
    """NFP: first Friday of each month."""
    dates = []
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            d = datetime(year, month, 1)
            while d.weekday() != 4:  # 4 = Friday
                d += timedelta(days=1)
            dates.append(d.strftime("%Y-%m-%d"))
    return dates


def build_event_date_set(start_year: int = 2015, end_year: int = 2025) -> set:
    """Build set of all major event dates (FOMC, CPI, NFP)."""
    events = set(_FOMC_DATES)
    events.update(_generate_cpi_dates(start_year, end_year))
    events.update(_generate_nfp_dates(start_year, end_year))
    return events


EVENT_DATES = build_event_date_set()


# ---------------------------------------------------------------------------
# Exit model implementations
# ---------------------------------------------------------------------------

def _baseline_exit(open_trade: dict, today, state: str, scorecard: dict, bars_held: int):
    """Baseline: structural stop + 20-bar time-stop (no trailing)."""
    close = today.close
    side = open_trade["side"]

    if state == "exit_signal":
        reason = scorecard.get("reason", "exit_signal")
        price = close * (1 - bt.SLIPPAGE) if side == "long" else close * (1 + bt.SLIPPAGE)
        return reason, price

    if side == "long":
        if close < open_trade["stop_level"]:
            return "stop_long", close * (1 - bt.SLIPPAGE)
        if bars_held >= bt.MAX_HOLD_BARS:
            return "time_stop", close * (1 - bt.SLIPPAGE)
    else:
        if close > open_trade["stop_level"]:
            return "stop_short", close * (1 + bt.SLIPPAGE)
        if bars_held >= bt.MAX_HOLD_BARS:
            return "time_stop", close * (1 + bt.SLIPPAGE)

    return None, None


def _trailing_exit(open_trade: dict, today, bar_history: list, state: str, scorecard: dict, bars_held: int):
    """
    Structure-based trailing exit:
    - Structural stop as absolute floor
    - Once in profit by 1R: trail stop to lowest low of last 3 bars (longs)
      / highest high of last 3 bars (shorts); never trail below breakeven
    - No fixed time-stop while trailing is active; 40-bar safety stop if never activated
    """
    close = today.close
    side = open_trade["side"]
    entry = open_trade["entry_price"]
    r_unit = open_trade["r_unit"]

    if state == "exit_signal":
        reason = scorecard.get("reason", "exit_signal")
        price = close * (1 - bt.SLIPPAGE) if side == "long" else close * (1 + bt.SLIPPAGE)
        return reason, price

    if side == "long":
        if (close - entry) >= r_unit and r_unit:
            recent = bar_history[-3:] if len(bar_history) >= 3 else bar_history
            if recent:
                trail = max(min(b.low for b in recent), entry)
                if trail > open_trade["stop_level"]:
                    open_trade["stop_level"] = round(trail, 4)
                    open_trade["trailing_active"] = True

        if close < open_trade["stop_level"]:
            reason = "trailing_stop_long" if open_trade.get("trailing_active") else "stop_long"
            return reason, close * (1 - bt.SLIPPAGE)

        if not open_trade.get("trailing_active") and bars_held >= TRAILING_MAX_HOLD:
            return "time_stop", close * (1 - bt.SLIPPAGE)

    else:  # short
        if (entry - close) >= r_unit and r_unit:
            recent = bar_history[-3:] if len(bar_history) >= 3 else bar_history
            if recent:
                trail = min(max(b.high for b in recent), entry)
                if trail < open_trade["stop_level"]:
                    open_trade["stop_level"] = round(trail, 4)
                    open_trade["trailing_active"] = True

        if close > open_trade["stop_level"]:
            reason = "trailing_stop_short" if open_trade.get("trailing_active") else "stop_short"
            return reason, close * (1 + bt.SLIPPAGE)

        if not open_trade.get("trailing_active") and bars_held >= TRAILING_MAX_HOLD:
            return "time_stop", close * (1 + bt.SLIPPAGE)

    return None, None


def _event_risk_exit(
    open_trade: dict,
    today,
    state: str,
    scorecard: dict,
    bars_held: int,
    tomorrow_date: Optional[str],
    event_dates: set,
):
    """
    Event-risk exit model:
    - Same baseline exits (structural stop + 20-bar time-stop)
    - On event day: exit if position is >1.5R adverse
    - 1 bar before event: halve position size (known event dates — no look-ahead of price)
    - Day after event: restore position size
    """
    close = today.close
    side = open_trade["side"]
    entry = open_trade["entry_price"]
    r_unit = open_trade["r_unit"]

    if state == "exit_signal":
        reason = scorecard.get("reason", "exit_signal")
        price = close * (1 - bt.SLIPPAGE) if side == "long" else close * (1 + bt.SLIPPAGE)
        return reason, price

    # Check adverse R on event day
    if today.date in event_dates and r_unit:
        adverse_r = (entry - close) / r_unit if side == "long" else (close - entry) / r_unit
        if adverse_r > EVENT_ADVERSE_R:
            price = close * (1 - bt.SLIPPAGE) if side == "long" else close * (1 + bt.SLIPPAGE)
            return "event_risk_exit", price

    # Structural stop
    if side == "long":
        if close < open_trade["stop_level"]:
            return "stop_long", close * (1 - bt.SLIPPAGE)
        if bars_held >= bt.MAX_HOLD_BARS:
            return "time_stop", close * (1 - bt.SLIPPAGE)
    else:
        if close > open_trade["stop_level"]:
            return "stop_short", close * (1 + bt.SLIPPAGE)
        if bars_held >= bt.MAX_HOLD_BARS:
            return "time_stop", close * (1 + bt.SLIPPAGE)

    # Pre-event position halving (FOMC/CPI/NFP dates are known in advance — not look-ahead)
    if tomorrow_date and tomorrow_date in event_dates and not open_trade.get("halved_for_event"):
        open_trade["pre_event_size"] = open_trade.get("current_size", bt.POSITION_SIZE)
        open_trade["halved_for_event"] = True
        open_trade["current_size"] = open_trade["pre_event_size"] * 0.5

    # Restore after event
    elif open_trade.get("halved_for_event"):
        today_is_event = today.date in event_dates
        if not today_is_event and (not tomorrow_date or tomorrow_date not in event_dates):
            open_trade["halved_for_event"] = False
            open_trade["current_size"] = open_trade.pop("pre_event_size", bt.POSITION_SIZE)

    return None, None


# ---------------------------------------------------------------------------
# Core backtest engine with pluggable exit model
# ---------------------------------------------------------------------------

def run_backtest_model(
    spy_bars: list,
    tlt_bars: list,
    uup_bars: list,
    hyg_bars: list,
    model: str = "baseline",
) -> dict:
    """
    Walk-forward backtest using the specified exit model.

    Entry logic is identical across all models; only position management differs.
    model: "baseline" | "trailing" | "event_risk"
    """
    n = len(spy_bars)
    signal_log: list = []
    trade_log: list = []
    forward_returns: list = []
    bar_history: list = []  # bars since entry (used by trailing model)

    def _align(bars, up_to_date):
        return [b for b in bars if b.date <= up_to_date]

    open_trade: Optional[dict] = None

    for d in range(bt.WARMUP_BARS, n):
        today = spy_bars[d]
        today_date = today.date
        primary = spy_bars[:d + 1]

        tlt_hist = _align(tlt_bars, today_date)
        uup_hist = _align(uup_bars, today_date)
        hyg_hist = _align(hyg_bars, today_date)

        pos_ctx = None
        if open_trade:
            pos_ctx = {
                "position_side": open_trade["side"],
                "entry_price": open_trade["entry_price"],
                "stop_level": open_trade["stop_level"],
                "bars_held": open_trade["bars_held"],
                "r_unit": open_trade["r_unit"],
            }

        features = calculate_indicators(primary, tlt_hist or None, uup_hist or None, hyg_hist or None)
        evaluation = evaluate_breakout_signal(features, pos_ctx)
        state = evaluation["signal_state"]
        confidence = evaluation["confidence"]
        scorecard = evaluation["scorecard"]

        signal_log.append({
            "date": today_date,
            "state": state,
            "confidence": round(confidence, 3),
            "long_confirms": scorecard.get("long_confirms", 0),
            "short_confirms": scorecard.get("short_confirms", 0),
            "close": round(today.close, 4),
            "in_position": open_trade is not None,
            "position_side": open_trade["side"] if open_trade else None,
        })

        # -------------------------------------------------------------------
        # Manage open position
        # -------------------------------------------------------------------
        if open_trade:
            open_trade["bars_held"] += 1
            bar_history.append(today)
            bars_held = open_trade["bars_held"]
            close = today.close
            side = open_trade["side"]
            entry = open_trade["entry_price"]
            r_unit = open_trade["r_unit"]
            partial_taken = open_trade.get("partial_taken", False)

            exit_reason = None
            exit_price = None

            if model == "baseline":
                exit_reason, exit_price = _baseline_exit(open_trade, today, state, scorecard, bars_held)
            elif model == "trailing":
                exit_reason, exit_price = _trailing_exit(
                    open_trade, today, bar_history, state, scorecard, bars_held
                )
            elif model == "event_risk":
                tomorrow_date = spy_bars[d + 1].date if d + 1 < n else None
                exit_reason, exit_price = _event_risk_exit(
                    open_trade, today, state, scorecard, bars_held, tomorrow_date, EVENT_DATES
                )

            # Partial profit at 2.5R (baseline and event_risk only; trailing uses stop-based exits)
            if exit_reason is None and model != "trailing":
                if side == "long" and not partial_taken and r_unit:
                    if (close - entry) >= r_unit * bt.PARTIAL_PROFIT_R:
                        open_trade["partial_taken"] = True
                        partial_ret = (close * (1 - bt.SLIPPAGE) - entry) / entry
                        trade_log.append(bt._trade_row(
                            open_trade, today_date, close * (1 - bt.SLIPPAGE),
                            "partial_profit_long", bars_held,
                            partial_ret, bt.POSITION_SIZE * bt.PARTIAL_FRACTION,
                        ))
                        open_trade["current_size"] = bt.POSITION_SIZE * (1 - bt.PARTIAL_FRACTION)
                        open_trade["stop_level"] = entry

                elif side == "short" and not partial_taken and r_unit:
                    if (entry - close) >= r_unit * bt.PARTIAL_PROFIT_R:
                        open_trade["partial_taken"] = True
                        partial_ret = (entry - close * (1 + bt.SLIPPAGE)) / entry
                        trade_log.append(bt._trade_row(
                            open_trade, today_date, close * (1 + bt.SLIPPAGE),
                            "partial_profit_short", bars_held,
                            partial_ret, bt.POSITION_SIZE * bt.PARTIAL_FRACTION,
                        ))
                        open_trade["current_size"] = bt.POSITION_SIZE * (1 - bt.PARTIAL_FRACTION)
                        open_trade["stop_level"] = entry

            if exit_reason and exit_price:
                size = open_trade.get("current_size", bt.POSITION_SIZE)
                ret = (exit_price - entry) / entry if side == "long" else (entry - exit_price) / entry
                trade_log.append(bt._trade_row(
                    open_trade, today_date, exit_price, exit_reason, bars_held, ret, size
                ))
                open_trade = None
                bar_history = []

        # -------------------------------------------------------------------
        # Check for new entry (identical across all models)
        # -------------------------------------------------------------------
        if open_trade is None and d + 1 < n:
            next_bar = spy_bars[d + 1]

            if state == "entry_trigger_long" and not features.get("gap_up_open"):
                entry_price = next_bar.open * (1 + bt.SLIPPAGE)
                stop_level = features.get("long_stop") or (
                    next_bar.open * (1 - (features.get("atr20") or entry_price * 0.01) / entry_price)
                )
                r_unit = features.get("r_unit") or features.get("atr20") or entry_price * 0.01
                open_trade = bt._new_trade("long", today_date, next_bar.date, entry_price, stop_level, r_unit)
                if model == "trailing":
                    open_trade["trailing_active"] = False
                elif model == "event_risk":
                    open_trade["halved_for_event"] = False
                bar_history = []
                bt._record_fwd_returns(forward_returns, spy_bars, d, today_date, today.close, "long")

            elif state == "entry_trigger_short" and not features.get("gap_down_open"):
                entry_price = next_bar.open * (1 - bt.SLIPPAGE)
                stop_level = features.get("short_stop") or (
                    next_bar.open * (1 + (features.get("atr20") or entry_price * 0.01) / entry_price)
                )
                r_unit = features.get("r_unit") or features.get("atr20") or entry_price * 0.01
                open_trade = bt._new_trade("short", today_date, next_bar.date, entry_price, stop_level, r_unit)
                if model == "trailing":
                    open_trade["trailing_active"] = False
                elif model == "event_risk":
                    open_trade["halved_for_event"] = False
                bar_history = []
                bt._record_fwd_returns(forward_returns, spy_bars, d, today_date, today.close, "short")

    # Close any still-open position at the last bar
    if open_trade and spy_bars:
        last = spy_bars[-1]
        side = open_trade["side"]
        size = open_trade.get("current_size", bt.POSITION_SIZE)
        if side == "long":
            exit_price = last.close * (1 - bt.SLIPPAGE)
            ret = (exit_price - open_trade["entry_price"]) / open_trade["entry_price"]
        else:
            exit_price = last.close * (1 + bt.SLIPPAGE)
            ret = (open_trade["entry_price"] - exit_price) / open_trade["entry_price"]
        trade_log.append(bt._trade_row(
            open_trade, last.date, exit_price, "end_of_data",
            open_trade["bars_held"], ret, size,
        ))

    return {"signal_log": signal_log, "trade_log": trade_log, "forward_returns": forward_returns}


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def _trade_stats_for(trades: list) -> dict:
    """Compute stats for a list of trade rows (full exits only)."""
    if not trades:
        return {}
    returns = np.array([t["return_pct"] for t in trades])
    stats = {
        "total_trades": len(trades),
        "win_rate_pct": round(float((returns > 0).mean() * 100), 1),
        "avg_return_pct": round(float(returns.mean()), 3),
        "median_return_pct": round(float(np.median(returns)), 3),
        "best_trade_pct": round(float(returns.max()), 3),
        "worst_trade_pct": round(float(returns.min()), 3),
        "avg_bars_held": round(float(np.mean([t["bars_held"] for t in trades])), 1),
    }
    if len(returns) > 1:
        per_bar = np.array([t["return_pct"] / max(t["bars_held"], 1) for t in trades])
        sharpe = float(per_bar.mean() / (per_bar.std() + 1e-9) * (252 ** 0.5))
        stats["sharpe_ratio"] = round(sharpe, 3)
    else:
        stats["sharpe_ratio"] = None
    equity = [1.0]
    for t in trades:
        prev = equity[-1]
        equity.append(prev * (1 + t["return_pct"] / 100 * t.get("position_size", bt.POSITION_SIZE)))
    eq_arr = np.array(equity)
    roll_max = np.maximum.accumulate(eq_arr)
    dd = (eq_arr - roll_max) / (roll_max + 1e-9)
    stats["max_drawdown_pct"] = round(float(dd.min() * 100), 3)
    return stats


def compute_model_metrics(results: dict) -> dict:
    """Compute per-model metrics with IS/OOS split."""
    trade_log = results["trade_log"]

    def is_full(t):
        return "partial_profit" not in t["exit_reason"]

    long_trades = [t for t in trade_log if t["side"] == "long" and is_full(t)]
    short_trades = [t for t in trade_log if t["side"] == "short" and is_full(t)]
    all_trades = long_trades + short_trades

    is_cutoff = "2019-12-31"
    return {
        "all": _trade_stats_for(all_trades),
        "long": _trade_stats_for(long_trades),
        "short": _trade_stats_for(short_trades),
        "in_sample_long": _trade_stats_for([t for t in long_trades if t.get("exit_date", "") <= is_cutoff]),
        "out_of_sample_long": _trade_stats_for([t for t in long_trades if t.get("exit_date", "") > is_cutoff]),
        "in_sample_short": _trade_stats_for([t for t in short_trades if t.get("exit_date", "") <= is_cutoff]),
        "out_of_sample_short": _trade_stats_for([t for t in short_trades if t.get("exit_date", "") > is_cutoff]),
    }


def compare_exit_models(
    spy_bars: list,
    tlt_bars: list,
    uup_bars: list,
    hyg_bars: list,
) -> dict:
    """Run all three exit models and return a dict of model → metrics."""
    comparison = {}
    for model in ("baseline", "trailing", "event_risk"):
        print(f"  Running exit model: {model}...")
        results = run_backtest_model(spy_bars, tlt_bars, uup_bars, hyg_bars, model=model)
        metrics = compute_model_metrics(results)
        comparison[model] = {"results": results, "metrics": metrics}
        n_trades = sum(1 for t in results["trade_log"] if "partial_profit" not in t["exit_reason"])
        print(f"    → {n_trades} full trades completed")
    return comparison


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def write_comparison_report(comparison: dict) -> str:
    """Write exit_model_comparison.md; return path."""
    models = ("baseline", "trailing", "event_risk")
    model_labels = {
        "baseline": "Baseline (20-bar stop + 2.5R partial)",
        "trailing": "Trailing (3-bar low trail from +1R)",
        "event_risk": "Event-Risk (halve before FOMC/CPI/NFP)",
    }

    def _v(m, key, fmt=None):
        v = m.get(key)
        if v is None:
            return "—"
        return f"{v:{fmt}}" if fmt else str(v)

    lines = [
        "# Exit Model Comparison: PTJ Macro Breakout",
        "",
        f"**Run date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Backtest period:** {bt.START_DATE} – {bt.END_DATE}  "
        "(walk-forward, entry at next bar open)",
        "**Signal engine:** identical across all models (same entries, different exits)",
        "",
        "## Exit Models",
        "",
        "| Model | Description |",
        "|-------|-------------|",
        "| **Baseline** | Fixed 20-bar time-stop, structural stop, 2.5R partial profit (1/3 off → stop to breakeven) |",
        "| **Trailing** | Trail stop to lowest low of last 3 bars (for longs) once +1R in profit; 40-bar safety stop if trailing never activates |",
        "| **Event-Risk** | Same as baseline + halve size 1 bar before FOMC/CPI/NFP; exit if >1.5R adverse on event day |",
        "",
        "---",
        "",
        "## Side-by-Side: All Trades (Combined Long + Short)",
        "",
        "| Metric | Baseline | Trailing | Event-Risk |",
        "|--------|----------|----------|------------|",
    ]

    def _row(label, key, fmt=None):
        vals = [_v(comparison[m]["metrics"]["all"], key, fmt) for m in models]
        return f"| {label} | {' | '.join(vals)} |"

    lines += [
        _row("Total trades", "total_trades"),
        _row("Win rate", "win_rate_pct", ".1f"),
        _row("Avg return %", "avg_return_pct", "+.3f"),
        _row("Median return %", "median_return_pct", "+.3f"),
        _row("Best trade %", "best_trade_pct", "+.3f"),
        _row("Worst trade %", "worst_trade_pct", "+.3f"),
        _row("Avg bars held", "avg_bars_held", ".1f"),
        _row("Sharpe (annualised)", "sharpe_ratio", ".3f"),
        _row("Max drawdown %", "max_drawdown_pct", ".3f"),
        "",
    ]

    for side in ("long", "short"):
        side_label = "Long Trades" if side == "long" else "Short Trades"
        lines += [
            "---",
            "",
            f"## {side_label}",
            "",
            "| Metric | Baseline | Trailing | Event-Risk |",
            "|--------|----------|----------|------------|",
        ]

        def _row_side(label, key, fmt=None, s=side):
            vals = [_v(comparison[m]["metrics"][s], key, fmt) for m in models]
            return f"| {label} | {' | '.join(vals)} |"

        lines += [
            _row_side("Total trades", "total_trades"),
            _row_side("Win rate", "win_rate_pct", ".1f"),
            _row_side("Avg return %", "avg_return_pct", "+.3f"),
            _row_side("Avg bars held", "avg_bars_held", ".1f"),
            _row_side("Sharpe (annualised)", "sharpe_ratio", ".3f"),
            _row_side("Max drawdown %", "max_drawdown_pct", ".3f"),
            "",
        ]

    lines += [
        "---",
        "",
        "## In-Sample (2015–2019) vs Out-of-Sample (2020–2025)",
        "",
    ]

    for period_key, period_label in [("in_sample", "2015–2019 (IS)"), ("out_of_sample", "2020–2025 (OOS)")]:
        for side in ("long", "short"):
            mk = f"{period_key}_{side}"
            lines += [
                f"### {period_label} — {side.capitalize()}",
                "",
                "| Metric | Baseline | Trailing | Event-Risk |",
                "|--------|----------|----------|------------|",
            ]

            def _row_is(label, key, fmt=None, k=mk):
                vals = [_v(comparison[m]["metrics"][k], key, fmt) for m in models]
                return f"| {label} | {' | '.join(vals)} |"

            lines += [
                _row_is("Total trades", "total_trades"),
                _row_is("Win rate", "win_rate_pct", ".1f"),
                _row_is("Avg return %", "avg_return_pct", "+.3f"),
                _row_is("Avg bars held", "avg_bars_held", ".1f"),
                _row_is("Sharpe", "sharpe_ratio", ".3f"),
                _row_is("Max drawdown %", "max_drawdown_pct", ".3f"),
                "",
            ]

    lines += [
        "---",
        "",
        "## Exit Reason Breakdown",
        "",
    ]
    for model in models:
        trade_log = comparison[model]["results"]["trade_log"]
        reason_counts: dict = {}
        for t in trade_log:
            r = t["exit_reason"]
            reason_counts[r] = reason_counts.get(r, 0) + 1
        lines += [
            f"### {model_labels[model]}",
            "",
            "| Exit Reason | Count |",
            "|-------------|-------|",
        ]
        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
            lines.append(f"| `{reason}` | {count} |")
        lines.append("")

    lines += [
        "---",
        "",
        "## Methodology Notes",
        "",
        "- **Signal engine:** same `calculate_indicators` / `evaluate_breakout_signal` across all models",
        "- **Entry logic:** identical — entry at next bar open after trigger; gap exclusion applied",
        "- **Slippage:** 0.05% each way on all entries and exits",
        "- **Trailing model:** 3-bar structural trail activates once position reaches +1R profit; ",
        "  stop never trails below entry (breakeven floor); 40-bar safety stop if trailing never activates",
        "- **Event-risk model:** FOMC/CPI/NFP dates are known in advance (pre-announced); ",
        "  position halved the bar before the event, restored after; exits if >1.5R adverse on event day",
        "- **IS/OOS cutoff:** 2019-12-31",
        "",
    ]

    path = os.path.join(RESULTS_DIR, "exit_model_comparison.md")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  → {path}")
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("PTJ Macro Breakout — Exit Model Comparison")
    print(f"Period: {bt.START_DATE} – {bt.END_DATE}")
    print("=" * 60)

    print(f"\nEvent dates loaded: {len(EVENT_DATES)} (FOMC + CPI + NFP)")

    print("\n[1/3] Downloading historical data...")
    spy_df = bt._yf_fetch("SPY", bt.START_DATE, bt.END_DATE)
    print(f"  SPY: {len(spy_df)} bars")
    time.sleep(0.5)
    tlt_df = bt._yf_fetch("TLT", bt.START_DATE, bt.END_DATE)
    print(f"  TLT: {len(tlt_df)} bars")
    time.sleep(0.5)
    uup_df = bt._yf_fetch("UUP", bt.START_DATE, bt.END_DATE)
    print(f"  UUP: {len(uup_df)} bars")
    time.sleep(0.5)
    hyg_df = bt._yf_fetch("HYG", bt.START_DATE, bt.END_DATE)
    print(f"  HYG: {len(hyg_df)} bars")

    spy_bars = bt._df_to_bars(spy_df)
    tlt_bars = bt._df_to_bars(tlt_df)
    uup_bars = bt._df_to_bars(uup_df)
    hyg_bars = bt._df_to_bars(hyg_df)

    print(f"\n[2/3] Running exit model comparison ({len(spy_bars)} SPY bars)...")
    comparison = compare_exit_models(spy_bars, tlt_bars, uup_bars, hyg_bars)

    print("\n[3/3] Writing comparison report...")
    report_path = write_comparison_report(comparison)

    # Terminal summary
    print("\n" + "=" * 60)
    print("SUMMARY — All Trades Combined")
    print("=" * 60)
    print(f"\n{'Metric':<30} {'Baseline':>12} {'Trailing':>12} {'Event-Risk':>12}")
    print("-" * 70)
    for label, key, fmt in [
        ("Total trades", "total_trades", "d"),
        ("Win rate %", "win_rate_pct", ".1f"),
        ("Avg return %", "avg_return_pct", "+.3f"),
        ("Avg bars held", "avg_bars_held", ".1f"),
        ("Sharpe", "sharpe_ratio", ".3f"),
        ("Max drawdown %", "max_drawdown_pct", ".3f"),
    ]:
        vals = []
        for m in ("baseline", "trailing", "event_risk"):
            v = comparison[m]["metrics"]["all"].get(key)
            vals.append(f"{v:{fmt}}" if v is not None else "N/A")
        print(f"{label:<30} {vals[0]:>12} {vals[1]:>12} {vals[2]:>12}")

    print(f"\nFull report: {report_path}")
    print("\nDone.")


if __name__ == "__main__":
    main()
