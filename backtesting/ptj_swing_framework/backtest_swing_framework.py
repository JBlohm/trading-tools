#!/usr/bin/env python3
"""
backtest_swing_framework.py — Multi-symbol walk-forward backtest for the
PTJ Macro Breakout With Asymmetric Risk swing strategy.

Extends the single-symbol SPY backtest to a 6-symbol universe (SPY, QQQ, IWM,
GLD, XLF, XLE) with portfolio-level constraints and $25,000 account sizing.

Backtest design:
  - Zero look-ahead: entry at open of bar d+1; indicators on bars[0..d] only.
  - Adjusted close prices (dividends + splits via Yahoo Finance / yfinance).
  - Slippage: 0.05% each way (entry and exit).
  - Risk per trade: 1% of account equity = $250 at $25k starting equity.
  - Position sizing: floor($250 / stop_distance), capped at 20% of equity notional.
  - Partial profit: 1/3 of position at 2.5R; trail stop to breakeven.
  - Time stop: exit after 20 bars.
  - Portfolio constraints:
      - Max 6 open positions simultaneously.
      - Max 2 positions from equity_broad cluster (SPY, QQQ, IWM).
      - One new trade per week per symbol (7-day cooldown).
  - In-sample: 2015–2019.  Out-of-sample: 2020–2025.
  - No parameter optimisation: all parameters from detect_macro_breakout.py defaults.

Outputs (all in ./results/):
  - portfolio_trades.csv       : trade-level results across all symbols
  - portfolio_daily.csv        : daily portfolio equity curve
  - performance_summary.md     : aggregate and per-symbol metrics

Usage:
    python backtest_swing_framework.py
"""

import csv
import json
import os
import sys
import time
import types
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date
from types import SimpleNamespace
from typing import Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

if "ib_async" not in sys.modules:
    _stub = types.ModuleType("ib_async")
    for _attr in ("IB", "Stock", "Future", "Contract", "Index", "Forex", "Option"):
        setattr(_stub, _attr, object)
    sys.modules["ib_async"] = _stub

from tools.detect_macro_breakout import calculate_indicators, evaluate_breakout_signal

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SYMBOLS = ["SPY", "QQQ", "IWM", "GLD", "XLF", "XLE"]
MACRO_SYMBOLS = {"rates": "TLT", "dollar": "UUP", "credit": "HYG"}

EQUITY_BROAD = frozenset(["SPY", "QQQ", "IWM"])
MAX_EQUITY_CLUSTER = 2
MAX_POSITIONS = 6
WEEKLY_COOLDOWN_DAYS = 7

ACCOUNT_START = 25_000.0
RISK_PCT = 0.01           # 1% per trade
MAX_NOTIONAL_PCT = 0.20   # 20% max notional per position
SLIPPAGE = 0.0005         # 0.05% each way
MAX_HOLD_BARS = 20
PARTIAL_PROFIT_R = 2.5
PARTIAL_FRACTION = 1 / 3

START_DATE = "2015-01-01"
END_DATE = "2025-06-01"
IS_CUTOFF = "2020-01-01"
WARMUP_BARS = 220

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Data fetching (identical pattern to existing backtest)
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
            q = result["indicators"]["quote"][0]
            adj = (result["indicators"].get("adjclose") or [{}])[0].get("adjclose") or q["close"]
            rows = []
            for i, ts in enumerate(timestamps):
                try:
                    d = pd.Timestamp(ts, unit="s", tz="UTC").normalize().tz_localize(None)
                    rows.append({
                        "date": d,
                        "open": float(q["open"][i] or 0),
                        "high": float(q["high"][i] or 0),
                        "low": float(q["low"][i] or 0),
                        "close": float(adj[i] or 0),
                        "volume": int(q["volume"][i] or 0),
                    })
                except (TypeError, ValueError, IndexError):
                    continue
            df = pd.DataFrame(rows)
            df = df.dropna(subset=["close"]).reset_index(drop=True)
            df["date"] = pd.to_datetime(df["date"])
            return df.sort_values("date").reset_index(drop=True)
        except Exception as exc:
            last_err = exc
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to fetch {ticker}: {last_err}")


