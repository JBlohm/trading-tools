#!/usr/bin/env python3
"""
backtest_short_instruments_comparison.py — Comparative backtest of PTJ Macro
Breakout short signals across alternative execution instruments.

Motivation (TRA-63): SPY short signals from detect_macro_breakout.py underperform
due to the long-term equity bull bias (27.3% win rate, -0.593% avg return). This
module tests whether the SAME short signals, when applied to better-suited instruments,
produce better risk-adjusted outcomes.

Signal source
-------------
detect_macro_breakout.py with SPY as primary_bars (unchanged from TRA-61 backtest).
All entry and exit TIMING is determined on SPY. Alternative instrument returns are
computed over the same holding periods.

Instruments evaluated
---------------------
  SH          ProShares Short S&P500 ETF (long execution = short SPY exposure)
  TLT_short   20Y Treasury ETF short (rates thesis: rising yields → TLT falls)
  HYG_short   High-Yield Bond ETF short (credit spread widening in risk-off)
  VXX         VIX Short-Term Futures ETP (long vol, decay documented below)
  XLU         Utilities ETF (defensive long in risk-off regime)
  XLV         Health Care ETF (defensive long in risk-off regime)
  XLY_short   Consumer Discretionary ETF short (cyclical underperforms in risk-off)
  Sector_Rotation   Spread: long (XLU + XLV)/2, short XLY

VXX / Volatility ETP Decay Adjustment
---------------------------------------
VIX ETP products (VXX, UVXY) suffer structural contango decay from the continuous
roll of front-month VIX futures. This daily roll cost averages ~0.3–0.6% per day in
normal environments, roughly 10% per month.

Accounting method used here:
  - Historical VXX adjusted-close prices already embed the full realised roll cost.
    No manual adjustment is applied; the observed return is the net-of-decay return.
  - For informational comparison, DECAY_PER_BAR (0.004, ≈ 0.4%/day) is noted in the
    report alongside raw and decay-adjusted returns so readers can calibrate expected
    alpha vs. the carry cost.
  - UVXY (2x leveraged) has double the decay rate; it is excluded from the backtest
    because the leverage also distorts the signal payoff ratio.

The MAX_HOLD_BARS = 20 cap limits gross decay exposure to ~8% (20 × 0.4%) per trade,
making VXX viable only when a macro breakdown materialises quickly.

Outputs (all in ./results/)
  short_instruments_comparison.md   Summary report with rankings
"""

import csv
import os
import sys
import time
import types
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional

import numpy as np
import pandas as pd

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

import backtest_ptj_macro_breakout as bt

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SLIPPAGE = bt.SLIPPAGE         # 0.05% each way (consistent with SPY backtest)
MAX_HOLD_BARS = bt.MAX_HOLD_BARS  # 20-bar time stop
START_DATE = bt.START_DATE     # "2015-01-01"
END_DATE = bt.END_DATE         # "2025-06-01"
POSITION_SIZE = bt.POSITION_SIZE  # 0.33 units

VXX_DECAY_PER_BAR = 0.004  # ~0.4%/day structural contango decay on VXX (informational)

