#!/usr/bin/env python3
"""
backtest_ptj_crash.py — Walk-forward backtest for the PTJ Crash Playbook
/ Liquidity Breakdown strategy.

Design principles:
  - Zero look-ahead: indicators computed on bars[0..d] only; trade entry is
    at the OPEN of bar d+1 (next day's open).
  - Strategy rules verbatim: all parameters from detect_crash_liquidity_breakdown.py
    defaults; no parameter optimisation.
  - Realistic execution: 0.05% slippage each way, fractional 0.33-unit sizing.
  - Gap-down open rule: no short entry when gap_down_open is True.
  - Stop hierarchy: risk_high stop → de_risk_exit signal → fixed 20-bar timeout.
  - Adjusted close prices from Yahoo Finance (dividends + splits included).

Data sources: Yahoo Finance (SPY, ^VIX, HYG, RSP) — no TWS required.

Outputs (all in ./results/):
  - signal_history.csv       : daily signal state for every bar
  - forward_returns.csv      : 5 / 10 / 20 bar returns after entry_trigger_short
  - trades.csv               : individual trade-level results
  - performance_summary.md   : aggregate metrics + episode hit-check
"""

import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional

import types

import pandas as pd
import numpy as np

# Locate the tools directory relative to this file
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

# Inject a stub for ib_async so the pure-function module loads without TWS.
# The backtest only uses calculate_indicators and evaluate_crash_playbook,
# which have zero dependency on ib_async at runtime.
if "ib_async" not in sys.modules:
    _stub = types.ModuleType("ib_async")
    _stub.IB = object
    _stub.Stock = object
    _stub.Future = object
    _stub.Contract = object
    _stub.Index = object
    sys.modules["ib_async"] = _stub

from tools.detect_crash_liquidity_breakdown import (
    calculate_indicators,
    evaluate_crash_playbook,
)

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SLIPPAGE = 0.0005        # 0.05% each way
POSITION_SIZE = 0.33     # strategy spec: 0.33 risk unit
MAX_HOLD_BARS = 20       # fixed time-stop (bars)

_TRADE_LOG_FIELDS = [
    "entry_date", "entry_price", "exit_date", "exit_price", "exit_reason",
    "bars_held", "return_pct", "signal_date", "entry_support_shelf", "position_size",
]
START_DATE = "2015-01-01"
END_DATE = "2025-06-01"

# Known crash episodes to verify coverage
EPISODES = {
    "2018-Q4 Selloff":  ("2018-10-01", "2018-12-24"),
    "2020 COVID Crash": ("2020-02-19", "2020-03-23"),
    "2022 Bear Market": ("2022-01-04", "2022-10-13"),
}


# ---------------------------------------------------------------------------
# Data download: yfinance (preferred) with HTTP fallback
# ---------------------------------------------------------------------------

try:
    import yfinance as _yf
    _HAS_YF = True
except ImportError:
    _HAS_YF = False


def _yf_fetch(ticker: str, start: str, end: str, max_retries: int = 3) -> pd.DataFrame:
    """Download daily OHLCV. Uses yfinance when available, else Yahoo Finance v8 HTTP."""
    if _HAS_YF:
        return _fetch_via_yfinance(ticker, start, end)
    return _fetch_via_http(ticker, start, end, max_retries)


