#!/usr/bin/env python3
"""
backtest_uso_vol_contraction_breakout.py

Public-data-only daily backtest for USO_VolContraction_Breakout_v1.

Design notes:
  - Long/cash only, one position at a time.
  - Entry is evaluated on the signal day close and executed at the next day's open.
  - Uses only public data sources (Yahoo Finance / yfinance fallback to Yahoo chart API).
  - No live or paper execution code is included here.
  - Artifacts are written into ./results/:
      * trade_ledger.csv
      * decision_log.csv
      * equity_curve.csv
      * drawdown_series.csv
      * benchmark_comparison.csv
      * performance_summary.md

The implementation is deliberately conservative. If data is missing or the regime
is too hot, the strategy stays flat. Survival first.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

START_DATE = "2015-01-01"
END_DATE = "2025-06-01"
STARTING_EQUITY = 25_000.0
MAX_RISK_NORMAL = 0.01
MAX_RISK_ELEVATED = 0.005
MAX_NOTIONAL_NORMAL = 0.20
MAX_NOTIONAL_ELEVATED = 0.10
MAX_HOLD_DAYS = 10
SLIPPAGE_PER_SHARE = 0.02
MIN_AVG_VOLUME = 5_000_000
MAX_GAP_PCT = 0.05
MAX_ATR_PCT = 0.08
SHOCK_ATR_PCT = 0.10
ATR_LOOKBACK = 14
ATR_PERCENTILE_LOOKBACK = 120
EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 200
EMA_SLOPE_LOOKBACK = 5
RSI_PERIOD = 3
SWING_LOOKBACK = 5
TARGET_R_MULTIPLE = 2.0
ELEVATED_VIX_LEVEL = 25.0
VIX_MAX = 30.0
VIX_HARD_STOP = 40.0

_FOMC_DATES = {
    "2015-01-28", "2015-03-18", "2015-04-29", "2015-06-17", "2015-07-29", "2015-09-17", "2015-10-28", "2015-12-16",
    "2016-01-27", "2016-03-16", "2016-04-27", "2016-06-15", "2016-07-27", "2016-09-21", "2016-11-02", "2016-12-14",
    "2017-02-01", "2017-03-15", "2017-05-03", "2017-06-14", "2017-07-26", "2017-09-20", "2017-11-01", "2017-12-13",
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13", "2018-08-01", "2018-09-26", "2018-11-08", "2018-12-19",
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19", "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
    "2020-01-29", "2020-03-03", "2020-03-15", "2020-04-29", "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
}


def _generate_cpi_dates(start_year: int, end_year: int) -> set[str]:
    dates: set[str] = set()
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            d = datetime(year, month, 11)
            while d.weekday() >= 5:
                d = d.replace(day=d.day + 1)
            dates.add(d.strftime("%Y-%m-%d"))
    return dates


def _generate_nfp_dates(start_year: int, end_year: int) -> set[str]:
    dates: set[str] = set()
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            d = datetime(year, month, 1)
            while d.weekday() != 4:
                d = d.replace(day=d.day + 1)
            dates.add(d.strftime("%Y-%m-%d"))
    return dates


def build_event_date_set(start_year: int = 2015, end_year: int = 2025) -> set[str]:
    events = set(_FOMC_DATES)
    events.update(_generate_cpi_dates(start_year, end_year))
    events.update(_generate_nfp_dates(start_year, end_year))
    return events


PUBLIC_EVENT_DATES = build_event_date_set()

try:
    import yfinance as _yf
    HAS_YF = True
except Exception:
    _yf = None
    HAS_YF = False


@dataclass
class Trade:
    trade_id: int
    signal_date: str
    entry_date: str
    exit_date: str
    symbol: str
    direction: str
    signal_type: str
    entry_price: float
    stop_level: float
    target_level: float
    exit_price: float
    exit_reason: str
    shares: int
    notional: float
    notional_pct: float
    risk_amount: float
    risk_pct: float
    slippage: float
    commission: float
    gap_pct: float
    atr: float
    atr_percentile: float
    rsi3: float
    ema20: float
    ema50: float
    ema200: float
    vix: float | None
    spy_regime: str
    volume: float
    avg_volume_20d: float
    skip_flags: str
    decision_json: str
    pnl: float
    r_multiple: float


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
    return pd.DataFrame(
        {
            "date": pd.to_datetime(df_raw.index),
            "open": df_raw["open"].astype(float).values,
            "high": df_raw["high"].astype(float).values,
            "low": df_raw["low"].astype(float).values,
            "close": df_raw["close"].astype(float).values,
            "volume": df_raw.get("volume", pd.Series([0] * len(df_raw))).astype(float).values,
        }
    ).dropna(subset=["close"]).sort_values("date").reset_index(drop=True)


def _fetch_via_http(ticker: str, start: str, end: str, max_retries: int = 3) -> pd.DataFrame:
    start_ts = int(pd.Timestamp(start).timestamp())
    end_ts = int(pd.Timestamp(end).timestamp())
    url = (
        f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker, safe='')}"
        f"?interval=1d&period1={start_ts}&period2={end_ts}&events=adjsplit"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/json",
    }
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = json.loads(resp.read().decode())
            result = raw["chart"]["result"][0]
            timestamps = result["timestamp"]
            quote = result["indicators"]["quote"][0]
            adj = result["indicators"].get("adjclose", [{}])
            adj_close = adj[0].get("adjclose") if adj else None
            dates = [datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") for ts in timestamps]
            frame = pd.DataFrame(
                {
                    "date": pd.to_datetime(dates),
                    "open": quote["open"],
                    "high": quote["high"],
                    "low": quote["low"],
                    "close": quote["close"],
                    "volume": quote.get("volume", [0] * len(dates)),
                }
            )
            if adj_close:
                ratios = [a / c if c else 1.0 for a, c in zip(adj_close, quote["close"])]
                frame["open"] = [o * r for o, r in zip(frame["open"], ratios)]
                frame["high"] = [h * r for h, r in zip(frame["high"], ratios)]
                frame["low"] = [l * r for l, r in zip(frame["low"], ratios)]
                frame["close"] = adj_close
            return frame.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
        except Exception as exc:
            last_err = exc
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to download {ticker}: {last_err}")


def fetch_ohlcv(ticker: str, start: str = START_DATE, end: str = END_DATE) -> pd.DataFrame:
    return _fetch_via_yfinance(ticker, start, end) if HAS_YF else _fetch_via_http(ticker, start, end)


def _prefix_frame(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    return df.copy().sort_values("date").reset_index(drop=True).rename(
        columns={"open": f"{prefix}_open", "high": f"{prefix}_high", "low": f"{prefix}_low", "close": f"{prefix}_close", "volume": f"{prefix}_volume"}
    )


def _rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50.0)


def _percentile_rank_last(values: np.ndarray) -> float:
    return float(np.mean(values <= values[-1]))


def add_price_indicators(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    out = _prefix_frame(df, prefix)
    close = out[f"{prefix}_close"]
    high = out[f"{prefix}_high"]
    low = out[f"{prefix}_low"]
    volume = out[f"{prefix}_volume"]
    prev_close = close.shift(1)

    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(ATR_LOOKBACK, min_periods=ATR_LOOKBACK).mean()
    atr_pct = atr / close
    atr_pctile = atr_pct.rolling(ATR_PERCENTILE_LOOKBACK, min_periods=60).apply(_percentile_rank_last, raw=True)

    out[f"{prefix}_ema20"] = close.ewm(span=EMA_FAST, adjust=False).mean()
    out[f"{prefix}_ema50"] = close.ewm(span=EMA_MID, adjust=False).mean()
    out[f"{prefix}_ema200"] = close.ewm(span=EMA_SLOW, adjust=False).mean()
    out[f"{prefix}_ema20_slope"] = out[f"{prefix}_ema20"].pct_change(EMA_SLOPE_LOOKBACK)
    out[f"{prefix}_rsi3"] = _rsi(close, RSI_PERIOD)
    out[f"{prefix}_atr14"] = atr
    out[f"{prefix}_atr_pct"] = atr_pct
    out[f"{prefix}_atr_pctile"] = atr_pctile
    out[f"{prefix}_avg_volume_20d"] = volume.rolling(20, min_periods=20).mean()
    out[f"{prefix}_prior_high"] = high.shift(1)
    out[f"{prefix}_prior_swing_high"] = high.rolling(10, min_periods=10).max().shift(1)
    out[f"{prefix}_prior_swing_low"] = low.rolling(SWING_LOOKBACK, min_periods=SWING_LOOKBACK).min().shift(1)
    out[f"{prefix}_gap_pct"] = (out[f"{prefix}_open"] / prev_close - 1.0).abs()
    return out


def merge_market_data(uso: pd.DataFrame, spy: pd.DataFrame, vix: pd.DataFrame, cl: pd.DataFrame, uup: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    uso_i = add_price_indicators(uso, "uso")
    spy_i = add_price_indicators(spy, "spy")
    vix_i = add_price_indicators(vix, "vix")
    cl_i = add_price_indicators(cl, "cl")
    frames = [
        uso_i,
        spy_i[["date", "spy_close", "spy_ema20", "spy_ema50", "spy_ema200"]],
        vix_i[["date", "vix_close"]],
        cl_i[["date", "cl_close", "cl_gap_pct"]],
    ]
    if uup is not None:
        uup_i = add_price_indicators(uup, "uup")
        frames.append(uup_i[["date", "uup_close", "uup_ema20", "uup_ema50", "uup_ema200"]])
    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.merge(frame, on="date", how="inner")
    merged = merged.sort_values("date").reset_index(drop=True)
    merged["is_wednesday"] = pd.to_datetime(merged["date"]).dt.weekday == 2
    merged["is_public_event"] = merged["date"].dt.strftime("%Y-%m-%d").isin(PUBLIC_EVENT_DATES)
    merged["risk_off_spy"] = (merged["spy_close"] < merged["spy_ema200"]) | (merged["spy_ema20"] < merged["spy_ema50"])
    return merged


def _required_fields_present(row: pd.Series) -> bool:
    required = [
        "uso_open", "uso_high", "uso_low", "uso_close", "uso_volume", "uso_ema20", "uso_ema50", "uso_ema200",
        "uso_ema20_slope", "uso_rsi3", "uso_atr14", "uso_atr_pct", "uso_atr_pctile", "uso_avg_volume_20d",
        "spy_close", "spy_ema20", "spy_ema50", "spy_ema200", "vix_close", "cl_close", "cl_gap_pct",
    ]
    return all(pd.notna(row.get(col)) for col in required)


def _skip_reasons(row: pd.Series) -> list[str]:
    reasons: list[str] = []
    if not _required_fields_present(row):
        reasons.append("missing_data")
    if row.get("uso_volume", 0) <= 0:
        reasons.append("zero_volume")
    if row.get("uso_avg_volume_20d", 0) < MIN_AVG_VOLUME:
        reasons.append("low_avg_volume")
    if row.get("uso_gap_pct", 0) > MAX_GAP_PCT:
        reasons.append("uso_gap_too_large")
    if row.get("cl_gap_pct", 0) > 0.08:
        reasons.append("wti_discontinuity")
    if row.get("vix_close", 0) > VIX_HARD_STOP:
        reasons.append("vix_hard_stop")
    if row.get("uso_atr_pct", 0) > MAX_ATR_PCT:
        reasons.append("atr_too_high")
    if row.get("uso_atr_pct", 0) > SHOCK_ATR_PCT:
        reasons.append("atr_shock")
    if row.get("vix_close", 0) > VIX_MAX:
        reasons.append("vix_too_high")
    if row.get("is_wednesday", False):
        reasons.append("eia_inventory_day")
    if row.get("is_public_event", False):
        reasons.append("public_event_window")
    if row.get("risk_off_spy", False):
        reasons.append("spy_risk_off")
    return reasons


def _pullback_setup_ok(prev_row: pd.Series) -> bool:
    near_ema = prev_row["uso_close"] <= prev_row["uso_ema20"] * 1.01
    oversold = prev_row["uso_rsi3"] < 30
    healthy_volume = prev_row["uso_volume"] >= 0.5 * prev_row.get("uso_avg_volume_20d", 0)
    constructive_trend = prev_row["uso_close"] > prev_row["uso_ema200"] and prev_row["uso_ema20_slope"] > 0 and prev_row["uso_ema20"] > prev_row["uso_ema50"]
    regime_ok = 15.0 <= prev_row["vix_close"] <= VIX_MAX and prev_row["spy_close"] >= prev_row["spy_ema200"] and prev_row["spy_ema20"] >= prev_row["spy_ema50"]
    atr_ok = 0.0 < prev_row["uso_atr_pct"] <= MAX_ATR_PCT and 0.20 <= prev_row["uso_atr_pctile"] <= 0.80
    return bool(near_ema and oversold and healthy_volume and constructive_trend and regime_ok and atr_ok)


def _entry_signal(curr_row: pd.Series, prev_row: pd.Series) -> bool:
    return bool(curr_row["uso_close"] > prev_row["uso_high"] and _pullback_setup_ok(prev_row))


def _risk_inputs(row: pd.Series) -> tuple[float, float]:
    elevated = row["vix_close"] >= ELEVATED_VIX_LEVEL or row["is_public_event"]
    risk_pct = MAX_RISK_ELEVATED if elevated else MAX_RISK_NORMAL
    notional_pct = MAX_NOTIONAL_ELEVATED if elevated else MAX_NOTIONAL_NORMAL
    return risk_pct, notional_pct


def _build_entry_trade(market: pd.DataFrame, signal_idx: int, entry_idx: int, trade_id: int) -> tuple[Optional[dict[str, Any]], Optional[str], Optional[dict[str, Any]]]:
    signal_row = market.iloc[signal_idx]
    entry_row = market.iloc[entry_idx]
    prev_close = signal_row["uso_close"]
    entry_gap_pct = abs(entry_row["uso_open"] / prev_close - 1.0)
    skip_flags = [f"signal_{reason}" for reason in _skip_reasons(signal_row)]
    if entry_row["is_wednesday"]:
        skip_flags.append("entry_eia_inventory_day")
    if entry_row["is_public_event"]:
        skip_flags.append("entry_public_event_window")
    if entry_gap_pct > MAX_GAP_PCT:
        skip_flags.append("entry_gap_too_large")
    if entry_row["cl_gap_pct"] > 0.08:
        skip_flags.append("entry_wti_discontinuity")

    if skip_flags:
        return None, ",".join(skip_flags), None

    risk_pct, notional_pct = _risk_inputs(signal_row)
    stop_candidate_swing = float(signal_row["uso_prior_swing_low"])
    stop_candidate_atr = float(entry_row["uso_open"] - 2.0 * signal_row["uso_atr14"])
    stop_level = min(stop_candidate_swing, stop_candidate_atr)
    risk_per_share = float(entry_row["uso_open"] - stop_level)
    if not math.isfinite(risk_per_share) or risk_per_share <= 0:
        return None, "invalid_risk_per_share", None

    equity_at_risk = STARTING_EQUITY * risk_pct
    shares_by_risk = int(equity_at_risk // (risk_per_share + 2 * SLIPPAGE_PER_SHARE))
    max_notional = STARTING_EQUITY * notional_pct
    shares_by_notional = int(max_notional // entry_row["uso_open"])
    shares = max(0, min(shares_by_risk, shares_by_notional))
    if shares <= 0:
        return None, "cannot_size_within_caps", None

    prior_swing_high = float(signal_row["uso_prior_swing_high"])
    two_r_target = float(entry_row["uso_open"] + TARGET_R_MULTIPLE * risk_per_share)
    if math.isfinite(prior_swing_high) and prior_swing_high > entry_row["uso_open"]:
        target_level = min(two_r_target, prior_swing_high)
    else:
        target_level = two_r_target

    decision = {
        "signal_date": signal_row["date"].strftime("%Y-%m-%d"),
        "entry_date": entry_row["date"].strftime("%Y-%m-%d"),
        "signal_type": "pullback_breakout",
        "risk_pct": risk_pct,
        "notional_pct": notional_pct,
        "risk_per_share": risk_per_share,
        "shares": shares,
        "stop_level": stop_level,
        "target_level": target_level,
        "entry_gap_pct": entry_gap_pct,
        "atr_pct": float(signal_row["uso_atr_pct"]),
        "atr_pctile": float(signal_row["uso_atr_pctile"]),
        "rsi3": float(signal_row["uso_rsi3"]),
        "ema20": float(signal_row["uso_ema20"]),
        "ema50": float(signal_row["uso_ema50"]),
        "ema200": float(signal_row["uso_ema200"]),
        "vix": float(signal_row["vix_close"]),
        "spy_regime": "constructive" if not signal_row["risk_off_spy"] else "risk_off",
        "skip_flags": skip_flags,
    }
    trade = {
        "trade_id": trade_id,
        "signal_idx": signal_idx,
        "entry_idx": entry_idx,
        "signal_date": signal_row["date"].strftime("%Y-%m-%d"),
        "entry_date": entry_row["date"].strftime("%Y-%m-%d"),
        "symbol": "USO",
        "direction": "long",
        "signal_type": "pullback_breakout",
        "entry_price": float(entry_row["uso_open"] + SLIPPAGE_PER_SHARE),
        "stop_level": float(stop_level),
        "target_level": float(target_level),
        "shares": shares,
        "risk_amount": float(risk_per_share * shares),
        "risk_pct": risk_pct,
        "notional": float(entry_row["uso_open"] * shares),
        "notional_pct": float((entry_row["uso_open"] * shares) / STARTING_EQUITY),
        "commission": 0.0,
        "slippage": SLIPPAGE_PER_SHARE,
        "gap_pct": float(entry_gap_pct),
        "atr": float(signal_row["uso_atr14"]),
        "atr_pctile": float(signal_row["uso_atr_pctile"]),
        "rsi3": float(signal_row["uso_rsi3"]),
        "ema20": float(signal_row["uso_ema20"]),
        "ema50": float(signal_row["uso_ema50"]),
        "ema200": float(signal_row["uso_ema200"]),
        "vix": float(signal_row["vix_close"]),
        "spy_regime": "constructive" if not signal_row["risk_off_spy"] else "risk_off",
        "volume": float(signal_row["uso_volume"]),
        "avg_volume_20d": float(signal_row["uso_avg_volume_20d"]),
        "skip_flags": ",".join(skip_flags),
        "decision_json": json.dumps(decision, sort_keys=True),
        "pnl": 0.0,
        "r_multiple": 0.0,
        "entry_fill": float(entry_row["uso_open"] + SLIPPAGE_PER_SHARE),
        "max_favorable_close": float(entry_row["uso_open"] + SLIPPAGE_PER_SHARE),
    }
    return trade, None, decision


def _exit_trade(trade: dict[str, Any], row: pd.Series, reason: str, exit_fill: float) -> dict[str, Any]:
    pnl = (exit_fill - trade["entry_fill"]) * trade["shares"]
    r_multiple = pnl / trade["risk_amount"] if trade["risk_amount"] else 0.0
    out = trade.copy()
    out.update({
        "exit_date": row["date"].strftime("%Y-%m-%d"),
        "exit_price": float(exit_fill),
        "exit_reason": reason,
        "pnl": float(pnl),
        "r_multiple": float(r_multiple),
    })
    return out


def run_backtest(market: pd.DataFrame) -> dict[str, Any]:
    market = market.sort_values("date").reset_index(drop=True)
    trade_log: list[dict[str, Any]] = []
    decision_log: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    drawdown_series: list[dict[str, Any]] = []

    cash = STARTING_EQUITY
    open_trade: Optional[dict[str, Any]] = None
    pending_entry: Optional[dict[str, Any]] = None
    trade_id = 1
    peak_equity = STARTING_EQUITY

    for i in range(1, len(market)):
        row = market.iloc[i]
        prev = market.iloc[i - 1]
        date = row["date"].strftime("%Y-%m-%d")
        action = "flat"
        reason = None

        if pending_entry and i == pending_entry["entry_idx"] and open_trade is None:
            trade, skip_reason, decision = _build_entry_trade(market, pending_entry["signal_idx"], i, trade_id)
            if trade is not None:
                open_trade = trade
                action = "enter_long"
                reason = "entry_signal"
                trade_id += 1
            else:
                action = "skip_entry"
                reason = skip_reason
            pending_entry = None
        elif pending_entry and i > pending_entry["entry_idx"]:
            pending_entry = None

        if open_trade is None and pending_entry is None:
            if not _skip_reasons(prev) and not _skip_reasons(row) and _entry_signal(row, prev):
                pending_entry = {"signal_idx": i, "entry_idx": i + 1}
                action = "signal_long"
                reason = "pullback_breakout"

        if open_trade is not None:
            held_days = i - open_trade["entry_idx"] + 1
            open_trade["max_favorable_close"] = max(open_trade["max_favorable_close"], float(row["uso_high"]))
            mfe = open_trade["max_favorable_close"] - open_trade["entry_fill"]
            open_trade["mfe"] = mfe

            stop = open_trade["stop_level"]
            target = open_trade["target_level"]
            exit_reason = None
            exit_fill = None

            if row["uso_open"] <= stop:
                exit_reason = "gap_through_stop"
                exit_fill = float(max(0.0, row["uso_open"] - SLIPPAGE_PER_SHARE))
            elif row["uso_low"] <= stop:
                exit_reason = "stop_loss"
                exit_fill = float(stop - SLIPPAGE_PER_SHARE)
            elif row["uso_high"] >= target:
                exit_reason = "profit_target"
                exit_fill = float((row["uso_open"] - SLIPPAGE_PER_SHARE) if row["uso_open"] >= target else (target - SLIPPAGE_PER_SHARE))
            elif held_days >= MAX_HOLD_DAYS and mfe < 0.5 * open_trade["risk_amount"] / max(open_trade["shares"], 1):
                exit_reason = "time_stop"
                exit_fill = float(row["uso_close"] - SLIPPAGE_PER_SHARE)
            elif row["uso_atr_pct"] > SHOCK_ATR_PCT:
                exit_reason = "atr_shock_exit"
                exit_fill = float(row["uso_close"] - SLIPPAGE_PER_SHARE)
            elif i == len(market) - 1:
                exit_reason = "end_of_data"
                exit_fill = float(row["uso_close"] - SLIPPAGE_PER_SHARE)

            if exit_reason is not None:
                closed = _exit_trade(open_trade, row, exit_reason, exit_fill)
                trade_log.append(closed)
                cash += closed["pnl"]
                action = f"exit_{exit_reason}"
                reason = exit_reason
                open_trade = None

        unrealized = 0.0
        if open_trade is not None:
            unrealized = (float(row["uso_close"]) - open_trade["entry_fill"]) * open_trade["shares"]
        equity = cash + unrealized
        peak_equity = max(peak_equity, equity)
        drawdown = (equity / peak_equity - 1.0) if peak_equity else 0.0

        decision_payload = {
            "date": date,
            "action": action,
            "reason": reason,
            "pending_entry": pending_entry is not None,
            "open_trade": open_trade is not None,
            "skip_flags": _skip_reasons(prev) if open_trade is None else [],
            "signal_candidate": bool(open_trade is None and pending_entry is None and _entry_signal(row, prev)),
            "regime": {
                "vix": float(row["vix_close"]),
                "spy_risk_off": bool(row["risk_off_spy"]),
                "uso_atr_pct": float(row["uso_atr_pct"]),
                "uso_atr_pctile": float(row["uso_atr_pctile"]),
            },
        }
        decision_log.append({"date": date, "action": action, "decision_json": json.dumps(decision_payload, sort_keys=True)})
        equity_curve.append({"date": date, "equity": equity})
        drawdown_series.append({"date": date, "equity": equity, "drawdown": drawdown})

    trades_df = pd.DataFrame(trade_log)
    equity_df = pd.DataFrame(equity_curve)
    dd_df = pd.DataFrame(drawdown_series)
    decision_df = pd.DataFrame(decision_log)
    metrics = compute_metrics(trades_df, equity_df, dd_df)
    benchmark = compute_benchmarks(market)
    return {"trades": trades_df, "decision_log": decision_df, "equity_curve": equity_df, "drawdown_series": dd_df, "metrics": metrics, "benchmark": benchmark}


def _cagr(equity: pd.Series, dates: pd.Series) -> float:
    if equity.empty:
        return 0.0
    days = (pd.to_datetime(dates.iloc[-1]) - pd.to_datetime(dates.iloc[0])).days
    if days <= 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0]) ** (365.25 / days) - 1.0)


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    return float((equity / equity.cummax() - 1.0).min())


def compute_metrics(trades: pd.DataFrame, equity_curve: pd.DataFrame, drawdown_series: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {"trade_count": 0, "win_rate": 0.0, "profit_factor": 0.0, "expectancy": 0.0, "max_drawdown": float(drawdown_series["drawdown"].min()) if not drawdown_series.empty else 0.0, "ending_equity": float(equity_curve["equity"].iloc[-1]) if not equity_curve.empty else STARTING_EQUITY, "cagr": _cagr(equity_curve["equity"], equity_curve["date"]) if not equity_curve.empty else 0.0}
    wins = trades[trades["pnl"] > 0]
    losses = trades[trades["pnl"] < 0]
    gross_profit = wins["pnl"].sum()
    gross_loss = abs(losses["pnl"].sum())
    profit_factor = float(gross_profit / gross_loss) if gross_loss else float("inf")
    return {
        "trade_count": int(len(trades)),
        "win_rate": float(len(wins) / len(trades)),
        "profit_factor": profit_factor,
        "expectancy": float(trades["pnl"].mean()),
        "average_r": float(trades["r_multiple"].mean()),
        "median_r": float(trades["r_multiple"].median()),
        "max_drawdown": _max_drawdown(equity_curve["equity"]),
        "ending_equity": float(equity_curve["equity"].iloc[-1]) if not equity_curve.empty else STARTING_EQUITY,
        "cagr": _cagr(equity_curve["equity"], equity_curve["date"]) if not equity_curve.empty else 0.0,
        "avg_hold_days": float((pd.to_datetime(trades["exit_date"]) - pd.to_datetime(trades["entry_date"])).dt.days.mean()),
        "gross_profit": float(gross_profit),
        "gross_loss": float(gross_loss),
    }


def compute_benchmarks(market: pd.DataFrame) -> pd.DataFrame:
    base = market[["date", "uso_close", "uso_ema50", "uso_ema200"]].copy()
    base["buy_hold_uso"] = STARTING_EQUITY * (base["uso_close"] / base["uso_close"].iloc[0])
    base["cash"] = STARTING_EQUITY
    trend_equity = [STARTING_EQUITY]
    in_position = False
    shares = 0.0
    for _, row in base.iloc[1:].iterrows():
        if row["uso_close"] > row["uso_ema50"] and row["uso_ema50"] > row["uso_ema200"]:
            if not in_position:
                in_position = True
                shares = trend_equity[-1] / row["uso_close"]
            equity = shares * row["uso_close"]
        else:
            if in_position:
                equity = shares * row["uso_close"]
                in_position = False
                shares = 0.0
            else:
                equity = trend_equity[-1]
        trend_equity.append(equity)
    base["trend_filter"] = trend_equity
    return pd.DataFrame(
        {
            "strategy": ["buy_hold_uso", "cash", "trend_filter"],
            "ending_equity": [float(base["buy_hold_uso"].iloc[-1]), STARTING_EQUITY, float(base["trend_filter"].iloc[-1])],
            "cagr": [_cagr(base["buy_hold_uso"], base["date"]), 0.0, _cagr(base["trend_filter"], base["date"])],
            "max_drawdown": [_max_drawdown(base["buy_hold_uso"]), 0.0, _max_drawdown(base["trend_filter"])],
        }
    )


def _equity_from_realized_trades(trades: pd.DataFrame, period_start: pd.Timestamp, period_end: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    if period_end < period_start:
        raise ValueError("period_end must not precede period_start")

    equity = STARTING_EQUITY
    rows = [{"date": period_start, "equity": equity}]
    if not trades.empty:
        ordered = trades.sort_values(["exit_date", "entry_date"]).reset_index(drop=True)
        for _, trade in ordered.iterrows():
            exit_date = pd.to_datetime(trade["exit_date"])
            if period_start <= exit_date <= period_end:
                equity += float(trade["pnl"])
                rows.append({"date": exit_date, "equity": equity})
    if period_end != rows[-1]["date"]:
        rows.append({"date": period_end, "equity": equity})
    equity_curve = pd.DataFrame(rows)
    drawdown = equity_curve["equity"] / equity_curve["equity"].cummax() - 1.0
    return equity_curve, pd.DataFrame({"date": equity_curve["date"], "drawdown": drawdown})


def _split_metrics(trades: pd.DataFrame, split_date: str, period_start: str, period_end: str) -> tuple[dict[str, Any], dict[str, Any]]:
    split = pd.to_datetime(split_date)
    start = pd.to_datetime(period_start)
    end = pd.to_datetime(period_end)
    if trades.empty:
        empty = pd.DataFrame()
        in_equity, in_drawdown = _equity_from_realized_trades(empty, start, split)
        out_equity, out_drawdown = _equity_from_realized_trades(empty, split, end)
        return compute_metrics(empty, in_equity, in_drawdown), compute_metrics(empty, out_equity, out_drawdown)
    in_sample = trades[pd.to_datetime(trades["entry_date"]) < split]
    out_sample = trades[pd.to_datetime(trades["entry_date"]) >= split]
    in_equity, in_drawdown = _equity_from_realized_trades(in_sample, start, split)
    out_equity, out_drawdown = _equity_from_realized_trades(out_sample, split, end)
    return compute_metrics(in_sample, in_equity, in_drawdown), compute_metrics(out_sample, out_equity, out_drawdown)




def determine_recommendation(metrics: dict[str, Any]) -> tuple[str, str]:
    if metrics.get("trade_count", 0) < 30:
        return "RESEARCH-ONLY/REJECTED", "fewer than 30 trades"
    if metrics.get("profit_factor", 0.0) < 1.25:
        return "RESEARCH-ONLY/REJECTED", "profit factor below 1.25"
    if metrics.get("expectancy", 0.0) <= 0:
        return "RESEARCH-ONLY/REJECTED", "non-positive expectancy"
    if metrics.get("max_drawdown", 0.0) < -0.12:
        return "RESEARCH-ONLY/REJECTED", "max drawdown worse than 12%"
    if metrics.get("profit_factor", 0.0) >= 1.5 and metrics.get("max_drawdown", 0.0) >= -0.12:
        return "PAPER-CANDIDATE", "meets coarse backtest thresholds"
    return "RESEARCH-ONLY/REJECTED", "did not clear paper-candidate thresholds"

def _fmt_metrics(metrics: dict[str, Any]) -> list[str]:
    lines = []
    for key, value in metrics.items():
        if isinstance(value, float):
            if key in {"win_rate", "cagr", "max_drawdown"}:
                lines.append(f"- {key}: {value:.2%}")
            else:
                lines.append(f"- {key}: {value:,.2f}")
        else:
            lines.append(f"- {key}: {value}")
    return lines


def write_artifacts(result: dict[str, Any], market: pd.DataFrame) -> None:
    trades: pd.DataFrame = result["trades"]
    decision_log: pd.DataFrame = result["decision_log"]
    equity_curve: pd.DataFrame = result["equity_curve"]
    drawdown_series: pd.DataFrame = result["drawdown_series"]
    benchmark: pd.DataFrame = result["benchmark"]
    metrics: dict[str, Any] = result["metrics"]

    trades.to_csv(os.path.join(RESULTS_DIR, "trade_ledger.csv"), index=False)
    decision_log.to_csv(os.path.join(RESULTS_DIR, "decision_log.csv"), index=False)
    equity_curve.to_csv(os.path.join(RESULTS_DIR, "equity_curve.csv"), index=False)
    drawdown_series.to_csv(os.path.join(RESULTS_DIR, "drawdown_series.csv"), index=False)
    benchmark.to_csv(os.path.join(RESULTS_DIR, "benchmark_comparison.csv"), index=False)

    split_date = market["date"].iloc[int(len(market) * 0.7)].strftime("%Y-%m-%d")
    in_sample_metrics, out_sample_metrics = _split_metrics(
        trades,
        split_date,
        market["date"].iloc[0].strftime("%Y-%m-%d"),
        market["date"].iloc[-1].strftime("%Y-%m-%d"),
    )

    verdict, verdict_reason = determine_recommendation(metrics)
    summary = [
        "# USO Volatility-Contraction Breakout Backtest Summary",
        "",
        "## Scope",
        "- Public-data-only daily backtest",
        "- Long/cash only",
        "- No live or paper execution code",
        f"- Date range: {market['date'].iloc[0].strftime('%Y-%m-%d')} to {market['date'].iloc[-1].strftime('%Y-%m-%d')}",
        f"- Split date (70/30): {split_date}",
        f"- Final recommendation: {verdict} ({verdict_reason})",
        "",
        "## Core metrics",
        *_fmt_metrics(metrics),
        "",
        "## In-sample metrics",
        *_fmt_metrics(in_sample_metrics),
        "",
        "## Out-of-sample metrics",
        *_fmt_metrics(out_sample_metrics),
        "",
        "## Benchmark comparison",
    ]
    summary.extend([f"- {row.strategy}: ending_equity={row.ending_equity:,.2f}, cagr={row.cagr:.2%}, max_drawdown={row.max_drawdown:.2%}" for row in benchmark.itertuples(index=False)])
    summary.extend([
        "",
        "## Notes",
        "- The strategy requires a constructive long regime in USO and SPY.",
        "- EIA Wednesdays and public macro event dates are blocked for fresh entries. ATR percentile uses a 120-day trailing lookback in this v1.",
        "- If a required public input is missing, the engine stays flat and records the skip.",
        "- This v1 deliberately avoids any live, paper, or broker-execution path.",
    ])
    with open(os.path.join(RESULTS_DIR, "performance_summary.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(summary) + "\n")


def main() -> int:
    uso = fetch_ohlcv("USO")
    spy = fetch_ohlcv("SPY")
    vix = fetch_ohlcv("^VIX")
    cl = fetch_ohlcv("CL=F")
    uup = None
    try:
        uup = fetch_ohlcv("UUP")
    except Exception:
        uup = None
    market = merge_market_data(uso, spy, vix, cl, uup=uup)
    result = run_backtest(market)
    write_artifacts(result, market)
    metrics = result["metrics"]
    verdict, verdict_reason = determine_recommendation(metrics)
    print(json.dumps({"trades": int(metrics["trade_count"]), "ending_equity": metrics["ending_equity"], "max_drawdown": metrics["max_drawdown"], "profit_factor": metrics.get("profit_factor", 0), "verdict": verdict, "verdict_reason": verdict_reason, "out_dir": RESULTS_DIR}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
