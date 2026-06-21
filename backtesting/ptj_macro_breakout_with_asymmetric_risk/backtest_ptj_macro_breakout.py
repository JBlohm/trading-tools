#!/usr/bin/env python3
"""
backtest_ptj_macro_breakout.py — Walk-forward backtest for the PTJ Macro Breakout
With Asymmetric Risk strategy.

Design principles:
  - Zero look-ahead: indicators computed on bars[0..d] only; trade entry is
    at the OPEN of bar d+1 (next day's open).
  - Strategy rules verbatim: all parameters from detect_macro_breakout.py
    defaults; no parameter optimisation.
  - Realistic execution: 0.05% slippage each way, 0.33-unit position sizing.
  - Gap exclusion: no long entry on gap-up open; no short entry on gap-down open.
  - Stop hierarchy: structural stop → failed-breakout exit → fixed 20-bar timeout.
  - Partial-profit rule: take 1/3 off at 2.5R; trail the remainder.
  - Adjusted close prices from Yahoo Finance (dividends + splits included).
  - Both long and short trades evaluated separately.

Data sources: Yahoo Finance (SPY, TLT, UUP, HYG) — no TWS required.

Outputs (all in ./results/):
  - signal_history.csv        : daily signal state for every bar
  - forward_returns.csv       : 5 / 10 / 20 bar returns after entry triggers
  - trades.csv                : individual trade-level results
  - performance_summary.md    : aggregate metrics + episode hit-check
"""

import csv
import json
import os
import sys
import time
import types
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Path setup — allow importing from tools/ without installation
# ---------------------------------------------------------------------------
_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, _ROOT)

# Stub ib_async so tools that import it at module level load without TWS
if "ib_async" not in sys.modules:
    _stub = types.ModuleType("ib_async")
    for _attr in ("IB", "Stock", "Future", "Contract", "Index", "Forex", "Option"):
        setattr(_stub, _attr, object)
    sys.modules["ib_async"] = _stub

from tools.detect_macro_breakout import calculate_indicators, evaluate_breakout_signal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SLIPPAGE = 0.0005          # 0.05% each way
POSITION_SIZE = 0.33       # risk unit (probe size per the strategy spec)
MAX_HOLD_BARS = 20         # fixed time-stop
PARTIAL_PROFIT_R = 2.5     # take 1/3 off at 2.5R
PARTIAL_FRACTION = 1 / 3   # fraction to take at partial-profit target
START_DATE = "2015-01-01"
END_DATE = "2025-06-01"
WARMUP_BARS = 220          # bars needed before first signal evaluation

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

# Known macro breakout / breakdown episodes for coverage check
LONG_EPISODES = {
    "2016-Q4 Trump Rally":        ("2016-11-01", "2016-12-31"),
    "2019-Q1 Fed Pivot Breakout": ("2019-01-01", "2019-03-31"),
    "2023-Q1 Bear Recovery":      ("2023-01-01", "2023-03-31"),
}
SHORT_EPISODES = {
    "2018-Q4 Selloff":   ("2018-10-01", "2018-12-24"),
    "2020-Q1 COVID":     ("2020-02-19", "2020-03-23"),
    "2022-Q1 Bear Onset":("2022-01-04", "2022-04-30"),
}

# ---------------------------------------------------------------------------
# Data download helpers
# ---------------------------------------------------------------------------

try:
    import yfinance as _yf
    _HAS_YF = True
except ImportError:
    _HAS_YF = False


def _yf_fetch(ticker: str, start: str, end: str, max_retries: int = 3) -> pd.DataFrame:
    if _HAS_YF:
        return _fetch_via_yfinance(ticker, start, end)
    return _fetch_via_http(ticker, start, end, max_retries)