def _fetch_via_yfinance(ticker: str, start: str, end: str) -> pd.DataFrame:
    df_raw = _yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if df_raw.empty:
        raise RuntimeError(f"yfinance returned empty data for {ticker}")
    # yfinance returns MultiIndex columns when downloading single ticker
    if isinstance(df_raw.columns, pd.MultiIndex):
        df_raw.columns = [c[0].lower() for c in df_raw.columns]
    else:
        df_raw.columns = [c.lower() for c in df_raw.columns]
    df_raw = df_raw.rename(columns={"adj close": "close"}) if "adj close" in df_raw.columns else df_raw
    df = pd.DataFrame({
        "date": df_raw.index,
        "open": df_raw["open"].values,
        "high": df_raw["high"].values,
        "low": df_raw["low"].values,
        "close": df_raw["close"].values,
        "volume": df_raw["volume"].values,
    })
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _fetch_via_http(ticker: str, start: str, end: str, max_retries: int = 3) -> pd.DataFrame:
    """Fallback: download daily OHLCV from Yahoo Finance v8 chart API."""
    start_ts = int(pd.Timestamp(start).timestamp())
    end_ts = int(pd.Timestamp(end).timestamp())
    url = (
        f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker, safe='')}"
        f"?interval=1d&period1={start_ts}&period2={end_ts}&events=adjsplit"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
    last_err = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = json.loads(resp.read().decode())
            result = raw["chart"]["result"][0]
            timestamps = result["timestamp"]
            ohlcv = result["indicators"]["quote"][0]
            adj = result["indicators"].get("adjclose", [{}])
            adj_closes = adj[0].get("adjclose") if adj else None

            dates = [
                datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                for ts in timestamps
            ]
            df = pd.DataFrame({
                "date": dates,
                "open": ohlcv["open"],
                "high": ohlcv["high"],
                "low": ohlcv["low"],
                "close": ohlcv["close"],
                "volume": ohlcv["volume"],
            })
            if adj_closes:
                ratio = [
                    (ac / c if c and c != 0 else 1.0)
                    for ac, c in zip(adj_closes, ohlcv["close"])
                ]
                df["open"] = [o * r for o, r in zip(df["open"], ratio)]
                df["high"] = [h * r for h, r in zip(df["high"], ratio)]
                df["low"] = [l * r for l, r in zip(df["low"], ratio)]
                df["close"] = adj_closes
            df = df.dropna(subset=["close"]).reset_index(drop=True)
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            return df
        except Exception as exc:
            last_err = exc
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to download {ticker}: {last_err}")


def _df_to_bars(df: pd.DataFrame) -> list:
    """Convert DataFrame rows to SimpleNamespace bar objects."""
    bars = []
    for _, row in df.iterrows():
        bars.append(
            SimpleNamespace(
                date=row["date"].strftime("%Y-%m-%d"),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0) or 0),
            )
        )
    return bars


# ---------------------------------------------------------------------------
# Walk-forward engine
# ---------------------------------------------------------------------------