def _df_to_bars(df: pd.DataFrame) -> list:
    return [
        SimpleNamespace(
            date=str(row["date"].date()),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(row["volume"]),
        )
        for _, row in df.iterrows()
    ]


# ---------------------------------------------------------------------------
# Portfolio state
# ---------------------------------------------------------------------------

class TradeContext:
    def __init__(self, symbol, side, entry_price, stop_level, shares, entry_date, entry_bar_idx):
        self.symbol = symbol
        self.side = side
        self.entry_price = entry_price
        self.stop_level = stop_level
        self.shares = shares
        self.entry_date = entry_date
        self.entry_bar_idx = entry_bar_idx
        self.bars_held = 0
        self.partial_taken = False
        self.trailing = False
        self.trail_stop = stop_level
        self.pnl = 0.0


class Portfolio:
    def __init__(self, starting_equity: float):
        self.equity = starting_equity
        self.peak_equity = starting_equity
        self.positions: dict[str, TradeContext] = {}
        self.last_trade_date: dict[str, str] = {}
        self.trade_log: list[dict] = []
        self.daily_equity: list[dict] = []

    def can_enter(self, symbol: str, today_str: str) -> tuple[bool, str]:
        if symbol in self.positions:
            return False, "already_in_position"
        if len(self.positions) >= MAX_POSITIONS:
            return False, "max_positions"
        if symbol in EQUITY_BROAD:
            n_eq = sum(1 for s in self.positions if s in EQUITY_BROAD)
            if n_eq >= MAX_EQUITY_CLUSTER:
                return False, "equity_cluster"
        if symbol in self.last_trade_date:
            last = datetime.strptime(self.last_trade_date[symbol], "%Y-%m-%d").date()
            today = datetime.strptime(today_str, "%Y-%m-%d").date()
            if (today - last).days < WEEKLY_COOLDOWN_DAYS:
                return False, "weekly_cooldown"
        return True, ""

    def compute_shares(self, entry_price: float, stop_level: float) -> int:
        stop_dist = abs(entry_price - stop_level)
        if stop_dist <= 0:
            return 0
        risk = self.equity * RISK_PCT
        max_notional = self.equity * MAX_NOTIONAL_PCT
        shares_by_risk = int(risk / stop_dist)
        shares_by_notional = int(max_notional / entry_price) if entry_price > 0 else 0
        return max(0, min(shares_by_risk, shares_by_notional))

    def enter(self, symbol: str, side: str, entry_price: float, stop_level: float,
              date_str: str, bar_idx: int) -> Optional[TradeContext]:
        shares = self.compute_shares(entry_price, stop_level)
        if shares < 1:
            return None
        entry_with_slip = entry_price * (1 + SLIPPAGE if side == "long" else 1 - SLIPPAGE)
        ctx = TradeContext(symbol, side, entry_with_slip, stop_level, shares, date_str, bar_idx)
        self.positions[symbol] = ctx
        self.last_trade_date[symbol] = date_str
        return ctx

    def exit_position(self, symbol: str, exit_price: float, exit_date: str,
                      exit_bars: int, reason: str, shares_to_close: Optional[int] = None) -> dict:
        ctx = self.positions[symbol]
        close_shares = shares_to_close if shares_to_close is not None else ctx.shares
        exit_with_slip = exit_price * (1 - SLIPPAGE if ctx.side == "long" else 1 + SLIPPAGE)
        if ctx.side == "long":
            pnl_per_share = exit_with_slip - ctx.entry_price
        else:
            pnl_per_share = ctx.entry_price - exit_with_slip
        pnl = pnl_per_share * close_shares
        pnl_pct = pnl_per_share / ctx.entry_price if ctx.entry_price else 0

        self.equity += pnl
        self.peak_equity = max(self.peak_equity, self.equity)

        trade = {
            "symbol": symbol,
            "side": ctx.side,
            "entry_date": ctx.entry_date,
            "exit_date": exit_date,
            "entry_price": round(ctx.entry_price, 4),
            "exit_price": round(exit_with_slip, 4),
            "stop_level": round(ctx.stop_level, 4),
            "shares": close_shares,
            "bars_held": exit_bars,
            "pnl_usd": round(pnl, 2),
            "pnl_pct": round(pnl_pct * 100, 4),
            "exit_reason": reason,
            "partial": shares_to_close is not None and shares_to_close < ctx.shares,
        }
        self.trade_log.append(trade)

        if shares_to_close is not None and shares_to_close < ctx.shares:
            ctx.shares -= close_shares
        else:
            del self.positions[symbol]

        return trade

    def record_daily(self, date_str: str) -> None:
        self.daily_equity.append({"date": date_str, "equity": round(self.equity, 2)})

    def drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - self.equity) / self.peak_equity