def _fetch_via_yfinance(ticker: str, start: str, end: str) -> pd.DataFrame:
    df_raw = _yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if df_raw.empty:
        raise RuntimeError(f"yfinance returned empty data for {ticker}")
    if isinstance(df_raw.columns, pd.MultiIndex):
        df_raw.columns = [c[0].lower() for c in df_raw.columns]
    else:
        df_raw.columns = [c.lower() for c in df_raw.columns]
    if "adj close" in df_raw.columns:
        df_raw = df_raw.rename(columns={"adj close": "close"})
    df = pd.DataFrame({
        "date": df_raw.index,
        "open": df_raw["open"].values,
        "high": df_raw["high"].values,
        "low": df_raw["low"].values,
        "close": df_raw["close"].values,
        "volume": df_raw.get("volume", pd.Series([0] * len(df_raw))).values,
    })
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _fetch_via_http(ticker: str, start: str, end: str, max_retries: int = 3) -> pd.DataFrame:
    start_ts = int(pd.Timestamp(start).timestamp())
    end_ts = int(pd.Timestamp(end).timestamp())
    url = (
        f"https://query2.finance.yahoo.com/v8/finance/chart/"
        f"{urllib.parse.quote(ticker, safe='')}"
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
            adj_list = result["indicators"].get("adjclose", [{}])
            adj_closes = adj_list[0].get("adjclose") if adj_list else None
            dates = [
                datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                for ts in timestamps
            ]
            close_raw = ohlcv["close"]
            volume_raw = ohlcv.get("volume") or [0] * len(dates)
            df = pd.DataFrame({
                "date": dates,
                "open": ohlcv["open"],
                "high": ohlcv["high"],
                "low": ohlcv["low"],
                "close": close_raw,
                "volume": volume_raw,
            })
            if adj_closes:
                ratio = [
                    (ac / c if c and c != 0 else 1.0)
                    for ac, c in zip(adj_closes, close_raw)
                ]
                df["open"] = [o * r for o, r in zip(df["open"], ratio)]
                df["high"] = [h * r for h, r in zip(df["high"], ratio)]
                df["low"] = [lo * r for lo, r in zip(df["low"], ratio)]
                df["close"] = adj_closes
            df = df.dropna(subset=["close"]).reset_index(drop=True)
            df["date"] = pd.to_datetime(df["date"])
            return df.sort_values("date").reset_index(drop=True)
        except Exception as exc:
            last_err = exc
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to download {ticker}: {last_err}")


def _df_to_bars(df: pd.DataFrame) -> list:
    bars = []
    for _, row in df.iterrows():
        bars.append(SimpleNamespace(
            date=row["date"].strftime("%Y-%m-%d"),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume", 0) or 0),
        ))
    return bars


# ---------------------------------------------------------------------------
# Walk-forward backtest engine
# ---------------------------------------------------------------------------

def run_backtest(
    spy_bars: list,
    tlt_bars: list,
    uup_bars: list,
    hyg_bars: list,
) -> dict:
    """
    Walk-forward backtest over all SPY bars.

    For each day d (starting at WARMUP_BARS):
      1. Compute indicators using spy_bars[0:d+1] and aligned secondary bars.
      2. Record signal state.
      3. If state is an entry trigger and no open position exists:
         Schedule entry at spy_bars[d+1].open (next day).
      4. If in position: check exits in order —
         a. structural stop (long_stop / short_stop from signal bar)
         b. failed-breakout exit signal
         c. partial-profit at 2.5R → flag for eventual full exit
         d. fixed time-stop at MAX_HOLD_BARS
    """
    n = len(spy_bars)
    signal_log: list = []
    trade_log: list = []
    forward_returns: list = []

    def _align(bars: list, up_to_date: str) -> list:
        return [b for b in bars if b.date <= up_to_date]

    open_trade: Optional[dict] = None

    for d in range(WARMUP_BARS, n):
        today = spy_bars[d]
        today_date = today.date
        primary = spy_bars[:d + 1]

        tlt_hist = _align(tlt_bars, today_date)
        uup_hist = _align(uup_bars, today_date)
        hyg_hist = _align(hyg_bars, today_date)

        # Position context for in-trade signal evaluation
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
            "range_compressed": scorecard.get("range_compressed", 0),
            "breakout_long": int(bool(features.get("breakout_long"))),
            "breakout_short": int(bool(features.get("breakout_short"))),
            "retest_hold_long": int(bool(features.get("retest_hold_long"))),
            "retest_hold_short": int(bool(features.get("retest_hold_short"))),
            "close": round(today.close, 4),
            "range_high": round(features["range_high"], 4) if features.get("range_high") else None,
            "range_low": round(features["range_low"], 4) if features.get("range_low") else None,
            "atr20": round(features["atr20"], 4) if features.get("atr20") else None,
            "in_position": open_trade is not None,
            "position_side": open_trade["side"] if open_trade else None,
        })

        # -------------------------------------------------------------------
        # Manage open position
        # -------------------------------------------------------------------
        if open_trade:
            open_trade["bars_held"] += 1
            bars_held = open_trade["bars_held"]
            close = today.close
            side = open_trade["side"]
            entry = open_trade["entry_price"]
            r_unit = open_trade["r_unit"]
            partial_taken = open_trade.get("partial_taken", False)

            exit_reason = None
            exit_price = None

            if state in ("exit_signal",):
                # stop hit or failed breakout from evaluate_breakout_signal
                if side == "long":
                    exit_reason = scorecard.get("reason", "exit_signal")
                    exit_price = close * (1 - SLIPPAGE)  # sell to exit long
                else:
                    exit_reason = scorecard.get("reason", "exit_signal")
                    exit_price = close * (1 + SLIPPAGE)  # buy to cover short

            elif side == "long":
                # Structural stop (below stop_level)
                if close < open_trade["stop_level"]:
                    exit_reason = "stop_long"
                    exit_price = close * (1 - SLIPPAGE)
                # Partial profit at 2.5R (mark — actual exit via next logic)
                elif not partial_taken and r_unit and (close - entry) >= r_unit * PARTIAL_PROFIT_R:
                    open_trade["partial_taken"] = True
                    # Record partial exit trade
                    partial_ret = (close * (1 - SLIPPAGE) - entry) / entry
                    trade_log.append(_trade_row(
                        open_trade, today_date, close * (1 - SLIPPAGE),
                        "partial_profit_long", bars_held,
                        partial_ret, POSITION_SIZE * PARTIAL_FRACTION,
                    ))
                    # Reduce position size for remainder
                    open_trade["current_size"] = POSITION_SIZE * (1 - PARTIAL_FRACTION)
                    open_trade["stop_level"] = entry  # trail to breakeven
                # Time stop
                if bars_held >= MAX_HOLD_BARS and exit_reason is None:
                    exit_reason = "time_stop"
                    exit_price = close * (1 - SLIPPAGE)

            elif side == "short":
                # Structural stop (above stop_level)
                if close > open_trade["stop_level"]:
                    exit_reason = "stop_short"
                    exit_price = close * (1 + SLIPPAGE)
                elif not partial_taken and r_unit and (entry - close) >= r_unit * PARTIAL_PROFIT_R:
                    open_trade["partial_taken"] = True
                    partial_ret = (entry - close * (1 + SLIPPAGE)) / entry
                    trade_log.append(_trade_row(
                        open_trade, today_date, close * (1 + SLIPPAGE),
                        "partial_profit_short", bars_held,
                        partial_ret, POSITION_SIZE * PARTIAL_FRACTION,
                    ))
                    open_trade["current_size"] = POSITION_SIZE * (1 - PARTIAL_FRACTION)
                    open_trade["stop_level"] = entry
                if bars_held >= MAX_HOLD_BARS and exit_reason is None:
                    exit_reason = "time_stop"
                    exit_price = close * (1 + SLIPPAGE)

            if exit_reason and exit_price:
                size = open_trade.get("current_size", POSITION_SIZE)
                if side == "long":
                    ret = (exit_price - entry) / entry
                else:
                    ret = (entry - exit_price) / entry
                trade_log.append(_trade_row(
                    open_trade, today_date, exit_price, exit_reason, bars_held, ret, size
                ))
                open_trade = None

        # -------------------------------------------------------------------
        # Check for new entry
        # -------------------------------------------------------------------
        if open_trade is None and d + 1 < n:
            next_bar = spy_bars[d + 1]

            if state == "entry_trigger_long" and not features.get("gap_up_open"):
                entry_price = next_bar.open * (1 + SLIPPAGE)
                stop_level = features.get("long_stop") or (
                    next_bar.open * (1 - (features.get("atr20") or entry_price * 0.01) / entry_price)
                )
                r_unit = features.get("r_unit") or features.get("atr20") or entry_price * 0.01
                open_trade = _new_trade("long", today_date, next_bar.date, entry_price, stop_level, r_unit)
                _record_fwd_returns(forward_returns, spy_bars, d, today_date, today.close, "long")

            elif state == "entry_trigger_short" and not features.get("gap_down_open"):
                entry_price = next_bar.open * (1 - SLIPPAGE)
                stop_level = features.get("short_stop") or (
                    next_bar.open * (1 + (features.get("atr20") or entry_price * 0.01) / entry_price)
                )
                r_unit = features.get("r_unit") or features.get("atr20") or entry_price * 0.01
                open_trade = _new_trade("short", today_date, next_bar.date, entry_price, stop_level, r_unit)
                _record_fwd_returns(forward_returns, spy_bars, d, today_date, today.close, "short")

    # Close any still-open position at the last bar
    if open_trade and spy_bars:
        last = spy_bars[-1]
        side = open_trade["side"]
        size = open_trade.get("current_size", POSITION_SIZE)
        if side == "long":
            exit_price = last.close * (1 - SLIPPAGE)
            ret = (exit_price - open_trade["entry_price"]) / open_trade["entry_price"]
        else:
            exit_price = last.close * (1 + SLIPPAGE)
            ret = (open_trade["entry_price"] - exit_price) / open_trade["entry_price"]
        trade_log.append(_trade_row(
            open_trade, last.date, exit_price, "end_of_data",
            open_trade["bars_held"], ret, size,
        ))

    return {"signal_log": signal_log, "trade_log": trade_log, "forward_returns": forward_returns}