def run_backtest(
    spy_bars: list,
    vix_bars: list,
    hyg_bars: list,
    rsp_bars: list,
) -> dict:
    """
    Walk-forward backtest over all SPY bars.

    For each day d (starting at bar 260 to have enough history):
      1. Compute indicators using spy_bars[0:d+1] etc.
      2. Record signal state.
      3. If state == entry_trigger_short and no open position:
         - Entry at spy_bars[d+1].open next day (if d+1 exists)
         - Stop: support_shelf from the signal bar (adjusted for slippage)
      4. If in position: check exits (stop, signal de_risk, time).

    Returns: dict with signal_log, trade_log, forward_returns_log.
    """
    n = len(spy_bars)
    signal_log = []       # one row per bar
    trade_log = []        # one row per completed trade
    forward_returns = []  # forward returns for every entry trigger bar

    # Align VIX/HYG/RSP bars by date (build lookup)
    def _build_date_map(bars):
        return {b.date: b for b in bars}

    vix_map = _build_date_map(vix_bars)
    hyg_map = _build_date_map(hyg_bars)
    rsp_map = _build_date_map(rsp_bars)

    # Walk forward — need at least 260 bars of history
    open_trade = None   # dict when position is open

    for d in range(260, n):
        primary = spy_bars[: d + 1]
        today = spy_bars[d]
        today_date = today.date

        # Collect aligned optional bars up to and including today
        vix_hist = [b for b in vix_bars if b.date <= today_date]
        hyg_hist = [b for b in hyg_bars if b.date <= today_date]
        rsp_hist = [b for b in rsp_bars if b.date <= today_date]

        features = calculate_indicators(
            primary,
            vix_bars=vix_hist or None,
            credit_bars=hyg_hist or None,
            breadth_bars=rsp_hist or None,
        )

        position_context = None
        if open_trade:
            position_context = {
                "position_side": "short",
                "entry_price": open_trade["entry_price"],
                "risk_high": open_trade["risk_high"],
            }

        evaluation = evaluate_crash_playbook(features, position_context=position_context)
        state = evaluation["signal_state"]
        confidence = evaluation["confidence"]
        confirmations = evaluation["scorecard"].get("confirmations", 0)
        support_shelf = features.get("support_shelf")
        close = today.close

        signal_log.append(
            {
                "date": today_date,
                "state": state,
                "confidence": round(confidence, 3),
                "confirmations": confirmations,
                "close": round(close, 4),
                "support_shelf": round(support_shelf, 4) if support_shelf else None,
                "vix": features.get("vix_level"),
                "below_200dma": features.get("below_200dma"),
                "shelf_break": features.get("shelf_break"),
                "gap_down_open": features.get("gap_down_open"),
                "in_position": open_trade is not None,
            }
        )

        # --- Manage open position ---
        if open_trade:
            bars_held = open_trade["bars_held"] + 1
            open_trade["bars_held"] = bars_held

            exit_reason = None
            exit_price = None

            # Risk-high stop: close above the stop level
            if close > open_trade["risk_high"]:
                exit_reason = "stop_risk_high"
                exit_price = close * (1 + SLIPPAGE)  # slip on exit (short cover)

            # De-risk exit signal
            elif state == "de_risk_exit":
                exit_reason = "derisk_signal"
                exit_price = close * (1 + SLIPPAGE)

            # Fixed time-stop
            elif bars_held >= MAX_HOLD_BARS:
                exit_reason = "time_stop"
                exit_price = close * (1 + SLIPPAGE)

            if exit_reason:
                ret = (open_trade["entry_price"] - exit_price) / open_trade["entry_price"]
                trade_log.append(
                    {
                        "entry_date": open_trade["entry_date"],
                        "entry_price": round(open_trade["entry_price"], 4),
                        "exit_date": today_date,
                        "exit_price": round(exit_price, 4),
                        "exit_reason": exit_reason,
                        "bars_held": bars_held,
                        "return_pct": round(ret * 100, 4),
                        "signal_date": open_trade["signal_date"],
                        "entry_support_shelf": round(open_trade["entry_support_shelf"], 4),
                        "position_size": POSITION_SIZE,
                    }
                )
                open_trade = None

        # --- Check for new entry ---
        if (
            open_trade is None
            and state == "entry_trigger_short"
            and not features.get("gap_down_open")
            and d + 1 < n
        ):
            next_bar = spy_bars[d + 1]
            # Skip if execution bar opens below the support shelf (crash-gap at execution)
            if next_bar.open >= (support_shelf or close):
                entry_price = next_bar.open * (1 - SLIPPAGE)  # sell short at open minus slip

                # Stop: support_shelf from signal bar (slightly above for wiggle room +0.5%)
                risk_high = (support_shelf or close) * 1.005

                # Record forward returns (5, 10, 20 bars) relative to today's close
                fwd = {"signal_date": today_date, "close_at_signal": round(close, 4)}
                for horizon in [5, 10, 20]:
                    future_idx = d + horizon
                    if future_idx < n:
                        fwd[f"fwd_{horizon}d_return_pct"] = round(
                            (close - spy_bars[future_idx].close) / close * 100, 4
                        )
                    else:
                        fwd[f"fwd_{horizon}d_return_pct"] = None
                forward_returns.append(fwd)

                open_trade = {
                    "signal_date": today_date,
                    "entry_date": next_bar.date,
                    "entry_price": entry_price,
                    "risk_high": risk_high,
                    "entry_support_shelf": support_shelf or close,
                    "bars_held": 0,
                }

    # Close any still-open position at the last bar
    if open_trade and spy_bars:
        last = spy_bars[-1]
        exit_price = last.close * (1 + SLIPPAGE)
        ret = (open_trade["entry_price"] - exit_price) / open_trade["entry_price"]
        trade_log.append(
            {
                "entry_date": open_trade["entry_date"],
                "entry_price": round(open_trade["entry_price"], 4),
                "exit_date": last.date,
                "exit_price": round(exit_price, 4),
                "exit_reason": "end_of_data",
                "bars_held": open_trade["bars_held"],
                "return_pct": round(ret * 100, 4),
                "signal_date": open_trade["signal_date"],
                "entry_support_shelf": round(open_trade["entry_support_shelf"], 4),
                "position_size": POSITION_SIZE,
            }
        )

    return {
        "signal_log": signal_log,
        "trade_log": trade_log,
        "forward_returns": forward_returns,
    }


# ---------------------------------------------------------------------------
# Performance analytics
# ---------------------------------------------------------------------------

