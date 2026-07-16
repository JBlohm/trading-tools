"""
Tests for backtest_uso_vol_contraction_breakout.py.

Covers:
  1. Public event-date helper coverage.
  2. Indicator generation for USO / SPY / VIX / CL frames.
  3. Signal generation and one-trade backtest flow.
  4. Artifact writing to CSV / Markdown outputs.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BT_DIR = os.path.dirname(__file__)
ROOT = os.path.dirname(os.path.dirname(BT_DIR))
sys.path.insert(0, ROOT)
sys.path.insert(0, BT_DIR)

import backtest_uso_vol_contraction_breakout as bt


def make_frame(start: str, closes: list[float], volume: float = 6_000_000, spread: float = 0.01) -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(closes), freq="B")
    rows = []
    for d, c in zip(dates, closes):
        rows.append(
            {
                "date": d,
                "open": c,
                "high": c * (1 + spread),
                "low": c * (1 - spread),
                "close": c,
                "volume": volume,
            }
        )
    return pd.DataFrame(rows)


def build_market() -> pd.DataFrame:
    base = list(np.linspace(100.0, 125.0, 260))
    uso_closes = base + [116.0, 126.0, 126.5, 132.0, 131.0]
    spy_closes = list(np.linspace(300.0, 330.0, len(uso_closes)))
    vix_closes = [20.0] * len(uso_closes)
    cl_closes = [70.0] * len(uso_closes)
    uup_closes = [25.0] * len(uso_closes)

    # Create a true pullback day before the breakout.
    uso = make_frame("2020-01-02", uso_closes)
    final_dates = pd.to_datetime(["2021-02-04", "2021-02-08", "2021-02-09", "2021-02-11", "2021-02-12"])
    for idx, d in zip(range(len(base), len(base) + 5), final_dates):
        uso.at[idx, "date"] = d
    uso.loc[len(base), ["open", "high", "low", "close"]] = [121.0, 122.0, 115.0, 116.0]
    uso.loc[len(base) + 1, ["open", "high", "low", "close"]] = [118.0, 127.0, 117.0, 126.0]
    uso.loc[len(base) + 2, ["open", "high", "low", "close"]] = [124.5, 132.0, 124.0, 126.5]
    uso.loc[len(base) + 3, ["open", "high", "low", "close"]] = [126.5, 132.0, 126.0, 132.0]
    uso.loc[len(base) + 4, ["open", "high", "low", "close"]] = [132.0, 133.0, 130.0, 131.0]

    spy = make_frame("2020-01-02", spy_closes, volume=80_000_000)
    vix = make_frame("2020-01-02", vix_closes, volume=20_000_000)
    cl = make_frame("2020-01-02", cl_closes, volume=15_000_000)
    uup = make_frame("2020-01-02", uup_closes, volume=4_000_000)
    for idx, d in zip(range(len(base), len(base) + 5), final_dates):
        spy.at[idx, "date"] = d
        vix.at[idx, "date"] = d
        cl.at[idx, "date"] = d
        uup.at[idx, "date"] = d
    return bt.merge_market_data(uso, spy, vix, cl, uup=uup)


def test_build_event_date_set_contains_known_public_dates():
    events = bt.build_event_date_set(2020, 2020)
    assert "2020-03-03" in events  # FOMC
    assert "2020-03-11" in events  # CPI approximation
    assert "2020-01-03" in events  # first Friday of Jan 2020
    assert all(isinstance(d, str) for d in events)


def test_add_price_indicators_populates_core_fields():
    frame = make_frame("2020-01-02", list(np.linspace(100, 130, 260)))
    out = bt.add_price_indicators(frame, "uso")
    last = out.iloc[-1]
    assert pd.notna(last["uso_ema20"])
    assert pd.notna(last["uso_ema200"])
    assert pd.notna(last["uso_atr14"])
    assert pd.notna(last["uso_atr_pctile"])
    assert pd.notna(last["uso_rsi3"])
    assert last["uso_avg_volume_20d"] > 0


def test_run_backtest_creates_one_trade_and_artifacts(tmp_path, monkeypatch):
    market = build_market()
    monkeypatch.setattr(bt, "_pullback_setup_ok", lambda row: bool(row["uso_close"] <= row["uso_ema20"] * 1.01 and row["uso_rsi3"] < 30))
    result = bt.run_backtest(market)
    trades = result["trades"]
    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["direction"] == "long"
    assert trade["entry_date"] > trade["signal_date"]
    assert trade["exit_reason"] in {"profit_target", "time_stop", "stop_loss", "gap_through_stop", "atr_shock_exit"}
    assert trade["shares"] > 0

    monkeypatch.setattr(bt, "RESULTS_DIR", str(tmp_path))
    bt.write_artifacts(result, market)

    expected = [
        "trade_ledger.csv",
        "decision_log.csv",
        "equity_curve.csv",
        "drawdown_series.csv",
        "benchmark_comparison.csv",
        "performance_summary.md",
    ]
    for name in expected:
        assert (Path(tmp_path) / name).exists(), name

    summary = (Path(tmp_path) / "performance_summary.md").read_text(encoding="utf-8")
    assert "USO Volatility-Contraction Breakout Backtest Summary" in summary
    assert "Benchmark comparison" in summary


def test_signal_and_entry_skip_gates_block_event_days(monkeypatch):
    market = build_market()
    monkeypatch.setattr(bt, "_pullback_setup_ok", lambda row: bool(row["uso_close"] <= row["uso_ema20"] * 1.01 and row["uso_rsi3"] < 30))
    signal_idx = len(market) - 4
    entry_idx = signal_idx + 1

    market.loc[market.index[signal_idx], "is_public_event"] = True
    assert bt.run_backtest(market)["trades"].empty

    market.loc[market.index[signal_idx], "is_public_event"] = False
    market.loc[market.index[entry_idx], "is_wednesday"] = True
    trade, skip_reason, _ = bt._build_entry_trade(market, signal_idx, entry_idx, 1)
    assert trade is None
    assert skip_reason is not None
    assert "entry_eia_inventory_day" in skip_reason


def test_open_position_is_realized_on_final_bar(monkeypatch):
    market = build_market().iloc[:-1].copy()
    monkeypatch.setattr(bt, "_pullback_setup_ok", lambda row: bool(row["uso_close"] <= row["uso_ema20"] * 1.01 and row["uso_rsi3"] < 30))
    original_build_entry_trade = bt._build_entry_trade

    def build_unexitable_trade(*args, **kwargs):
        trade, skip_reason, decision = original_build_entry_trade(*args, **kwargs)
        if trade is not None:
            trade["stop_level"] = 0.0
            trade["target_level"] = float("inf")
        return trade, skip_reason, decision

    monkeypatch.setattr(bt, "_build_entry_trade", build_unexitable_trade)

    trades = bt.run_backtest(market)["trades"]

    assert len(trades) == 1
    assert trades.iloc[0]["exit_reason"] == "end_of_data"
    assert trades.iloc[0]["exit_date"] == market.iloc[-1]["date"].strftime("%Y-%m-%d")


def test_split_metrics_use_realized_trade_equity():
    trades = pd.DataFrame(
        [
            {"entry_date": "2020-01-02", "exit_date": "2020-01-10", "pnl": 100.0, "r_multiple": 1.0},
            {"entry_date": "2020-02-03", "exit_date": "2020-02-10", "pnl": -250.0, "r_multiple": -1.0},
            {"entry_date": "2021-01-04", "exit_date": "2021-01-11", "pnl": 50.0, "r_multiple": 0.5},
        ]
    )

    in_sample, out_sample = bt._split_metrics(trades, "2021-01-01", "2020-01-01", "2021-12-31")

    assert in_sample["ending_equity"] == bt.STARTING_EQUITY - 150.0
    assert in_sample["max_drawdown"] < 0.0
    assert -0.01 < in_sample["cagr"] < 0.0
    assert out_sample["ending_equity"] == bt.STARTING_EQUITY + 50.0


def test_split_metrics_supports_a_no_trade_period():
    in_sample, out_sample = bt._split_metrics(pd.DataFrame(), "2021-01-01", "2020-01-01", "2021-12-31")

    assert in_sample["trade_count"] == 0
    assert out_sample["trade_count"] == 0
    assert in_sample["ending_equity"] == bt.STARTING_EQUITY
    assert out_sample["ending_equity"] == bt.STARTING_EQUITY


def test_split_metrics_assigns_split_straddling_trades_by_exit_date():
    trades = pd.DataFrame(
        [{"entry_date": "2020-12-30", "exit_date": "2021-01-04", "pnl": 100.0, "r_multiple": 1.0}]
    )

    in_sample, out_sample = bt._split_metrics(trades, "2021-01-01", "2020-01-01", "2021-12-31")

    assert in_sample["trade_count"] == 0
    assert out_sample["trade_count"] == 1
    assert out_sample["ending_equity"] == bt.STARTING_EQUITY + 100.0


def test_trend_benchmark_realizes_exit_day_close():
    market = pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"]),
            "uso_close": [100.0, 110.0, 90.0],
            "uso_ema50": [90.0, 100.0, 100.0],
            "uso_ema200": [80.0, 90.0, 95.0],
        }
    )

    benchmark = bt.compute_benchmarks(market).set_index("strategy")

    assert benchmark.loc["trend_filter", "ending_equity"] == bt.STARTING_EQUITY * 90.0 / 110.0


def test_entry_uses_only_open_time_and_signal_day_inputs():
    market = build_market()
    signal_idx = len(market) - 4
    entry_idx = signal_idx + 1
    baseline, baseline_reason, _ = bt._build_entry_trade(market, signal_idx, entry_idx, 1)
    assert baseline is not None
    assert baseline_reason is None

    market.loc[market.index[entry_idx], ["uso_volume", "uso_atr14", "uso_atr_pct", "uso_atr_pctile", "vix_close"]] = [0.0, 999.0, 9.0, 1.0, 99.0]
    market.loc[market.index[entry_idx], "risk_off_spy"] = True
    trade, skip_reason, _ = bt._build_entry_trade(market, signal_idx, entry_idx, 1)

    assert trade is not None
    assert skip_reason is None
    assert trade["shares"] == baseline["shares"]
    assert trade["stop_level"] == baseline["stop_level"]
    assert trade["target_level"] == baseline["target_level"]
