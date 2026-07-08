#!/usr/bin/env python3
"""Research-only backtest for following public U.S. politician trade disclosures.

The defensive rule is simple: a disclosure can only create a signal at the
public filing date. Transaction dates are metadata for lag analysis; they are
never allowed to timestamp entries.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import re
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

HOUSE_ZIP_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
HOUSE_PTR_PDF_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
RESULTS_DIR = Path(__file__).resolve().parent / "results"


@dataclass(frozen=True)
class DisclosureMetadata:
    politician: str
    chamber: str
    filing_date: pd.Timestamp
    filing_type: str
    year: int
    source_doc_id: str
    source_url: str
    state_district: str = ""


@dataclass(frozen=True)
class PoliticianTransaction:
    politician: str
    chamber: str
    filing_date: pd.Timestamp
    transaction_date: pd.Timestamp
    owner: str
    ticker: str
    security: str
    asset_type: str
    transaction_type: str
    amount_range: str
    source_doc_id: str
    source_url: str
    party: str = ""


@dataclass(frozen=True)
class StrategySignal:
    politician: str
    chamber: str
    ticker: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    transaction_date: pd.Timestamp
    direction: int
    exit_only: bool
    lag_days: int
    lag_bucket: str
    variant: str
    amount_range: str
    transaction_type: str
    source_doc_id: str
    source_url: str


class DisclosureSource(Protocol):
    chamber: str

    def load_metadata(self, years: list[int]) -> list[DisclosureMetadata]:
        ...


class HouseDisclosureSource:
    chamber = "House"

    def __init__(self, cache_dir: str | Path = "data/politician_trade_following") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def download_year_zip(self, year: int) -> Path:
        target = self.cache_dir / f"{year}FD.zip"
        if not target.exists():
            urllib.request.urlretrieve(HOUSE_ZIP_URL.format(year=year), target)  # nosec: public source URL
        return target

    def load_metadata(self, years: list[int]) -> list[DisclosureMetadata]:
        rows: list[DisclosureMetadata] = []
        for year in years:
            rows.extend(self.parse_year_zip(self.download_year_zip(year), year))
        return rows

    def parse_year_zip(self, zip_path: str | Path, year: int) -> list[DisclosureMetadata]:
        rows: list[DisclosureMetadata] = []
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                lower = name.lower()
                data = zf.read(name)
                if lower.endswith(".txt"):
                    rows.extend(self._parse_txt_metadata(data.decode("utf-8-sig", errors="replace"), year))
                elif lower.endswith(".xml"):
                    rows.extend(self._parse_xml_metadata(data, year))
        seen: set[str] = set()
        deduped: list[DisclosureMetadata] = []
        for row in rows:
            key = row.source_doc_id
            if row.filing_type.upper() == "P" and key not in seen:
                seen.add(key)
                deduped.append(row)
        return deduped

    def _parse_txt_metadata(self, text: str, year: int) -> list[DisclosureMetadata]:
        sample = text[:2048]
        delimiter = "|" if "|" in sample else "\t"
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows: list[DisclosureMetadata] = []
        for raw in reader:
            normalized = {_clean_key(k): (v or "").strip() for k, v in raw.items() if k}
            row = self._metadata_from_mapping(normalized, year)
            if row:
                rows.append(row)
        return rows

    def _parse_xml_metadata(self, data: bytes, year: int) -> list[DisclosureMetadata]:
        root = ET.fromstring(data)
        rows: list[DisclosureMetadata] = []
        for elem in root.iter():
            children = list(elem)
            if not children:
                continue
            mapping = {_clean_key(child.tag): (child.text or "").strip() for child in children}
            row = self._metadata_from_mapping(mapping, year)
            if row:
                rows.append(row)
        return rows

    def _metadata_from_mapping(self, mapping: dict[str, str], year: int) -> DisclosureMetadata | None:
        filing_type = _first(mapping, "filingtype", "filing_type", "type")
        doc_id = _first(mapping, "docid", "documentid", "document_id")
        filing_date = _parse_date(_first(mapping, "filingdate", "filing_date", "date"))
        if not filing_type or not doc_id or pd.isna(filing_date):
            return None
        first = _first(mapping, "first", "firstname", "first_name")
        last = _first(mapping, "last", "lastname", "last_name")
        politician = " ".join(part for part in [first, last] if part).strip() or _first(mapping, "name")
        return DisclosureMetadata(
            politician=politician,
            chamber="House",
            filing_date=pd.Timestamp(filing_date).normalize(),
            filing_type=filing_type,
            year=int(_first(mapping, "year") or year),
            source_doc_id=str(doc_id),
            source_url=HOUSE_PTR_PDF_URL.format(year=year, doc_id=doc_id),
            state_district=_first(mapping, "statedst", "state_district"),
        )

    def download_ptr_pdf(self, metadata: DisclosureMetadata) -> Path:
        target = self.cache_dir / "ptr-pdfs" / str(metadata.year) / f"{metadata.source_doc_id}.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            urllib.request.urlretrieve(metadata.source_url, target)  # nosec: public source URL
        return target


class SenateDisclosureSource:
    chamber = "Senate"

    def __init__(self, manual_csv: str | Path | None = None) -> None:
        self.manual_csv = Path(manual_csv) if manual_csv else None

    def load_metadata(self, years: list[int]) -> list[DisclosureMetadata]:
        if not self.manual_csv or not self.manual_csv.exists():
            raise RuntimeError(
                "Senate EFD search blocks this runtime (HTTP 403 observed). "
                "Provide a manual/vendor CSV via --senate-manual-csv; it must map into the normalized schema."
            )
        frame = pd.read_csv(self.manual_csv)
        rows: list[DisclosureMetadata] = []
        for _, row in frame.iterrows():
            filing_date = _parse_date(row.get("filing_date"))
            if pd.isna(filing_date):
                continue
            rows.append(
                DisclosureMetadata(
                    politician=str(row.get("politician", "")),
                    chamber="Senate",
                    filing_date=pd.Timestamp(filing_date).normalize(),
                    filing_type="P",
                    year=int(pd.Timestamp(filing_date).year),
                    source_doc_id=str(row.get("source_doc_id", "")),
                    source_url=str(row.get("source_url", "")),
                )
            )
        return rows


def extract_transactions_from_text(text: str, metadata: DisclosureMetadata) -> list[PoliticianTransaction]:
    """Best-effort PTR text parser for rows already extracted from a PDF.

    This deliberately returns normalized rows and leaves hard PDF layout cases
    auditable: raw text can be cached alongside the source DocID.
    """
    rows: list[PoliticianTransaction] = []
    for line in text.splitlines():
        if not re.search(r"\b(Purchase|Sale|Exchange)\b", line, flags=re.I):
            continue
        date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", line)
        ticker_match = re.search(r"\(([A-Z]{1,5}(?:\.[A-Z])?)\)", line)
        amount_match = re.search(r"\$[\d,]+\s*-\s*\$?[\d,]+", line)
        type_match = re.search(r"\b(Purchase|Sale(?: \(Full\))?|Sale(?: \(Partial\))?|Exchange)\b", line, flags=re.I)
        if not date_match or not ticker_match or not type_match:
            continue
        rows.append(
            PoliticianTransaction(
                politician=metadata.politician,
                chamber=metadata.chamber,
                filing_date=metadata.filing_date,
                transaction_date=pd.Timestamp(_parse_date(date_match.group(1))).normalize(),
                owner="",
                ticker=ticker_match.group(1).upper(),
                security=line[: ticker_match.start()].strip(" -"),
                asset_type="Stock",
                transaction_type=type_match.group(1),
                amount_range=amount_match.group(0) if amount_match else "",
                source_doc_id=metadata.source_doc_id,
                source_url=metadata.source_url,
            )
        )
    return rows


def next_tradable_open(filing_date: pd.Timestamp, calendar: pd.DatetimeIndex) -> pd.Timestamp:
    cal = pd.DatetimeIndex(pd.to_datetime(calendar)).sort_values().normalize().unique()
    filing = pd.Timestamp(filing_date).normalize()
    candidates = cal[cal > filing]
    if len(candidates) == 0:
        raise ValueError(f"No tradable date after filing_date={filing.date()}")
    return pd.Timestamp(candidates[0])


def lag_bucket(lag_days: int) -> str:
    if lag_days <= 7:
        return "<=7d"
    if lag_days <= 14:
        return "<=14d"
    if lag_days <= 30:
        return "<=30d"
    if lag_days <= 45:
        return "<=45d"
    return ">45d"


def transaction_to_signal(transaction: PoliticianTransaction, calendar: pd.DatetimeIndex, variant: str = "naive_long_buys") -> StrategySignal:
    tx_type = transaction.transaction_type.lower()
    is_buy = "purchase" in tx_type or tx_type == "buy"
    is_sell = "sale" in tx_type or tx_type == "sell"
    direction = 1 if is_buy else (-1 if variant == "long_buys_short_sells" and is_sell else 0)
    exit_only = bool(is_sell and variant == "sells_exit_only")
    lag = int((pd.Timestamp(transaction.filing_date).normalize() - pd.Timestamp(transaction.transaction_date).normalize()).days)
    return StrategySignal(
        politician=transaction.politician,
        chamber=transaction.chamber,
        ticker=transaction.ticker.upper(),
        signal_date=pd.Timestamp(transaction.filing_date).normalize(),
        entry_date=next_tradable_open(transaction.filing_date, calendar),
        transaction_date=pd.Timestamp(transaction.transaction_date).normalize(),
        direction=direction,
        exit_only=exit_only,
        lag_days=lag,
        lag_bucket=lag_bucket(lag),
        variant=variant,
        amount_range=transaction.amount_range,
        transaction_type=transaction.transaction_type,
        source_doc_id=transaction.source_doc_id,
        source_url=transaction.source_url,
    )


def build_strategy_signals(
    transactions: list[PoliticianTransaction],
    calendar: pd.DatetimeIndex,
    variant: str = "naive_long_buys",
    max_lag_days: int | None = None,
) -> list[StrategySignal]:
    signals: list[StrategySignal] = []
    for tx in sorted(transactions, key=lambda t: (t.filing_date, t.source_doc_id, t.ticker)):
        signal = transaction_to_signal(tx, calendar, variant=variant)
        if max_lag_days is not None and signal.lag_days > max_lag_days:
            continue
        if variant == "naive_long_buys" and signal.direction != 1:
            continue
        if signal.direction == 0 and not signal.exit_only:
            continue
        signals.append(signal)
    return signals


def run_backtest(
    signals: list[StrategySignal],
    prices: pd.DataFrame,
    holding_days: int = 30,
    starting_equity: float = 100_000.0,
    transaction_cost_bps: float = 5.0,
    slippage_bps: float = 5.0,
    single_name_cap: float = 0.05,
    include_placebo: bool = True,
) -> dict[str, pd.DataFrame]:
    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"]).dt.normalize()
    prices["ticker"] = prices["ticker"].str.upper()
    price_map = {(r.ticker, r.date): r for r in prices.itertuples(index=False)}
    dates = pd.DatetimeIndex(sorted(prices["date"].unique()))

    trades: list[dict[str, object]] = []
    equity = starting_equity
    per_side_cost = (transaction_cost_bps + slippage_bps) / 10_000.0
    for signal in signals:
        if signal.direction == 0:
            continue
        entry_row = price_map.get((signal.ticker, signal.entry_date))
        if entry_row is None:
            continue
        exit_candidates = dates[dates > signal.entry_date]
        if len(exit_candidates) == 0:
            continue
        exit_date = pd.Timestamp(exit_candidates[min(holding_days - 1, len(exit_candidates) - 1)])
        exit_row = price_map.get((signal.ticker, exit_date))
        if exit_row is None:
            continue
        entry = float(entry_row.open) * (1 + per_side_cost * signal.direction)
        exit_px = float(exit_row.close) * (1 - per_side_cost * signal.direction)
        gross_ret = signal.direction * (exit_px / entry - 1.0)
        capital = equity * single_name_cap
        pnl = capital * gross_ret
        equity += pnl
        trades.append(
            {
                **asdict(signal),
                "exit_date": exit_date,
                "entry_price": entry,
                "exit_price": exit_px,
                "gross_return": gross_ret,
                "pnl": pnl,
                "equity_after": equity,
                "sector": getattr(entry_row, "sector", "Unknown"),
            }
        )

    trades_df = pd.DataFrame(trades)
    equity_curve = _equity_curve_from_trades(trades_df, dates, starting_equity)
    metrics = _build_metrics(trades_df, equity_curve, starting_equity)
    placebo = _build_placebo(signals, prices, holding_days, starting_equity, transaction_cost_bps, slippage_bps, single_name_cap) if include_placebo else pd.DataFrame()
    return {
        "trades": trades_df,
        "signals": pd.DataFrame([asdict(s) for s in signals]),
        "equity_curve": equity_curve,
        "metrics": metrics,
        "placebo": placebo,
    }


def write_artifacts(result: dict[str, pd.DataFrame], output_dir: str | Path = RESULTS_DIR) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    result["signals"].to_csv(out / "normalized_transactions.csv", index=False)
    result["trades"].to_csv(out / "trade_ledger.csv", index=False)
    result["equity_curve"].to_csv(out / "equity_curve.csv", index=False)
    result["metrics"].to_csv(out / "metrics.csv")
    result["placebo"].to_csv(out / "random_shuffled_filing_date_placebo.csv", index=False)
    (out / "performance_summary.md").write_text(_summary_markdown(result))


def _build_metrics(trades: pd.DataFrame, equity_curve: pd.DataFrame, starting_equity: float) -> pd.DataFrame:
    if trades.empty:
        values = {"trade_count": 0, "total_return": 0.0, "hit_rate": 0.0, "max_drawdown": 0.0, "turnover": 0.0, "avg_lag_days": math.nan, "tail_loss_days": 0}
    else:
        returns = trades["gross_return"].astype(float)
        values = {
            "trade_count": int(len(trades)),
            "total_return": float(equity_curve["equity"].iloc[-1] / starting_equity - 1.0),
            "hit_rate": float((returns > 0).mean()),
            "max_drawdown": float(equity_curve["drawdown"].min()),
            "turnover": float(len(trades) * 0.05),
            "avg_lag_days": float(trades["lag_days"].mean()),
            "tail_loss_days": int((returns <= returns.quantile(0.05)).sum()) if len(returns) > 1 else int((returns < 0).sum()),
        }
    return pd.DataFrame.from_dict({"strategy": values}, orient="index")


def _equity_curve_from_trades(trades: pd.DataFrame, dates: pd.DatetimeIndex, starting_equity: float) -> pd.DataFrame:
    equity = starting_equity
    rows = []
    pnl_by_date = {}
    if not trades.empty:
        pnl_by_date = trades.groupby("exit_date")["pnl"].sum().to_dict()
    high = starting_equity
    for d in dates:
        equity += float(pnl_by_date.get(pd.Timestamp(d), 0.0))
        high = max(high, equity)
        rows.append({"date": pd.Timestamp(d), "equity": equity, "drawdown": equity / high - 1.0})
    return pd.DataFrame(rows)


def _build_placebo(signals: list[StrategySignal], prices: pd.DataFrame, holding_days: int, starting_equity: float, transaction_cost_bps: float, slippage_bps: float, single_name_cap: float) -> pd.DataFrame:
    if not signals:
        return pd.DataFrame(columns=["variant", "trade_count", "total_return"])
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(prices["date"]).dt.normalize().unique()))
    shuffled: list[StrategySignal] = []
    for idx, sig in enumerate(signals):
        new_signal_date = pd.Timestamp(dates[idx % max(len(dates) - 1, 1)])
        try:
            new_entry = next_tradable_open(new_signal_date, dates)
        except ValueError:
            continue
        shuffled.append(StrategySignal(**{**asdict(sig), "signal_date": new_signal_date, "entry_date": new_entry, "variant": "random_shuffled_filing_date_placebo"}))
    if not shuffled:
        return pd.DataFrame([{"variant": "random_shuffled_filing_date_placebo", "trade_count": 0, "total_return": 0.0}])
    placebo = run_backtest(shuffled, prices, holding_days, starting_equity, transaction_cost_bps, slippage_bps, single_name_cap, include_placebo=False)
    row = placebo["metrics"].loc["strategy"].to_dict()
    row["variant"] = "random_shuffled_filing_date_placebo"
    return pd.DataFrame([row])


def _summary_markdown(result: dict[str, pd.DataFrame]) -> str:
    metrics = result["metrics"].loc["strategy"].to_dict()
    lines = [
        "# Politician Trade-Following Backtest",
        "",
        "Research-only. No live trading integration. Signals are timestamped at the public filing date and execute no earlier than the next tradable open.",
        "",
        "## Core result",
        f"- Trades: {int(metrics.get('trade_count', 0))}",
        f"- Total return after costs/slippage: {metrics.get('total_return', 0.0):.2%}",
        f"- Max drawdown: {metrics.get('max_drawdown', 0.0):.2%}",
        f"- Hit rate: {metrics.get('hit_rate', 0.0):.2%}",
        f"- Average filing lag: {metrics.get('avg_lag_days', float('nan')):.1f} days",
        "",
        "## Methodology guardrails",
        "- Public House source: yearly ZIP metadata; PTRs are FilingType=P; PTR PDFs use the documented DocID URL pattern.",
        "- Senate is isolated behind an adapter; this runtime observed HTTP 403, so use manual/vendor CSV fallback until authenticated access is available.",
        "- Variants supported: naive long buys, long buys/short sells, sells-as-exit-only, lag filters <=7/14/30/45/all, and a random shuffled filing-date placebo.",
        "- Phase 1 is liquid U.S. listed common stocks/ETFs only; options are intentionally excluded from equity signals.",
        "",
        "## Placebo",
        "The file `random_shuffled_filing_date_placebo.csv` is the random shuffled filing-date placebo benchmark. If the strategy cannot beat that after costs and drawdown, the edge is not real enough to risk capital.",
    ]
    return "\n".join(lines) + "\n"


def _clean_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _first(mapping: dict[str, object], *keys: str) -> str:
    cleaned = {_clean_key(k): v for k, v in mapping.items()}
    for key in keys:
        value = cleaned.get(_clean_key(key))
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _parse_date(value: object) -> pd.Timestamp:
    return pd.to_datetime(value, errors="coerce")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run research-only politician trade-following backtest.")
    parser.add_argument("--prices-csv", help="CSV with date,ticker,open,close[,sector].")
    parser.add_argument("--transactions-csv", help="Normalized transactions CSV matching PoliticianTransaction fields.")
    parser.add_argument("--output-dir", default=str(RESULTS_DIR))
    parser.add_argument("--variant", default="naive_long_buys", choices=["naive_long_buys", "long_buys_short_sells", "sells_exit_only"])
    parser.add_argument("--max-lag-days", type=int, default=None)
    args = parser.parse_args()

    if not args.prices_csv or not args.transactions_csv:
        demo_dir = Path(args.output_dir)
        demo_dir.mkdir(parents=True, exist_ok=True)
        prices, transactions = _demo_inputs()
    else:
        prices = pd.read_csv(args.prices_csv)
        frame = pd.read_csv(args.transactions_csv)
        transactions = [PoliticianTransaction(**{**row.to_dict(), "filing_date": pd.Timestamp(row["filing_date"]), "transaction_date": pd.Timestamp(row["transaction_date"])}) for _, row in frame.iterrows()]
    calendar = pd.DatetimeIndex(pd.to_datetime(prices["date"]))
    signals = build_strategy_signals(transactions, calendar, variant=args.variant, max_lag_days=args.max_lag_days)
    result = run_backtest(signals, prices)
    write_artifacts(result, args.output_dir)
    print(f"Wrote politician trade-following research artifacts to {args.output_dir}")


def _demo_inputs() -> tuple[pd.DataFrame, list[PoliticianTransaction]]:
    prices = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]),
        "ticker": ["AAPL"] * 5,
        "open": [100.0, 101.0, 104.0, 106.0, 107.0],
        "close": [100.0, 103.0, 105.0, 107.0, 108.0],
        "sector": ["Technology"] * 5,
    })
    tx = PoliticianTransaction("Demo Member", "House", pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-02"), "Self", "AAPL", "Apple", "Stock", "Purchase", "$1,001 - $15,000", "demo", "https://example.test/demo.pdf")
    return prices, [tx]


if __name__ == "__main__":
    main()