def compute_metrics(results: dict, spy_bars: list) -> dict:
    signal_log = results["signal_log"]
    trade_log = results["trade_log"]
    fwd_returns = results["forward_returns"]

    # Signal frequency
    from collections import Counter
    state_counts = Counter(r["state"] for r in signal_log)
    total_bars = len(signal_log)

    # Forward return stats for entry_trigger_short
    fwd_5 = [r["fwd_5d_return_pct"] for r in fwd_returns if r["fwd_5d_return_pct"] is not None]
    fwd_10 = [r["fwd_10d_return_pct"] for r in fwd_returns if r["fwd_10d_return_pct"] is not None]
    fwd_20 = [r["fwd_20d_return_pct"] for r in fwd_returns if r["fwd_20d_return_pct"] is not None]

    def _stats(vals):
        if not vals:
            return {}
        arr = np.array(vals)
        return {
            "mean": round(float(np.mean(arr)), 3),
            "median": round(float(np.median(arr)), 3),
            "std": round(float(np.std(arr)), 3),
            "win_rate_pct": round(float((arr > 5.0).mean() * 100), 1),
            "loss_rate_pct": round(float((arr < -5.0).mean() * 100), 1),
            "min": round(float(arr.min()), 3),
            "max": round(float(arr.max()), 3),
            "n": len(vals),
        }

    # Trade stats
    returns = np.array([t["return_pct"] for t in trade_log]) if trade_log else np.array([])
    trade_stats = {}
    if len(returns):
        trade_stats = {
            "total_trades": len(returns),
            "win_rate_pct": round(float((returns > 0).mean() * 100), 1),
            "avg_return_pct": round(float(returns.mean()), 3),
            "median_return_pct": round(float(np.median(returns)), 3),
            "best_trade_pct": round(float(returns.max()), 3),
            "worst_trade_pct": round(float(returns.min()), 3),
            "avg_bars_held": round(
                float(np.mean([t["bars_held"] for t in trade_log])), 1
            ),
        }
        # Sharpe: annualised (assuming ~252 trading bars per year)
        # Daily strategy returns series (0 except on trade bars, position_size * daily_return)
        if len(returns) > 1:
            gross_daily = returns / [t["bars_held"] or 1 for t in trade_log]
            sharpe = float(np.mean(gross_daily) / (np.std(gross_daily) + 1e-9) * np.sqrt(252))
            trade_stats["sharpe_ratio"] = round(sharpe, 3)
        # Max drawdown on equity curve
        equity = [1.0]
        for t in trade_log:
            prev = equity[-1]
            equity.append(prev * (1 + t["return_pct"] / 100 * POSITION_SIZE))
        eq_arr = np.array(equity)
        rolling_max = np.maximum.accumulate(eq_arr)
        drawdowns = (eq_arr - rolling_max) / rolling_max
        trade_stats["max_drawdown_pct"] = round(float(drawdowns.min() * 100), 3)

    # Episode hit-check
    episode_hits = {}
    for name, (ep_start, ep_end) in EPISODES.items():
        ep_signals = [
            r for r in signal_log
            if ep_start <= r["date"] <= ep_end
            and r["state"] in ("entry_trigger_short", "setup_armed")
        ]
        entry_triggers = [r for r in ep_signals if r["state"] == "entry_trigger_short"]
        episode_hits[name] = {
            "period": f"{ep_start} – {ep_end}",
            "entry_trigger_days": len(entry_triggers),
            "setup_armed_days": len(ep_signals) - len(entry_triggers),
            "stress_detected": len(ep_signals) > 0,
            "entry_triggered": len(entry_triggers) > 0,
        }

    return {
        "signal_frequency": {
            state: {
                "count": count,
                "pct": round(count / total_bars * 100, 1) if total_bars else 0,
            }
            for state, count in state_counts.most_common()
        },
        "forward_return_stats": {
            "5d": _stats(fwd_5),
            "10d": _stats(fwd_10),
            "20d": _stats(fwd_20),
        },
        "trade_stats": trade_stats,
        "episode_hits": episode_hits,
        "total_bars_evaluated": total_bars,
    }


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def write_results(results: dict, metrics: dict) -> None:
    # signal_history.csv
    sig_path = os.path.join(RESULTS_DIR, "signal_history.csv")
    if results["signal_log"]:
        keys = results["signal_log"][0].keys()
        with open(sig_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(keys))
            w.writeheader()
            w.writerows(results["signal_log"])
        print(f"  → {sig_path} ({len(results['signal_log'])} rows)")

    # trades.csv — always write to avoid stale artifacts from a previous run
    trade_path = os.path.join(RESULTS_DIR, "trades.csv")
    keys = results["trade_log"][0].keys() if results["trade_log"] else _TRADE_LOG_FIELDS
    with open(trade_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(keys))
        w.writeheader()
        w.writerows(results["trade_log"])
    print(f"  → {trade_path} ({len(results['trade_log'])} rows)")

    # forward_returns.csv
    fwd_path = os.path.join(RESULTS_DIR, "forward_returns.csv")
    if results["forward_returns"]:
        keys = results["forward_returns"][0].keys()
        with open(fwd_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(keys))
            w.writeheader()
            w.writerows(results["forward_returns"])
        print(f"  → {fwd_path} ({len(results['forward_returns'])} rows)")

    # performance_summary.md
    _write_summary_md(metrics)