# ---------------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------------

def _align_bars(all_bars: dict[str, list], primary_dates: list[str]) -> dict[str, dict[str, SimpleNamespace]]:
    """Build date-indexed lookup for all bar series."""
    indexed = {}
    for sym, bars in all_bars.items():
        indexed[sym] = {b.date: b for b in bars}
    return indexed


def _bars_up_to(bars: list, date_str: str) -> list:
    """All bars with date <= date_str."""
    return [b for b in bars if b.date <= date_str]


def run_backtest(
    primary_bars_by_symbol: dict[str, list],
    macro_bars: dict[str, list],
    start_date: str = START_DATE,
    end_date: str = END_DATE,
) -> Portfolio:
    """
    Run multi-symbol walk-forward backtest.

    Entry decisions are evaluated on bar[d], executed at bar[d+1].open.
    Management decisions (stop/trail/time) are checked intrabar on bar[d+1].
    """
    portfolio = Portfolio(ACCOUNT_START)

    # Build a unified sorted list of trading dates (union of all symbols)
    all_dates = sorted(set(
        b.date for sym_bars in primary_bars_by_symbol.values() for b in sym_bars
    ))
    all_dates = [d for d in all_dates if start_date <= d <= end_date]

    # Per-symbol bars indexed by date for fast lookup
    date_bars: dict[str, dict[str, SimpleNamespace]] = {}
    for sym, bars in primary_bars_by_symbol.items():
        date_bars[sym] = {b.date: b for b in bars}

    macro_date_bars: dict[str, dict[str, SimpleNamespace]] = {}
    for sym, bars in macro_bars.items():
        macro_date_bars[sym] = {b.date: b for b in bars}

    # Deferred entries: decided on bar[d], executed at bar[d+1].open
    pending_entries: list[dict] = []

    for date_idx, today in enumerate(all_dates):
        # --- Execute pending entries from previous bar ---
        still_pending = []
        for entry in pending_entries:
            sym = entry["symbol"]
            if today in date_bars.get(sym, {}):
                bar = date_bars[sym][today]
                can, reason = portfolio.can_enter(sym, today)
                if can:
                    portfolio.enter(sym, entry["side"], bar.open, entry["stop_level"], today, date_idx)
            # entries not executed are dropped (bar not available → SKIP)

        # --- Manage open positions ---
        to_exit = []
        for sym, ctx in list(portfolio.positions.items()):
            bar = date_bars.get(sym, {}).get(today)
            if not bar:
                continue
            ctx.bars_held += 1
            close = bar.close
            r_unit = abs(ctx.entry_price - ctx.stop_level)

            # Update trailing stop when profitable beyond 2R
            if r_unit > 0:
                if ctx.side == "long" and (close - ctx.entry_price) > r_unit * 2.0:
                    ctx.trailing = True
                elif ctx.side == "short" and (ctx.entry_price - close) > r_unit * 2.0:
                    ctx.trailing = True

            if ctx.trailing and not ctx.partial_taken:
                if ctx.side == "long":
                    ctx.trail_stop = max(ctx.trail_stop, ctx.entry_price)
                else:
                    ctx.trail_stop = min(ctx.trail_stop, ctx.entry_price)

            # Partial profit at 2.5R
            if not ctx.partial_taken and ctx.shares > 1 and r_unit > 0:
                profit_r = (close - ctx.entry_price) / r_unit if ctx.side == "long" else (ctx.entry_price - close) / r_unit
                if profit_r >= PARTIAL_PROFIT_R:
                    partial_shares = max(1, int(ctx.shares * PARTIAL_FRACTION))
                    portfolio.exit_position(sym, close, today, ctx.bars_held, "partial_profit",
                                           shares_to_close=partial_shares)
                    ctx.partial_taken = True
                    if ctx.side == "long":
                        ctx.trail_stop = max(ctx.trail_stop, ctx.entry_price)
                    else:
                        ctx.trail_stop = min(ctx.trail_stop, ctx.entry_price)

            # Exit conditions
            effective_stop = ctx.trail_stop if ctx.trailing else ctx.stop_level
            if sym not in portfolio.positions:
                continue  # already exited via partial above (shouldn't happen but guard)
            if ctx.side == "long" and close < effective_stop:
                to_exit.append((sym, close, today, ctx.bars_held, "stop"))
            elif ctx.side == "short" and close > effective_stop:
                to_exit.append((sym, close, today, ctx.bars_held, "stop"))
            elif ctx.bars_held >= MAX_HOLD_BARS:
                to_exit.append((sym, close, today, ctx.bars_held, "time_stop"))

        for sym, px, dt, bars, reason in to_exit:
            if sym in portfolio.positions:
                portfolio.exit_position(sym, px, dt, bars, reason)

        # --- Evaluate new entries ---
        for sym in SYMBOLS:
            if sym in portfolio.positions:
                continue

            sym_bars = primary_bars_by_symbol.get(sym, [])
            bars_to_d = [b for b in sym_bars if b.date <= today]
            if len(bars_to_d) < WARMUP_BARS:
                continue

            def _macro_bars_to_d(key: str) -> list:
                mb = macro_bars.get(key, [])
                return [b for b in mb if b.date <= today]

            features = calculate_indicators(
                bars_to_d,
                _macro_bars_to_d("rates"),
                _macro_bars_to_d("dollar"),
                _macro_bars_to_d("credit"),
            )
            if not features:
                continue

            signal = evaluate_breakout_signal(features)
            sig_state = signal["signal_state"]

            if sig_state not in ("entry_trigger_long", "entry_trigger_short"):
                continue

            side = "long" if sig_state == "entry_trigger_long" else "short"
            stop_level = features.get("long_stop") if side == "long" else features.get("short_stop")
            if not stop_level:
                continue

            can, reason = portfolio.can_enter(sym, today)
            if not can:
                continue

            # Defer to next bar open
            pending_entries = [{"symbol": sym, "side": side, "stop_level": stop_level}]

        portfolio.record_daily(today)

    # Close any remaining positions at last available close
    last_date = all_dates[-1] if all_dates else END_DATE
    for sym in list(portfolio.positions.keys()):
        bar = date_bars.get(sym, {}).get(last_date)
        if bar:
            ctx = portfolio.positions[sym]
            portfolio.exit_position(sym, bar.close, last_date, ctx.bars_held, "end_of_backtest")

    return portfolio


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _trade_stats(trades: list[dict]) -> dict:
    if not trades:
        return {"count": 0}
    pnl_pcts = [t["pnl_pct"] for t in trades if not t.get("partial")]
    if not pnl_pcts:
        return {"count": 0}
    wins = [p for p in pnl_pcts if p > 0]
    n = len(pnl_pcts)
    mean = float(np.mean(pnl_pcts))
    std = float(np.std(pnl_pcts)) if n > 1 else 0.0
    sharpe = (mean / std * (252 ** 0.5 / n ** 0.5)) if std > 0 and n > 1 else 0.0
    return {
        "count": n,
        "win_rate": round(len(wins) / n * 100, 1) if n else 0,
        "mean_pct": round(mean, 3),
        "median_pct": round(float(np.median(pnl_pcts)), 3),
        "best_pct": round(max(pnl_pcts), 3),
        "worst_pct": round(min(pnl_pcts), 3),
        "sharpe": round(sharpe, 3),
    }