def _new_trade(side, signal_date, entry_date, entry_price, stop_level, r_unit):
    return {
        "side": side,
        "signal_date": signal_date,
        "entry_date": entry_date,
        "entry_price": round(entry_price, 4),
        "stop_level": round(stop_level, 4),
        "r_unit": round(r_unit, 4),
        "bars_held": 0,
        "partial_taken": False,
        "current_size": POSITION_SIZE,
    }


def _trade_row(trade, exit_date, exit_price, exit_reason, bars_held, ret, size):
    return {
        "side": trade["side"],
        "signal_date": trade["signal_date"],
        "entry_date": trade["entry_date"],
        "entry_price": round(trade["entry_price"], 4),
        "exit_date": exit_date,
        "exit_price": round(exit_price, 4),
        "exit_reason": exit_reason,
        "bars_held": bars_held,
        "return_pct": round(ret * 100, 4),
        "position_size": round(size, 4),
        "weighted_return_pct": round(ret * 100 * size, 4),
    }


def _record_fwd_returns(fwd_list, spy_bars, d, date, close, side):
    row = {"signal_date": date, "side": side, "close_at_signal": round(close, 4)}
    n = len(spy_bars)
    for h in [5, 10, 20]:
        fi = d + h
        if fi < n:
            fwd_close = spy_bars[fi].close
            pct_change = (fwd_close - close) / close * 100
            # For shorts, a falling price is a positive outcome
            if side == "short":
                pct_change = -pct_change
            row[f"fwd_{h}d_return_pct"] = round(pct_change, 4)
        else:
            row[f"fwd_{h}d_return_pct"] = None
    fwd_list.append(row)