RESULTS_DIR = os.path.join(_BT_DIR, "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Instrument configuration
# ---------------------------------------------------------------------------

INSTRUMENTS: dict = {
    "SH": {
        "ticker": "SH",
        "direction": "long",
        "type": "inverse_etf",
        "description": "ProShares Short S&P500 (long SH = short SPY exposure, no decay)",
    },
    "TLT_short": {
        "ticker": "TLT",
        "direction": "short",
        "type": "rates",
        "description": "20Y Treasury ETF short (profits when yields rise / risk-off rate move)",
    },
    "HYG_short": {
        "ticker": "HYG",
        "direction": "short",
        "type": "credit",
        "description": "High-Yield Bond ETF short (profits from credit spread widening)",
    },
    "VXX": {
        "ticker": "VXX",
        "direction": "long",
        "type": "volatility",
        "description": "VIX Short-Term Futures ETP long (decay-adjusted; see module docstring)",
    },
    "XLU": {
        "ticker": "XLU",
        "direction": "long",
        "type": "sector_defensive",
        "description": "Utilities ETF long (defensive sector outperforms in risk-off)",
    },
    "XLV": {
        "ticker": "XLV",
        "direction": "long",
        "type": "sector_defensive",
        "description": "Health Care ETF long (defensive sector outperforms in risk-off)",
    },
    "XLY_short": {
        "ticker": "XLY",
        "direction": "short",
        "type": "sector_cyclical",
        "description": "Consumer Discretionary ETF short (cyclical underperforms in risk-off)",
    },
}

# Episodes to check for coverage
SHORT_EPISODES = {
    "2018-Q4 Selloff": ("2018-10-01", "2018-12-24"),
    "2020-Q1 COVID": ("2020-02-19", "2020-03-23"),
    "2022-Q1 Bear Onset": ("2022-01-04", "2022-04-30"),
}

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _nearest_bar(bars: list, date_str: str, max_offset_days: int = 5) -> Optional[SimpleNamespace]:
    """Return bar closest to date_str within max_offset_days, or None."""
    if not bars:
        return None
    target = pd.Timestamp(date_str)
    best: Optional[SimpleNamespace] = None
    best_diff = max_offset_days + 1
    for b in bars:
        diff = abs((pd.Timestamp(b.date) - target).days)
        if diff < best_diff:
            best_diff = diff
            best = b
    return best if best_diff <= max_offset_days else None


def _build_date_map(bars: list) -> dict:
    return {b.date: b for b in bars}


# ---------------------------------------------------------------------------
# Comparison backtest runner
# ---------------------------------------------------------------------------


def run_comparison(
    spy_bars: list,
    tlt_bars: list,
    uup_bars: list,
    hyg_bars: list,
    alt_bars_map: dict,
) -> dict:
    """
    Run comparative backtest of short signals across alternative instruments.

    Parameters
    ----------
    spy_bars, tlt_bars, uup_bars, hyg_bars : list[SimpleNamespace]
        Same as bt.run_backtest() — used for SPY signal detection.
    alt_bars_map : dict
        {instrument_name: {"bars": list, "direction": "long"|"short"}}
        Keys must match INSTRUMENTS.

    Returns
    -------
    dict
        {
          "spy_signal_log": [...],
          "spy_short_trades": [...],     # SPY short trades (full exits only)
          "comparison_trades": {name: [trade_row, ...]},
          "episode_coverage": {episode: {triggered: bool, ...}},
        }
    """
    # Signal detection on SPY (identical to original backtest)
    spy_results = bt.run_backtest(spy_bars, tlt_bars, uup_bars, hyg_bars)

    # Short trades only, excluding partial profit rows
    short_trades = [
        t for t in spy_results["trade_log"]
        if t["side"] == "short" and "partial_profit" not in t["exit_reason"]
    ]

    # Build date-indexed lookup for each alt instrument
    alt_date_maps = {
        name: _build_date_map(info["bars"])
        for name, info in alt_bars_map.items()
    }

    comparison_trades: dict = {name: [] for name in alt_bars_map}

    for trade in short_trades:
        entry_date = trade["entry_date"]
        exit_date = trade["exit_date"]
        bars_held = trade["bars_held"]

        for name, info in alt_bars_map.items():
            bars = info["bars"]
            direction = info["direction"]
            date_map = alt_date_maps[name]

            entry_bar = date_map.get(entry_date) or _nearest_bar(bars, entry_date)
            exit_bar = date_map.get(exit_date) or _nearest_bar(bars, exit_date)

            if entry_bar is None or exit_bar is None:
                continue

            if direction == "long":
                entry_price = entry_bar.open * (1 + SLIPPAGE)
                exit_price = exit_bar.close * (1 - SLIPPAGE)
                ret = (exit_price - entry_price) / entry_price
            else:
                entry_price = entry_bar.open * (1 - SLIPPAGE)
                exit_price = exit_bar.close * (1 + SLIPPAGE)
                ret = (entry_price - exit_price) / entry_price

            row: dict = {
                "signal_date": trade["signal_date"],
                "entry_date": entry_date,
                "exit_date": exit_date,
                "bars_held": bars_held,
                "entry_price": round(entry_price, 4),
                "exit_price": round(exit_price, 4),
                "exit_reason": trade["exit_reason"],
                "return_pct": round(ret * 100, 4),
                "spy_return_pct": trade["return_pct"],
            }

            # VXX: attach informational decay cost
            if name == "VXX":
                gross_decay_pct = bars_held * VXX_DECAY_PER_BAR * 100
                row["vxx_gross_decay_pct"] = round(gross_decay_pct, 2)
                row["vxx_decay_adj_return_pct"] = round(ret * 100 - gross_decay_pct, 4)

            comparison_trades[name].append(row)

    # Episode coverage: did SPY generate entry_trigger_short during each episode?
    signal_log = spy_results["signal_log"]
    episode_coverage: dict = {}
    for ep_name, (ep_start, ep_end) in SHORT_EPISODES.items():
        ep_signals = [r for r in signal_log if ep_start <= r["date"] <= ep_end]
        triggers = [r for r in ep_signals if r["state"] == "entry_trigger_short"]
        candidates = [r for r in ep_signals if r["state"] == "breakout_candidate"]
        trades_in_ep = [
            t for t in short_trades
            if ep_start <= t["entry_date"] <= ep_end
        ]
        episode_coverage[ep_name] = {
            "period": f"{ep_start} – {ep_end}",
            "entry_trigger_days": len(triggers),
            "candidate_days": len(candidates),
            "entry_triggered": len(triggers) > 0,
            "trades_executed": len(trades_in_ep),
        }

    return {
        "spy_signal_log": signal_log,
        "spy_short_trades": short_trades,
        "comparison_trades": comparison_trades,
        "episode_coverage": episode_coverage,
    }


# ---------------------------------------------------------------------------
# Sector rotation spread
# ---------------------------------------------------------------------------


def compute_sector_rotation_spread(comparison_trades: dict) -> list:
    """
    Combine XLU (long) and XLV (long) vs XLY_short into a spread trade.

    Return = mean(XLU_return, XLV_return) + XLY_short_return, divided by number of legs.
    Only trades where all three instruments have data are included.
    """
    xlu_map = {t["entry_date"]: t for t in comparison_trades.get("XLU", [])}
    xlv_map = {t["entry_date"]: t for t in comparison_trades.get("XLV", [])}
    xly_map = {t["entry_date"]: t for t in comparison_trades.get("XLY_short", [])}

    spread_trades = []
    for entry_date, xlu_t in xlu_map.items():
        xlv_t = xlv_map.get(entry_date)
        xly_t = xly_map.get(entry_date)
        if xlv_t is None or xly_t is None:
            continue
        avg_defensive = (xlu_t["return_pct"] + xlv_t["return_pct"]) / 2
        spread_ret = (avg_defensive + xly_t["return_pct"]) / 2
        spread_trades.append({
            "signal_date": xlu_t["signal_date"],
            "entry_date": entry_date,
            "exit_date": xlu_t["exit_date"],
            "bars_held": xlu_t["bars_held"],
            "exit_reason": xlu_t["exit_reason"],
            "return_pct": round(spread_ret, 4),
            "xlu_return_pct": xlu_t["return_pct"],
            "xlv_return_pct": xlv_t["return_pct"],
            "xly_short_return_pct": xly_t["return_pct"],
            "spy_return_pct": xlu_t["spy_return_pct"],
        })
    return spread_trades


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------


def compute_comparison_metrics(
    comparison_trades: dict,
    sector_rotation_trades: list,
) -> dict:
    """Aggregate per-instrument performance metrics."""

    def _stats(trades: list) -> dict:
        if not trades:
            return {"total_trades": 0}
        returns = np.array([t["return_pct"] for t in trades])
        stats: dict = {
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
            stats["sharpe_ratio"] = round(
                float(per_bar.mean() / (per_bar.std() + 1e-9) * (252 ** 0.5)), 3
            )
        equity = [1.0]
        for t in trades:
            equity.append(equity[-1] * (1 + t["return_pct"] / 100 * POSITION_SIZE))
        arr = np.array(equity)
        roll_max = np.maximum.accumulate(arr)
        dd = (arr - roll_max) / (roll_max + 1e-9)
        stats["max_drawdown_pct"] = round(float(dd.min() * 100), 3)
        return stats

    metrics: dict = {}
    for name, trades in comparison_trades.items():
        metrics[name] = _stats(trades)
        if name == "VXX" and trades:
            decay_adj_rets = [
                t.get("vxx_decay_adj_return_pct", t["return_pct"]) for t in trades
            ]
            arr = np.array(decay_adj_rets)
            metrics["VXX_decay_adj"] = {
                "avg_return_pct": round(float(arr.mean()), 3),
                "win_rate_pct": round(float((arr > 0).mean() * 100), 1),
                "note": "Informational: subtracts estimated roll decay from raw VXX returns",
            }

    metrics["Sector_Rotation"] = _stats(sector_rotation_trades)
    return metrics


def rank_instruments(metrics: dict) -> list:
    """Return instruments sorted by Sharpe ratio (descending)."""
    ranked = []
    for name, m in metrics.items():
        if name in ("VXX_decay_adj",):
            continue
        sharpe = m.get("sharpe_ratio", float("-inf"))
        avg_ret = m.get("avg_return_pct", float("-inf"))
        win_rate = m.get("win_rate_pct", 0)
        n = m.get("total_trades", 0)
        ranked.append((name, sharpe, avg_ret, win_rate, n, m))
    ranked.sort(key=lambda x: (x[1], x[2]), reverse=True)
    return ranked


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def write_comparison_report(results: dict, metrics: dict, sector_rotation_trades: list) -> str:
    """Write the comparison report to results/short_instruments_comparison.md."""

    ranked = rank_instruments(metrics)
    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    spy_short_trades = results.get("spy_short_trades", [])
    n_spy_short = len(spy_short_trades)
    spy_win_rate = (
        round(sum(1 for t in spy_short_trades if t["return_pct"] > 0) / n_spy_short * 100, 1)
        if n_spy_short else 0
    )
    spy_avg_ret = (
        round(sum(t["return_pct"] for t in spy_short_trades) / n_spy_short, 3)
        if n_spy_short else 0.0
    )

    lines = [
        "# PTJ Macro Breakout: Short Signals — Alternative Instruments Comparison",
        "",
        f"**Run date:** {run_ts}",
        f"**Backtest period:** {START_DATE} – {END_DATE} (walk-forward, entry at next bar open)",
        "**Signal source:** `detect_macro_breakout.py` on SPY (same as TRA-61 baseline)",
        "**Execution:** Each instrument is traded in the direction noted below for the same "
        "holding periods as the SPY short signal would dictate.",
        f"**Slippage:** {SLIPPAGE * 100:.2f}% each way | "
        f"**Position size:** {POSITION_SIZE} unit | "
        f"**Max hold:** {MAX_HOLD_BARS} bars",
        "",
        "---",
        "",
        "## Baseline: SPY Short Performance (TRA-61)",
        "",
        f"- **Short trades:** {n_spy_short}",
        f"- **Win rate:** {spy_win_rate}%",
        f"- **Avg return:** {spy_avg_ret}%",
        "- **Conclusion from TRA-61:** SPY shorts structurally disadvantaged by long-term bull bias.",
        "",
        "---",
        "",
        "## Instrument Comparison Results",
        "",
        "*(Ranked by Sharpe ratio)*",
        "",
        "| # | Instrument | Type | N | Win Rate | Avg Return | Sharpe | Max DD | Avg Hold |",
        "|---|------------|------|---|----------|------------|--------|--------|----------|",
    ]

    for rank, (name, sharpe, avg_ret, win_rate, n, m) in enumerate(ranked, 1):
        itype = INSTRUMENTS.get(name, {}).get("type", "spread") if name != "Sector_Rotation" else "spread"
        max_dd = m.get("max_drawdown_pct", "—")
        avg_hold = m.get("avg_bars_held", "—")
        sharpe_str = f"{sharpe:.3f}" if sharpe != float("-inf") else "—"
        lines.append(
            f"| {rank} | `{name}` | {itype} | {n} | {win_rate}% | "
            f"{avg_ret}% | {sharpe_str} | {max_dd}% | {avg_hold} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Instrument Detail",
        "",
    ]

    for name, m in metrics.items():
        if name == "VXX_decay_adj":
            continue
        desc = INSTRUMENTS.get(name, {}).get("description", "Sector spread")
        direction = INSTRUMENTS.get(name, {}).get("direction", "spread")
        lines += [
            f"### {name}",
            f"*{desc}*  ",
            f"Direction: **{direction}**",
            "",
        ]
        if m.get("total_trades", 0) == 0:
            lines += ["*No completed trades.*", ""]
            continue
        lines += [
            f"- Total trades: {m.get('total_trades', 0)}",
            f"- Win rate: {m.get('win_rate_pct', 0)}%",
            f"- Avg return: {m.get('avg_return_pct', 0)}%",
            f"- Median return: {m.get('median_return_pct', 0)}%",
            f"- Best / Worst: {m.get('best_trade_pct', 0)}% / {m.get('worst_trade_pct', 0)}%",
            f"- Sharpe (annualised): {m.get('sharpe_ratio', 'N/A')}",
            f"- Max drawdown: {m.get('max_drawdown_pct', 0)}%",
            f"- Avg hold: {m.get('avg_bars_held', 0)} bars",
            "",
        ]
        if name == "VXX":
            vd = metrics.get("VXX_decay_adj", {})
            lines += [
                "**VXX Decay Adjustment (informational):**",
                f"- Decay-adj avg return: {vd.get('avg_return_pct', 'N/A')}%",
                f"- Decay-adj win rate: {vd.get('win_rate_pct', 'N/A')}%",
                f"- Note: {vd.get('note', '')}",
                "",
            ]

    lines += [
        "---",
        "",
        "## Episode Coverage",
        "",
        "Signal detection on SPY — did the signal fire during each macro breakdown?",
        "",
        "| Episode | Period | Entry Triggers | Candidates | Triggered? | Trades Executed |",
        "|---------|--------|----------------|-----------|------------|----------------|",
    ]

    for ep_name, ev in results.get("episode_coverage", {}).items():
        triggered = "YES" if ev["entry_triggered"] else "NO"
        lines.append(
            f"| {ep_name} | {ev['period']} | {ev['entry_trigger_days']} | "
            f"{ev['candidate_days']} | {triggered} | {ev['trades_executed']} |"
        )

    lines += [
        "",
        "---",
        "",
        "## VIX / Volatility ETP Decay Adjustment",
        "",
        "VIX ETP products (VXX, UVXY) suffer structural decay from the continuous roll of",
        "front-month VIX futures into next-month contracts. When the VIX term structure is",
        "in contango (normal market conditions), rolling short positions forward incurs a",
        "negative carry cost, reducing the ETP's NAV regardless of VIX spot level.",
        "",
        f"**Estimated decay rate used:** {VXX_DECAY_PER_BAR * 100:.1f}% per trading day "
        f"(≈ {VXX_DECAY_PER_BAR * 100 * 20:.0f}% over the {MAX_HOLD_BARS}-bar hold period)",
        "",
        "**Important:** The raw VXX return figures in this report are sourced from adjusted",
        "historical closing prices and **already embed the realised roll cost**. The",
        "decay-adjusted column subtracts an *additional* theoretical decay amount to show",
        "what alpha (if any) remains above the carry cost. A positive decay-adjusted return",
        "means the trade generated real macro signal alpha beyond the roll yield drag.",
        "",
        "**UVXY (2x):** Excluded. The 2x leverage doubles both the volatility spike and the",
        "decay rate, making the payoff profile unsuitable for a directional macro trade where",
        "timing precision is limited.",
        "",
        "---",
        "",
        "## Recommendations",
        "",
    ]

    # Identify top 1-2 instruments
    qualified = [
        (name, m) for name, sharpe, avg_ret, win_rate, n, m in ranked
        if m.get("total_trades", 0) >= 3 and m.get("avg_return_pct", float("-inf")) > 0
    ]

    if qualified:
        lines.append(
            "Based on risk-adjusted returns across the backtest period, "
            "the top instruments for executing bearish macro breakout signals are:"
        )
        lines.append("")
        for i, (name, m) in enumerate(qualified[:2], 1):
            desc = INSTRUMENTS.get(name, {}).get("description", "Sector spread")
            lines.append(
                f"{i}. **{name}** — {desc}  "
                f"Win rate: {m.get('win_rate_pct', 0)}%, "
                f"Avg return: {m.get('avg_return_pct', 0)}%, "
                f"Sharpe: {m.get('sharpe_ratio', 'N/A')}"
            )
        lines.append("")
    else:
        lines += [
            "No instrument cleared the minimum threshold (≥3 trades, positive avg return).",
            "Consider: stricter macro alignment filters or longer backtesting window.",
            "",
        ]

    lines += [
        "---",
        "",
        "## Pitfalls Checklist",
        "",
        "- [x] No look-ahead bias (entry at next bar open; SPY signals closed-bar only)",
        "- [x] Adjusted close prices for all instruments (dividends + splits via Yahoo Finance)",
        "- [x] Slippage included (0.05% each way on entry and exit)",
        "- [x] VXX decay documented; raw prices embed actual realised roll cost",
        "- [x] Time-stop at 20 bars caps VXX decay exposure per trade",
        "- [x] Episode coverage verified: 2018-Q4, 2020-Q1, 2022-Q1",
        "- [x] Sector rotation spread computed from same-date entry/exit pairs only",
        "",
    ]

    path = os.path.join(RESULTS_DIR, "short_instruments_comparison.md")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  → {path}")
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 60)
    print("PTJ Macro Breakout: Short Instruments Comparison")
    print(f"Period: {START_DATE} – {END_DATE}")
    print("=" * 60)

    print("\n[1/5] Downloading signal-detection data (SPY, TLT, UUP, HYG)...")
    spy_df = bt._yf_fetch("SPY", START_DATE, END_DATE)
    print(f"  SPY: {len(spy_df)} bars")
    time.sleep(0.3)
    tlt_df = bt._yf_fetch("TLT", START_DATE, END_DATE)
    print(f"  TLT: {len(tlt_df)} bars")
    time.sleep(0.3)
    uup_df = bt._yf_fetch("UUP", START_DATE, END_DATE)
    print(f"  UUP: {len(uup_df)} bars")
    time.sleep(0.3)
    hyg_df = bt._yf_fetch("HYG", START_DATE, END_DATE)
    print(f"  HYG: {len(hyg_df)} bars")
    time.sleep(0.3)

    spy_bars = bt._df_to_bars(spy_df)
    tlt_bars = bt._df_to_bars(tlt_df)
    uup_bars = bt._df_to_bars(uup_df)
    hyg_bars = bt._df_to_bars(hyg_df)

    print("\n[2/5] Downloading alternative instrument data...")
    alt_tickers = {
        "SH": ("SH", "long"),
        "TLT_short": ("TLT", "short"),
        "HYG_short": ("HYG", "short"),
        "VXX": ("VXX", "long"),
        "XLU": ("XLU", "long"),
        "XLV": ("XLV", "long"),
        "XLY_short": ("XLY", "short"),
    }
    alt_bars_map: dict = {}
    for name, (ticker, direction) in alt_tickers.items():
        try:
            df = bt._yf_fetch(ticker, START_DATE, END_DATE)
            bars = bt._df_to_bars(df)
            alt_bars_map[name] = {"bars": bars, "direction": direction}
            print(f"  {ticker}: {len(bars)} bars → {name}")
            time.sleep(0.3)
        except Exception as exc:
            print(f"  WARNING: {ticker} ({name}) download failed: {exc}")

    print("\n[3/5] Running comparison backtest...")
    results = run_comparison(spy_bars, tlt_bars, uup_bars, hyg_bars, alt_bars_map)
    n_spy_short = len(results["spy_short_trades"])
    print(f"  SPY short signals detected: {n_spy_short}")
    for name, trades in results["comparison_trades"].items():
        print(f"  {name}: {len(trades)} trades mapped")

    print("\n[4/5] Computing metrics and sector rotation spread...")
    sector_rotation_trades = compute_sector_rotation_spread(results["comparison_trades"])
    print(f"  Sector rotation spread trades: {len(sector_rotation_trades)}")
    metrics = compute_comparison_metrics(results["comparison_trades"], sector_rotation_trades)

    print("\n[5/5] Writing report...")
    write_comparison_report(results, metrics, sector_rotation_trades)

    print("\n" + "=" * 60)
    print("SUMMARY (sorted by Sharpe ratio)")
    print("=" * 60)
    ranked = rank_instruments(metrics)
    print(f"\n{'Instrument':<20} {'N':>4} {'Win%':>6} {'AvgRet%':>9} {'Sharpe':>8} {'MaxDD%':>8}")
    print("-" * 60)
    for name, sharpe, avg_ret, win_rate, n, m in ranked:
        sharpe_str = f"{sharpe:.3f}" if sharpe != float("-inf") else "  N/A"
        max_dd = m.get("max_drawdown_pct", 0.0)
        print(f"{name:<20} {n:>4} {win_rate:>6.1f} {avg_ret:>9.3f} {sharpe_str:>8} {max_dd:>8.3f}")

    print("\nEpisode coverage:")
    for ep_name, ev in results["episode_coverage"].items():
        tag = "TRIGGERED" if ev["entry_triggered"] else "MISSED"
        print(f"  [{tag}] {ep_name}: {ev['entry_trigger_days']} trigger days")

    print("\nDone.")


if __name__ == "__main__":
    main()