def _write_summary_md(metrics: dict) -> None:
    lines = [
        "# Backtest Results: PTJ Crash Playbook / Liquidity Breakdown",
        "",
        f"**Run date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Backtest period:** {START_DATE} – {END_DATE}  "
        "(walk-forward, entry at next bar open)",
        f"**Instruments:** SPY (primary), ^VIX, HYG (credit), RSP (breadth)",
        f"**Parameters:** strategy defaults — shelf_lookback=63, break_threshold=0.005",
        f"**Slippage:** {SLIPPAGE * 100:.2f}% each way | "
        f"**Position size:** {POSITION_SIZE} unit | "
        f"**Max hold:** {MAX_HOLD_BARS} bars",
        "",
        "---",
        "",
        "## Signal Frequency",
        "",
        "| State | Count | % of Bars |",
        "|-------|-------|-----------|",
    ]
    for state, v in metrics["signal_frequency"].items():
        lines.append(f"| `{state}` | {v['count']} | {v['pct']}% |")

    lines += [
        "",
        f"*Total bars evaluated: {metrics['total_bars_evaluated']}*",
        "",
        "---",
        "",
        "## Forward Returns After `entry_trigger_short`",
        "",
        "*(Returns are positive when price falls — short profits)*",
        "",
        "| Horizon | N | Mean | Median | Std | Win >5% | Loss >5% | Best | Worst |",
        "|---------|---|------|--------|-----|---------|----------|------|-------|",
    ]
    for horizon in ["5d", "10d", "20d"]:
        s = metrics["forward_return_stats"].get(horizon, {})
        if s.get("n"):
            lines.append(
                f"| {horizon} | {s['n']} | {s['mean']}% | {s['median']}% | "
                f"{s['std']}% | {s['win_rate_pct']}% | {s['loss_rate_pct']}% | "
                f"{s['max']}% | {s['min']}% |"
            )
        else:
            lines.append(f"| {horizon} | 0 | — | — | — | — | — | — | — |")

    lines += [
        "",
        "---",
        "",
        "## Trade-Level Statistics",
        "",
    ]
    ts = metrics.get("trade_stats", {})
    if ts:
        lines += [
            f"- **Total trades:** {ts.get('total_trades', 0)}",
            f"- **Win rate:** {ts.get('win_rate_pct', 0)}% (positive P&L)",
            f"- **Average return:** {ts.get('avg_return_pct', 0)}%",
            f"- **Median return:** {ts.get('median_return_pct', 0)}%",
            f"- **Best trade:** {ts.get('best_trade_pct', 0)}%",
            f"- **Worst trade:** {ts.get('worst_trade_pct', 0)}%",
            f"- **Average hold:** {ts.get('avg_bars_held', 0)} bars",
            f"- **Sharpe ratio (annualised):** {ts.get('sharpe_ratio', 'N/A')}",
            f"- **Max drawdown:** {ts.get('max_drawdown_pct', 0)}%",
        ]
    else:
        lines.append("*No completed trades.*")

    lines += [
        "",
        "---",
        "",
        "## Known Crash Episode Coverage",
        "",
        "| Episode | Period | Entry Trigger Days | Setup Armed Days | Stress Detected? | Entry Triggered? |",
        "|---------|--------|-------------------|-----------------|-----------------|-----------------|",
    ]
    for ep, v in metrics["episode_hits"].items():
        stress = "YES" if v["stress_detected"] else "NO"
        triggered = "YES" if v["entry_triggered"] else "NO"
        lines.append(
            f"| {ep} | {v['period']} | {v['entry_trigger_days']} | "
            f"{v['setup_armed_days']} | {stress} | {triggered} |"
        )

    if any(v["stress_detected"] and not v["entry_triggered"] for v in metrics["episode_hits"].values()):
        lines += [
            "",
            "> **Note on stress-only episodes:** A `setup_armed` reading means liquidity stress was detected, "
            "but it is not a tradable entry by itself. The strategy requires a failed retest and "
            "an `entry_trigger_short`; stress-only episodes should be treated as watchlist/triage "
            "signals, not completed short setups.",
        ]

    lines += [
        "",
        "---",
        "",
        "## Pitfalls Checklist",
        "",
        "- [x] No look-ahead bias (entry at next bar open, no future features)",
        "- [x] Adjusted close prices (dividends + splits)",
        "- [x] No parameter optimisation (defaults only)",
        "- [x] Slippage included (0.05% each way)",
        "- [x] Gap-down open exclusion enforced",
        "- [x] Time-stop prevents indefinite drawdown",
        "- [x] Stop at support shelf level (per strategy rules)",
        "",
        "## Files in This Folder",
        "",
        "| File | Description |",
        "|------|-------------|",
        "| `backtest_ptj_crash.py` | Backtest engine |",
        "| `SMART_GOAL.md` | SMART goal definition |",
        "| `results/signal_history.csv` | Daily signal state |",
        "| `results/trades.csv` | Trade-level results |",
        "| `results/forward_returns.csv` | Forward returns per entry trigger |",
        "| `results/performance_summary.md` | This file |",
    ]

    path = os.path.join(RESULTS_DIR, "performance_summary.md")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  → {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("PTJ Crash Playbook Backtest")
    print(f"Period: {START_DATE} – {END_DATE}")
    print("=" * 60)

    print("\n[1/4] Downloading historical data...")

    spy_df = _yf_fetch("SPY", START_DATE, END_DATE)
    print(f"  SPY:  {len(spy_df)} bars  ({spy_df['date'].iloc[0].date()} – {spy_df['date'].iloc[-1].date()})")
    time.sleep(0.5)

    vix_df = _yf_fetch("^VIX", START_DATE, END_DATE)
    print(f"  ^VIX: {len(vix_df)} bars  ({vix_df['date'].iloc[0].date()} – {vix_df['date'].iloc[-1].date()})")
    time.sleep(0.5)

    hyg_df = _yf_fetch("HYG", START_DATE, END_DATE)
    print(f"  HYG:  {len(hyg_df)} bars  ({hyg_df['date'].iloc[0].date()} – {hyg_df['date'].iloc[-1].date()})")
    time.sleep(0.5)

    rsp_df = _yf_fetch("RSP", START_DATE, END_DATE)
    print(f"  RSP:  {len(rsp_df)} bars  ({rsp_df['date'].iloc[0].date()} – {rsp_df['date'].iloc[-1].date()})")

    spy_bars = _df_to_bars(spy_df)
    vix_bars = _df_to_bars(vix_df)
    hyg_bars = _df_to_bars(hyg_df)
    rsp_bars = _df_to_bars(rsp_df)

    print(f"\n[2/4] Running walk-forward backtest ({len(spy_bars)} bars)...")
    results = run_backtest(spy_bars, vix_bars, hyg_bars, rsp_bars)
    print(f"  Evaluated: {len(results['signal_log'])} signal bars")
    print(f"  Entry triggers: {len(results['forward_returns'])}")
    print(f"  Completed trades: {len(results['trade_log'])}")

    print("\n[3/4] Computing performance metrics...")
    metrics = compute_metrics(results, spy_bars)

    print("\n[4/4] Writing results to ./results/...")
    write_results(results, metrics)

    # Print summary to terminal
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    print("\nSignal frequency (top 5):")
    for state, v in list(metrics["signal_frequency"].items())[:5]:
        print(f"  {state:35s}  {v['count']:5d} bars  ({v['pct']}%)")

    print("\nForward returns after entry_trigger_short:")
    for horizon in ["5d", "10d", "20d"]:
        s = metrics["forward_return_stats"].get(horizon, {})
        if s.get("n"):
            print(
                f"  {horizon}:  mean={s['mean']:+.2f}%  "
                f"median={s['median']:+.2f}%  "
                f"win(>5%)={s['win_rate_pct']}%  "
                f"n={s['n']}"
            )

    ts = metrics.get("trade_stats", {})
    if ts:
        print(f"\nTrade stats:")
        print(f"  Trades:      {ts.get('total_trades', 0)}")
        print(f"  Win rate:    {ts.get('win_rate_pct', 0)}%")
        print(f"  Avg return:  {ts.get('avg_return_pct', 0):+.3f}%")
        print(f"  Sharpe:      {ts.get('sharpe_ratio', 'N/A')}")
        print(f"  Max DD:      {ts.get('max_drawdown_pct', 0):.2f}%")

    print("\nEpisode coverage:")
    for ep, v in metrics["episode_hits"].items():
        triggered = "ENTRY" if v["entry_triggered"] else ("STRESS" if v["stress_detected"] else "MISS")
        print(
            f"  [{triggered:6s}] {ep}: "
            f"{v['entry_trigger_days']} entry trigger + "
            f"{v['setup_armed_days']} setup_armed days"
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