# ---------------------------------------------------------------------------
# Performance analytics
# ---------------------------------------------------------------------------

def compute_metrics(results: dict) -> dict:
    signal_log = results["signal_log"]
    trade_log = results["trade_log"]
    fwd_returns = results["forward_returns"]

    state_counts = Counter(r["state"] for r in signal_log)
    total_bars = len(signal_log)

    def _fwd_stats(side_filter: Optional[str] = None):
        rows = fwd_returns if side_filter is None else [r for r in fwd_returns if r["side"] == side_filter]
        out = {}
        for h in [5, 10, 20]:
            vals = [r[f"fwd_{h}d_return_pct"] for r in rows if r.get(f"fwd_{h}d_return_pct") is not None]
            if vals:
                arr = np.array(vals)
                out[f"{h}d"] = {
                    "n": len(vals),
                    "mean": round(float(arr.mean()), 3),
                    "median": round(float(np.median(arr)), 3),
                    "std": round(float(arr.std()), 3),
                    "win_rate_pct": round(float((arr > 3.0).mean() * 100), 1),
                    "loss_rate_pct": round(float((arr < -3.0).mean() * 100), 1),
                    "best": round(float(arr.max()), 3),
                    "worst": round(float(arr.min()), 3),
                }
            else:
                out[f"{h}d"] = {"n": 0}
        return out

    def _trade_stats(trades: list) -> dict:
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
            per_bar_rets = np.array([
                t["return_pct"] / max(t["bars_held"], 1) for t in trades
            ])
            sharpe = float(per_bar_rets.mean() / (per_bar_rets.std() + 1e-9) * (252 ** 0.5))
            stats["sharpe_ratio"] = round(sharpe, 3)
        equity = [1.0]
        for t in trades:
            prev = equity[-1]
            equity.append(prev * (1 + t["return_pct"] / 100 * t.get("position_size", POSITION_SIZE)))
        eq_arr = np.array(equity)
        roll_max = np.maximum.accumulate(eq_arr)
        dd = (eq_arr - roll_max) / (roll_max + 1e-9)
        stats["max_drawdown_pct"] = round(float(dd.min() * 100), 3)
        return stats

    long_trades = [t for t in trade_log if t["side"] == "long" and t["exit_reason"] != "partial_profit_long"]
    short_trades = [t for t in trade_log if t["side"] == "short" and t["exit_reason"] != "partial_profit_short"]
    all_full_trades = long_trades + short_trades

    # Episode hit-check
    def _episode_hits(episodes: dict, trigger_state_prefix: str) -> dict:
        hits = {}
        for name, (ep_start, ep_end) in episodes.items():
            ep_signals = [
                r for r in signal_log
                if ep_start <= r["date"] <= ep_end
            ]
            entry_triggers = [
                r for r in ep_signals
                if r["state"].startswith(trigger_state_prefix)
            ]
            candidates = [r for r in ep_signals if r["state"] == "breakout_candidate"]
            hits[name] = {
                "period": f"{ep_start} – {ep_end}",
                "entry_trigger_days": len(entry_triggers),
                "candidate_days": len(candidates),
                "stress_detected": len(entry_triggers) + len(candidates) > 0,
                "entry_triggered": len(entry_triggers) > 0,
            }
        return hits

    long_episode_hits = _episode_hits(LONG_EPISODES, "entry_trigger_long")
    short_episode_hits = _episode_hits(SHORT_EPISODES, "entry_trigger_short")

    # In-sample / out-of-sample split
    is_cutoff = "2019-12-31"
    is_trades_long = [t for t in long_trades if t.get("exit_date", "") <= is_cutoff]
    oos_trades_long = [t for t in long_trades if t.get("exit_date", "") > is_cutoff]
    is_trades_short = [t for t in short_trades if t.get("exit_date", "") <= is_cutoff]
    oos_trades_short = [t for t in short_trades if t.get("exit_date", "") > is_cutoff]

    return {
        "signal_frequency": {
            state: {"count": count, "pct": round(count / total_bars * 100, 1) if total_bars else 0}
            for state, count in state_counts.most_common()
        },
        "total_bars_evaluated": total_bars,
        "forward_returns_long": _fwd_stats("long"),
        "forward_returns_short": _fwd_stats("short"),
        "trade_stats_long": _trade_stats(long_trades),
        "trade_stats_short": _trade_stats(short_trades),
        "trade_stats_all": _trade_stats(all_full_trades),
        "long_episode_hits": long_episode_hits,
        "short_episode_hits": short_episode_hits,
        "in_sample_long": _trade_stats(is_trades_long),
        "out_of_sample_long": _trade_stats(oos_trades_long),
        "in_sample_short": _trade_stats(is_trades_short),
        "out_of_sample_short": _trade_stats(oos_trades_short),
    }


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def write_results(results: dict, metrics: dict) -> None:
    _write_csv(
        os.path.join(RESULTS_DIR, "signal_history.csv"),
        results["signal_log"],
    )
    _write_csv(
        os.path.join(RESULTS_DIR, "trades.csv"),
        results["trade_log"],
    )
    _write_csv(
        os.path.join(RESULTS_DIR, "forward_returns.csv"),
        results["forward_returns"],
    )
    _write_summary_md(metrics)


