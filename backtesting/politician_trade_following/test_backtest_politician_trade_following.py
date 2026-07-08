"""
Tests for the public-disclosure-only politician trade-following backtest.

The central risk is look-ahead bias: no strategy signal may trade before the
public filing date, no matter when the underlying transaction happened.
"""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import pandas as pd

BT_DIR = os.path.dirname(__file__)
ROOT = os.path.dirname(os.path.dirname(BT_DIR))
sys.path.insert(0, ROOT)
sys.path.insert(0, BT_DIR)

import backtest_politician_trade_following as bt


def test_parse_house_zip_filters_periodic_transaction_reports(tmp_path: Path):
    zip_path = tmp_path / "2026FD.zip"
    txt = "\n".join(
        [
            "Prefix|Last|First|Suffix|FilingType|StateDst|Year|FilingDate|DocID",
            "|Pelosi|Nancy||P|CA12|2026|01/10/2026|200001",
            "|Smith|John||A|TX01|2026|01/11/2026|200002",
        ]
    )
    xml = """
    <FinancialDisclosure>
      <Member>
        <Last>Greene</Last><First>Marjorie</First><FilingType>P</FilingType>
        <StateDst>GA14</StateDst><Year>2026</Year><FilingDate>02/03/2026</FilingDate><DocID>200003</DocID>
      </Member>
    </FinancialDisclosure>
    """
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("2026FD.txt", txt)
        zf.writestr("2026FD.xml", xml)

    rows = bt.HouseDisclosureSource(cache_dir=tmp_path).parse_year_zip(zip_path, 2026)

    assert [row.source_doc_id for row in rows] == ["200001", "200003"]
    assert rows[0].politician == "Nancy Pelosi"
    assert rows[0].source_url.endswith("/ptr-pdfs/2026/200001.pdf")
    assert rows[1].filing_date == pd.Timestamp("2026-02-03")


def test_next_tradable_open_uses_filing_date_not_transaction_date():
    calendar = pd.DatetimeIndex(pd.to_datetime(["2026-01-09", "2026-01-12", "2026-01-13"]))
    transaction = bt.PoliticianTransaction(
        politician="Example Member",
        chamber="House",
        filing_date=pd.Timestamp("2026-01-10"),
        transaction_date=pd.Timestamp("2025-12-15"),
        owner="Self",
        ticker="MSFT",
        security="Microsoft Corp",
        asset_type="Stock",
        transaction_type="Purchase",
        amount_range="$1,001 - $15,000",
        source_doc_id="200001",
        source_url="https://example.test/ptr.pdf",
    )

    signal = bt.transaction_to_signal(transaction, calendar)

    assert signal.signal_date == pd.Timestamp("2026-01-10")
    assert signal.entry_date == pd.Timestamp("2026-01-12")
    assert signal.entry_date > transaction.transaction_date
    assert signal.direction == 1
    assert signal.lag_days == 26


def test_lag_buckets_and_sell_exit_only_variant_are_timestamped_at_filing_date():
    calendar = pd.DatetimeIndex(pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06", "2026-02-17"]))
    transactions = [
        bt.PoliticianTransaction("Fast", "House", pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-02"), "Self", "AAPL", "Apple", "Stock", "Purchase", "$1,001 - $15,000", "1", "url"),
        bt.PoliticianTransaction("Slow", "House", pd.Timestamp("2026-02-16"), pd.Timestamp("2026-01-02"), "Self", "TSLA", "Tesla", "Stock", "Sale", "$1,001 - $15,000", "2", "url"),
    ]

    signals = bt.build_strategy_signals(transactions, calendar, variant="sells_exit_only", max_lag_days=45)

    assert [signal.ticker for signal in signals] == ["AAPL", "TSLA"]
    assert signals[0].lag_bucket == "<=7d"
    assert signals[1].lag_bucket == "<=45d"
    assert signals[1].direction == 0
    assert signals[1].exit_only is True
    assert signals[1].entry_date == pd.Timestamp("2026-02-17")


def test_sells_exit_only_closes_existing_long_before_time_exit():
    prices = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12"]),
            "ticker": ["AAPL"] * 7,
            "open": [100.0, 101.0, 104.0, 105.0, 98.0, 97.0, 96.0],
            "close": [100.0, 103.0, 105.0, 99.0, 97.0, 96.0, 95.0],
            "sector": ["Technology"] * 7,
        }
    )
    transactions = [
        bt.PoliticianTransaction("Fast", "House", pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-02"), "Self", "AAPL", "Apple", "Stock", "Purchase", "$1,001 - $15,000", "1", "url"),
        bt.PoliticianTransaction("Fast", "House", pd.Timestamp("2026-01-07"), pd.Timestamp("2026-01-03"), "Self", "AAPL", "Apple", "Stock", "Sale", "$1,001 - $15,000", "2", "url"),
    ]
    signals = bt.build_strategy_signals(transactions, pd.DatetimeIndex(prices["date"]), variant="sells_exit_only")

    result = bt.run_backtest(signals, prices, holding_days=5, starting_equity=10_000.0, transaction_cost_bps=0.0, slippage_bps=0.0)

    assert result["metrics"].loc["strategy", "trade_count"] == 1
    trade = result["trades"].iloc[0]
    assert trade["entry_date"] == pd.Timestamp("2026-01-06")
    assert trade["exit_date"] == pd.Timestamp("2026-01-08")
    assert trade["exit_reason"] == "sell_exit_only"


def test_run_backtest_writes_metrics_and_placebo_artifacts(tmp_path: Path):
    prices = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]),
            "ticker": ["AAPL", "AAPL", "AAPL", "AAPL"],
            "open": [100.0, 101.0, 104.0, 106.0],
            "close": [100.0, 103.0, 105.0, 107.0],
            "sector": ["Technology"] * 4,
        }
    )
    tx = bt.PoliticianTransaction("Fast", "House", pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-02"), "Self", "AAPL", "Apple", "Stock", "Purchase", "$1,001 - $15,000", "1", "url")
    signals = bt.build_strategy_signals([tx], pd.DatetimeIndex(prices["date"]), variant="naive_long_buys")

    result = bt.run_backtest(signals, prices, holding_days=2, starting_equity=10_000.0, transaction_cost_bps=5.0, slippage_bps=5.0)
    bt.write_artifacts(result, tmp_path)

    assert result["metrics"].loc["strategy", "trade_count"] == 1
    assert result["trades"].iloc[0]["entry_date"] == pd.Timestamp("2026-01-06")
    assert (tmp_path / "normalized_transactions.csv").exists()
    summary = (tmp_path / "performance_summary.md").read_text()
    assert summary.startswith("# Politician Trade-Following Backtest")
    assert "random shuffled filing-date placebo" in summary