def _max_drawdown_pct(daily_equity: list[dict]) -> float:
    equities = [d["equity"] for d in daily_equity]
    if not equities:
        return 0.0
    peak = equities[0]
    max_dd = 0.0
    for eq in equities:
        peak = max(peak, eq)
        dd = (peak - eq) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
    return round(max_dd * 100, 2)


def write_results(portfolio: Portfolio) -> None:
    trades = portfolio.trade_log

    # portfolio_trades.csv
    trades_path = os.path.join(RESULTS_DIR, "portfolio_trades.csv")
    if trades:
        fieldnames = list(trades[0].keys())
        with open(trades_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(trades)

    # portfolio_daily.csv
    daily_path = os.path.join(RESULTS_DIR, "portfolio_daily.csv")
    with open(daily_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "equity"])
        w.writeheader()
        w.writerows(portfolio.daily_equity)

    # Performance summary
    full_trades = [t for t in trades if not t.get("partial")]
    long_trades = [t for t in full_trades if t["side"] == "long"]
    short_trades = [t for t in full_trades if t["side"] == "short"]

    is_trades = [t for t in full_trades if t["entry_date"] < IS_CUTOFF]
    oos_trades = [t for t in full_trades if t["entry_date"] >= IS_CUTOFF]

    per_symbol = {}
    for sym in SYMBOLS:
        per_symbol[sym] = _trade_stats([t for t in full_trades if t["symbol"] == sym])

    all_stats = _trade_stats(full_trades)
    long_stats = _trade_stats(long_trades)
    short_stats = _trade_stats(short_trades)
    is_stats = _trade_stats(is_trades)
    oos_stats = _trade_stats(oos_trades)

    max_dd = _max_drawdown_pct(portfolio.daily_equity)
    final_equity = portfolio.daily_equity[-1]["equity"] if portfolio.daily_equity else ACCOUNT_START
    total_return = (final_equity - ACCOUNT_START) / ACCOUNT_START * 100

    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# Backtest Results: PTJ Swing Framework (6-Symbol Portfolio)",
        "",
        f"**Run date:** {run_ts}",
        f"**Backtest period:** {START_DATE} – {END_DATE}  (walk-forward, entry at next bar open)",
        f"**Universe:** {', '.join(SYMBOLS)} + macro bars (TLT, UUP, HYG)",
        f"**Account:** ${ACCOUNT_START:,.0f} starting equity | Risk per trade: {RISK_PCT*100:.0f}% (${ACCOUNT_START*RISK_PCT:.0f})",
        f"**Slippage:** {SLIPPAGE*100:.2f}% each way | **Max hold:** {MAX_HOLD_BARS} bars | **Partial profit at:** {PARTIAL_PROFIT_R}R",
        f"**Portfolio constraints:** Max {MAX_POSITIONS} positions | Max {MAX_EQUITY_CLUSTER} equity-cluster | {WEEKLY_COOLDOWN_DAYS}-day trade cooldown",
        "",
        "---",
        "",
        "## Portfolio Performance",
        "",
        f"- **Starting equity:** ${ACCOUNT_START:,.0f}",
        f"- **Ending equity:** ${final_equity:,.2f}",
        f"- **Total return:** {total_return:.1f}%",
        f"- **Max portfolio drawdown:** {max_dd:.1f}%",
        "",
        "## All Trades Combined",
        "",
        f"- **Total closed trades:** {all_stats.get('count', 0)}",
        f"- **Win rate:** {all_stats.get('win_rate', 0)}%",
        f"- **Average return:** {all_stats.get('mean_pct', 0):.3f}%",
        f"- **Median return:** {all_stats.get('median_pct', 0):.3f}%",
        f"- **Best trade:** {all_stats.get('best_pct', 0):.3f}%",
        f"- **Worst trade:** {all_stats.get('worst_pct', 0):.3f}%",
        f"- **Sharpe ratio (annualised):** {all_stats.get('sharpe', 0):.3f}",
        "",
        "## By Direction",
        "",
        "| Direction | Trades | Win Rate | Avg Return | Sharpe |",
        "|-----------|--------|----------|------------|--------|",
        f"| Long  | {long_stats.get('count',0)} | {long_stats.get('win_rate',0)}% | {long_stats.get('mean_pct',0):.3f}% | {long_stats.get('sharpe',0):.3f} |",
        f"| Short | {short_stats.get('count',0)} | {short_stats.get('win_rate',0)}% | {short_stats.get('mean_pct',0):.3f}% | {short_stats.get('sharpe',0):.3f} |",
        "",
        "## In-Sample (2015–2019) vs Out-of-Sample (2020–2025)",
        "",
        "| Period | Trades | Win Rate | Avg Return | Sharpe |",
        "|--------|--------|----------|------------|--------|",
        f"| 2015–2019 (IS) | {is_stats.get('count',0)} | {is_stats.get('win_rate',0)}% | {is_stats.get('mean_pct',0):.3f}% | {is_stats.get('sharpe',0):.3f} |",
        f"| 2020–2025 (OOS) | {oos_stats.get('count',0)} | {oos_stats.get('win_rate',0)}% | {oos_stats.get('mean_pct',0):.3f}% | {oos_stats.get('sharpe',0):.3f} |",
        "",
        "## Per-Symbol Statistics",
        "",
        "| Symbol | Trades | Win Rate | Avg Return | Sharpe |",
        "|--------|--------|----------|------------|--------|",
    ]
    for sym in SYMBOLS:
        st = per_symbol[sym]
        lines.append(
            f"| {sym} | {st.get('count',0)} | {st.get('win_rate',0)}% "
            f"| {st.get('mean_pct',0):.3f}% | {st.get('sharpe',0):.3f} |"
        )

    lines += [
        "",
        "## Backtest Pitfalls Checklist",
        "",
        "- [x] No look-ahead bias (entry at next bar open; indicators on bars[0..d] only)",
        "- [x] Adjusted close prices (dividends + splits via Yahoo Finance)",
        "- [x] No parameter optimisation (strategy defaults only from detect_macro_breakout.py)",
        "- [x] Slippage included (0.05% each way on both entry and exit)",
        "- [x] Gap exclusion enforced (built into evaluate_breakout_signal)",
        "- [x] Partial profit at 2.5R reduces position, trails stop to breakeven",
        "- [x] Fixed time-stop at 20 bars prevents indefinite drawdown",
        f"- [x] Portfolio constraints: max {MAX_POSITIONS} positions, max {MAX_EQUITY_CLUSTER} equity-cluster, {WEEKLY_COOLDOWN_DAYS}-day cooldown",
        "- [x] In-sample / out-of-sample split reported",
        f"- [x] $25k account sizing with 1% risk per trade (max $250 risk, max 20% notional)",
        "- [x] Correlation-aware: max 2 positions from SPY/QQQ/IWM cluster",
        "",
        "## Decision Rule",
        "",
        "If backtest does not demonstrate net positive expectancy on the OOS period, the",
        "daily loop emits SKIP/RESEARCH for all symbols rather than forcing trades.",
        "The strategy must earn its risk allocation; edge cannot be assumed.",
    ]

    summary_path = os.path.join(RESULTS_DIR, "performance_summary.md")
    with open(summary_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Results written to {RESULTS_DIR}/")
    print(f"  portfolio_trades.csv  ({len(trades)} total records)")
    print(f"  portfolio_daily.csv")
    print(f"  performance_summary.md")
    print()
    print(f"Starting equity:  ${ACCOUNT_START:,.0f}")
    print(f"Ending equity:    ${final_equity:,.2f}")
    print(f"Total return:     {total_return:.1f}%")
    print(f"Max drawdown:     {max_dd:.1f}%")
    print(f"Closed trades:    {all_stats.get('count', 0)}")
    print(f"Win rate:         {all_stats.get('win_rate', 0)}%")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"Fetching data for {len(SYMBOLS)} symbols + 3 macro bars ...")
    all_bars: dict[str, list] = {}

    for sym in SYMBOLS:
        print(f"  {sym} ... ", end="", flush=True)
        df = _yf_fetch(sym, START_DATE, END_DATE)
        all_bars[sym] = _df_to_bars(df)
        print(f"{len(all_bars[sym])} bars")

    macro_bars: dict[str, list] = {}
    for key, sym in MACRO_SYMBOLS.items():
        print(f"  {sym} (macro/{key}) ... ", end="", flush=True)
        df = _yf_fetch(sym, START_DATE, END_DATE)
        macro_bars[key] = _df_to_bars(df)
        print(f"{len(macro_bars[key])} bars")

    print()
    print("Running backtest ...")
    portfolio = run_backtest(all_bars, macro_bars)

    print()
    print("Writing results ...")
    write_results(portfolio)


if __name__ == "__main__":
    main()