def _write_csv(path: str, rows: list) -> None:
    if not rows:
        print(f"  (skipped — no rows) {path}")
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"  → {path} ({len(rows)} rows)")


def _write_summary_md(metrics: dict) -> None:
    lines = [
        "# Backtest Results: PTJ Macro Breakout With Asymmetric Risk",
        "",
        f"**Run date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Backtest period:** {START_DATE} – {END_DATE}  "
        "(walk-forward, entry at next bar open)",
        "**Instruments:** SPY (primary), TLT (rates), UUP (dollar), HYG (credit)",
        f"**Slippage:** {SLIPPAGE * 100:.2f}% each way | "
        f"**Position size:** {POSITION_SIZE} unit | "
        f"**Max hold:** {MAX_HOLD_BARS} bars | "
        f"**Partial profit at:** {PARTIAL_PROFIT_R}R",
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
        "## Forward Returns After Entry Triggers",
        "",
        "*(Returns adjusted for direction: positive = profitable outcome)*",
        "",
    ]

    for side, label in [("long", "Long (Bullish Breakout)"), ("short", "Short (Bearish Breakdown)")]:
        key = f"forward_returns_{side}"
        fwd = metrics.get(key, {})
        lines += [
            f"### {label}",
            "",
            "| Horizon | N | Mean | Median | Std | Win >3% | Loss >3% | Best | Worst |",
            "|---------|---|------|--------|-----|---------|----------|------|-------|",
        ]
        for h in ["5d", "10d", "20d"]:
            s = fwd.get(h, {})
            if s.get("n"):
                lines.append(
                    f"| {h} | {s['n']} | {s['mean']}% | {s['median']}% | "
                    f"{s['std']}% | {s['win_rate_pct']}% | {s['loss_rate_pct']}% | "
                    f"{s['best']}% | {s['worst']}% |"
                )
            else:
                lines.append(f"| {h} | 0 | — | — | — | — | — | — | — |")
        lines.append("")

    lines += [
        "---",
        "",
        "## Trade Statistics",
        "",
    ]

    for side, label, stats_key in [
        ("long", "Long Trades", "trade_stats_long"),
        ("short", "Short Trades", "trade_stats_short"),
        ("all", "All Full Trades (Combined)", "trade_stats_all"),
    ]:
        ts = metrics.get(stats_key, {})
        lines += [f"### {label}", ""]
        if ts:
            lines += [
                f"- **Total trades:** {ts.get('total_trades', 0)}",
                f"- **Win rate:** {ts.get('win_rate_pct', 0)}%",
                f"- **Average return:** {ts.get('avg_return_pct', 0)}%",
                f"- **Median return:** {ts.get('median_return_pct', 0)}%",
                f"- **Best trade:** {ts.get('best_trade_pct', 0)}%",
                f"- **Worst trade:** {ts.get('worst_trade_pct', 0)}%",
                f"- **Average hold:** {ts.get('avg_bars_held', 0)} bars",
                f"- **Sharpe ratio (annualised):** {ts.get('sharpe_ratio', 'N/A')}",
                f"- **Max drawdown:** {ts.get('max_drawdown_pct', 0)}%",
                "",
            ]
        else:
            lines += ["*No completed trades.*", ""]

    lines += [
        "---",
        "",
        "## In-Sample (2015–2019) vs Out-of-Sample (2020–2025) Split",
        "",
        "| Period | Side | Trades | Win Rate | Avg Return | Sharpe |",
        "|--------|------|--------|----------|------------|--------|",
    ]
    for period_key, period_label in [
        ("in_sample", "2015–2019 (IS)"),
        ("out_of_sample", "2020–2025 (OOS)"),
    ]:
        for side in ["long", "short"]:
            ts = metrics.get(f"{period_key}_{side}", {})
            if ts:
                lines.append(
                    f"| {period_label} | {side} | {ts.get('total_trades', 0)} | "
                    f"{ts.get('win_rate_pct', 0)}% | {ts.get('avg_return_pct', 0)}% | "
                    f"{ts.get('sharpe_ratio', 'N/A')} |"
                )
            else:
                lines.append(f"| {period_label} | {side} | 0 | — | — | — |")

    lines += [
        "",
        "---",
        "",
        "## Known Episode Coverage",
        "",
        "### Long Breakout Episodes (Bullish)",
        "",
        "| Episode | Period | Entry Triggers | Candidates | Stress? | Entry Triggered? |",
        "|---------|--------|----------------|-----------|---------|-----------------|",
    ]
    for ep, v in metrics.get("long_episode_hits", {}).items():
        stress = "YES" if v["stress_detected"] else "NO"
        triggered = "YES" if v["entry_triggered"] else "NO"
        lines.append(
            f"| {ep} | {v['period']} | {v['entry_trigger_days']} | "
            f"{v['candidate_days']} | {stress} | {triggered} |"
        )

    lines += [
        "",
        "### Short Breakdown Episodes (Bearish)",
        "",
        "| Episode | Period | Entry Triggers | Candidates | Stress? | Entry Triggered? |",
        "|---------|--------|----------------|-----------|---------|-----------------|",
    ]
    for ep, v in metrics.get("short_episode_hits", {}).items():
        stress = "YES" if v["stress_detected"] else "NO"
        triggered = "YES" if v["entry_triggered"] else "NO"
        lines.append(
            f"| {ep} | {v['period']} | {v['entry_trigger_days']} | "
            f"{v['candidate_days']} | {stress} | {triggered} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Pitfalls Checklist",
        "",
        "- [x] No look-ahead bias (entry at next bar open; indicators on bars[0..d] only)",
        "- [x] Adjusted close prices (dividends + splits via Yahoo Finance)",
        "- [x] No parameter optimisation (strategy defaults only)",
        "- [x] Slippage included (0.05% each way on both entry and exit)",
        "- [x] Gap exclusion enforced (no long on gap-up; no short on gap-down)",
        "- [x] Partial profit at 2.5R reduces position, trails stop to breakeven",
        "- [x] Fixed time-stop at 20 bars prevents indefinite drawdown",
        "- [x] Both long and short directions evaluated separately",
        "- [x] In-sample / out-of-sample split reported",
        "- [x] Known macro episodes checked for signal coverage",
        "",
        "## Files in This Folder",
        "",
        "| File | Description |",
        "|------|-------------|",
        "| `backtest_ptj_macro_breakout.py` | Backtest engine |",
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
    print("PTJ Macro Breakout With Asymmetric Risk — Backtest")
    print(f"Period: {START_DATE} – {END_DATE}")
    print("=" * 60)

    print("\n[1/4] Downloading historical data...")
    spy_df = _yf_fetch("SPY", START_DATE, END_DATE)
    print(f"  SPY: {len(spy_df)} bars ({spy_df['date'].iloc[0].date()} – {spy_df['date'].iloc[-1].date()})")
    time.sleep(0.5)

    tlt_df = _yf_fetch("TLT", START_DATE, END_DATE)
    print(f"  TLT: {len(tlt_df)} bars")
    time.sleep(0.5)

    uup_df = _yf_fetch("UUP", START_DATE, END_DATE)
    print(f"  UUP: {len(uup_df)} bars")
    time.sleep(0.5)

    hyg_df = _yf_fetch("HYG", START_DATE, END_DATE)
    print(f"  HYG: {len(hyg_df)} bars")

    spy_bars = _df_to_bars(spy_df)
    tlt_bars = _df_to_bars(tlt_df)
    uup_bars = _df_to_bars(uup_df)
    hyg_bars = _df_to_bars(hyg_df)

    print(f"\n[2/4] Running walk-forward backtest ({len(spy_bars)} bars)...")
    results = run_backtest(spy_bars, tlt_bars, uup_bars, hyg_bars)
    print(f"  Signal bars evaluated: {len(results['signal_log'])}")
    long_entries = sum(1 for r in results["forward_returns"] if r["side"] == "long")
    short_entries = sum(1 for r in results["forward_returns"] if r["side"] == "short")
    print(f"  Entry triggers: {long_entries} long, {short_entries} short")
    full_trades = [t for t in results["trade_log"] if "partial_profit" not in t["exit_reason"]]
    print(f"  Completed full trades: {len(full_trades)}")

    print("\n[3/4] Computing performance metrics...")
    metrics = compute_metrics(results)

    print("\n[4/4] Writing results to ./results/...")
    write_results(results, metrics)

    # Terminal summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    print("\nSignal frequency (all states):")
    for state, v in metrics["signal_frequency"].items():
        print(f"  {state:40s}  {v['count']:5d}  ({v['pct']}%)")

    for side in ["long", "short"]:
        ts = metrics.get(f"trade_stats_{side}", {})
        if ts:
            print(f"\n{side.upper()} trade stats:")
            print(f"  Trades:    {ts.get('total_trades', 0)}")
            print(f"  Win rate:  {ts.get('win_rate_pct', 0)}%")
            print(f"  Avg ret:   {ts.get('avg_return_pct', 0):+.3f}%")
            print(f"  Sharpe:    {ts.get('sharpe_ratio', 'N/A')}")
            print(f"  Max DD:    {ts.get('max_drawdown_pct', 0):.2f}%")

    print("\nEpisode coverage — LONG:")
    for ep, v in metrics["long_episode_hits"].items():
        tag = "ENTRY" if v["entry_triggered"] else ("CANDIDATE" if v["stress_detected"] else "MISS")
        print(f"  [{tag:9s}] {ep}: {v['entry_trigger_days']} trigger days")

    print("\nEpisode coverage — SHORT:")
    for ep, v in metrics["short_episode_hits"].items():
        tag = "ENTRY" if v["entry_triggered"] else ("CANDIDATE" if v["stress_detected"] else "MISS")
        print(f"  [{tag:9s}] {ep}: {v['entry_trigger_days']} trigger days")

    print("\nDone.")


if __name__ == "__main__":
    main()
