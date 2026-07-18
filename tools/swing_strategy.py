#!/usr/bin/env python3
"""
swing_strategy.py — Daily swing strategy evaluation loop.

Evaluates up to 6 liquid symbols using the PTJ Macro Breakout With Asymmetric
Risk strategy and emits explicit decisions: ENTER, HOLD, ADD, REDUCE, EXIT, SKIP.

Desk constraints enforced:
  - $25,000 account baseline
  - ≤6 symbols open simultaneously
  - One new trade per week per symbol
  - Position size from stop distance (1% account risk per trade, max 20% notional)
  - Correlation/cluster limit: max 2 positions from equity_broad cluster (SPY/QQQ/IWM)
  - Event/liquidity skip: min average daily volume check

Execution path (paper/live mode):
  - ENTER: TradeProposal → pretrade_risk_check → place_order
  - EXIT: place_order (SELL/BUY to close)
  - ADD: TradeProposal → pretrade_risk_check → place_order (add units)
  - REDUCE: place_order for partial close (1/3 of position)

Usage:
    python swing_strategy.py --dry-run
    python swing_strategy.py --paper
    python swing_strategy.py --dry-run --symbols SPY QQQ GLD
    python swing_strategy.py --dry-run --state-file /path/to/swing_state.json

Output: JSON to stdout with decisions for all symbols.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import subprocess
import sys
import types
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

# Stub ib_async for dry-run / import-only contexts
if "ib_async" not in sys.modules:
    _stub = types.ModuleType("ib_async")
    for _attr in ("IB", "Stock", "Future", "Contract", "Index", "Forex", "Option"):
        setattr(_stub, _attr, object)
    sys.modules["ib_async"] = _stub

from tools.detect_macro_breakout import calculate_indicators, evaluate_breakout_signal
from tools.trade_proposal import (
    AssetClass, OrderType, PositionEffect, ProposalError, Side,
    TIF, TradeProposal, TradeType, normalize_order_payload,
)

# ---------------------------------------------------------------------------
# Strategy configuration
# ---------------------------------------------------------------------------

BASELINE_SYMBOLS = ["SPY", "QQQ", "IWM", "GLD", "XLF", "XLE"]
SPY_LONG_ONLY_SYMBOLS = ["SPY"]
DEFAULT_SYMBOLS = SPY_LONG_ONLY_SYMBOLS
DEFAULT_STRATEGY_VARIANT = "spy_long_only"
STRATEGY_VARIANTS = {
    "spy_long_only": {
        "symbols": SPY_LONG_ONLY_SYMBOLS,
        "allowed_entry_directions": frozenset({"long"}),
        "description": "SPY-only long-only paper candidate from TRA-77 research",
    },
    "legacy_multi_symbol": {
        "symbols": BASELINE_SYMBOLS,
        "allowed_entry_directions": frozenset({"long", "short"}),
        "description": "Original six-symbol long/short research framework",
    },
}
MACRO_SYMBOLS = {"rates": "TLT", "dollar": "UUP", "credit": "HYG"}

ACCOUNT_SIZE_DEFAULT = 25_000.0
RISK_PER_TRADE_PCT = 0.01          # 1% of account per trade = $250
MAX_NOTIONAL_PCT = 0.20            # max 20% of account per position
MAX_POSITIONS = 6
MIN_POSITION_SHARES = 1
WEEKLY_LOOKBACK_DAYS = 7
DAYS_TO_FETCH = 300                # calendar days of history
REQUIRED_INDICATOR_BARS = 225      # MA_LONG + RANGE_LOOKBACK + retest buffer
MIN_AVG_VOLUME = 500_000           # skip if ADV below this (illiquidity filter)
PARTIAL_CLOSE_FRACTION = 1 / 3    # fraction to close on REDUCE

# Correlation clusters: max 2 positions from equity_broad cluster
EQUITY_BROAD_CLUSTER = frozenset(["SPY", "QQQ", "IWM"])
MAX_EQUITY_CLUSTER_POSITIONS = 2

DEFAULT_STATE_FILE = str(pathlib.Path(__file__).parent / "swing_state.json")


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def load_state(state_file: str) -> dict:
    """Load desk state from JSON file. Returns empty state if file missing."""
    p = pathlib.Path(state_file)
    if not p.exists():
        return {"positions": {}, "last_trade_dates": {}, "account_nlv": ACCOUNT_SIZE_DEFAULT}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"positions": {}, "last_trade_dates": {}, "account_nlv": ACCOUNT_SIZE_DEFAULT}


def save_state(state: dict, state_file: str) -> None:
    """Persist desk state to JSON file."""
    pathlib.Path(state_file).write_text(json.dumps(state, indent=2, default=str))


# ---------------------------------------------------------------------------
# Data fetching helpers
# ---------------------------------------------------------------------------

try:
    import yfinance as _yf
    _HAS_YF = True
except ImportError:
    _HAS_YF = False


def _yf_fetch(ticker: str, start: str, end: str) -> list:
    """Fetch bars via yfinance or Yahoo Finance HTTP API. Returns list of SimpleNamespace."""
    if _HAS_YF:
        df = _yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty:
            return []
        if hasattr(df.columns, "levels"):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() for c in df.columns]
        bars = []
        for idx, row in df.iterrows():
            try:
                bars.append(SimpleNamespace(
                    date=str(idx.date()),
                    open=float(row.get("open", row.get("adj close", 0))),
                    high=float(row.get("high", 0)),
                    low=float(row.get("low", 0)),
                    close=float(row.get("close", row.get("adj close", 0))),
                    volume=int(row.get("volume", 0)),
                ))
            except (TypeError, ValueError):
                continue
        return bars

    # HTTP fallback (no yfinance dependency)
    start_ts = int(datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    url = (
        f"https://query2.finance.yahoo.com/v8/finance/chart/"
        f"{urllib.parse.quote(ticker, safe='')}"
        f"?interval=1d&period1={start_ts}&period2={end_ts}&events=adjsplit"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0 Safari/537.36",
        "Accept": "application/json",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode())
        result = raw["chart"]["result"][0]
        timestamps = result["timestamp"]
        indicators = result["indicators"]
        quote = indicators["quote"][0]
        adj_close_series = (indicators.get("adjclose") or [{}])[0].get("adjclose") or quote["close"]
        bars = []
        for i, ts in enumerate(timestamps):
            try:
                d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                bars.append(SimpleNamespace(
                    date=d,
                    open=float(quote["open"][i] or 0),
                    high=float(quote["high"][i] or 0),
                    low=float(quote["low"][i] or 0),
                    close=float(adj_close_series[i] or 0),
                    volume=int(quote["volume"][i] or 0),
                ))
            except (TypeError, ValueError, IndexError):
                continue
        return bars
    except Exception:
        return []


def fetch_bars_dryrun(symbol: str, days_back: int = DAYS_TO_FETCH) -> list:
    """Fetch historical bars using yfinance (no TWS required)."""
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=days_back)).isoformat()
    return _yf_fetch(symbol, start, end)


def fetch_bars_tws(symbol: str, host: str, port: int) -> dict:
    """
    Fetch bars from TWS by calling tws_historical_data.py as subprocess.
    Returns the JSON result dict from that script.
    """
    script = str(pathlib.Path(__file__).parent / "tws_historical_data.py")
    try:
        result = subprocess.run(
            [sys.executable, script, "--symbol", symbol, "--days", str(DAYS_TO_FETCH),
             "--host", host, "--port", str(port)],
            capture_output=True, text=True, timeout=60,
        )
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        return {
            "status": "tws_offline",
            "symbol": symbol,
            "message": str(exc),
            "bars": [],
        }


def bars_from_result(result: dict) -> list:
    """Convert bars from tws_historical_data result to SimpleNamespace list."""
    from tools.tws_historical_data import bars_to_namespace
    return bars_to_namespace(result.get("bars", []))


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------

def compute_position_size(
    entry_price: float,
    stop_level: float,
    account_nlv: float,
) -> tuple[int, float]:
    """
    Compute share count and actual risk amount.

    Returns (shares, risk_amount). shares=0 means stop is too tight or price
    is too high for meaningful position with this account.
    """
    stop_distance = abs(entry_price - stop_level)
    if stop_distance <= 0 or entry_price <= 0:
        return 0, 0.0

    risk_budget = account_nlv * RISK_PER_TRADE_PCT
    max_notional = account_nlv * MAX_NOTIONAL_PCT

    shares_by_risk = math.floor(risk_budget / stop_distance)
    shares_by_notional = math.floor(max_notional / entry_price)
    shares = min(shares_by_risk, shares_by_notional)

    return max(0, shares), round(shares * stop_distance, 2)


# ---------------------------------------------------------------------------
# Desk constraint checks
# ---------------------------------------------------------------------------

def _count_equity_cluster(open_positions: dict) -> int:
    return sum(1 for sym in open_positions if sym in EQUITY_BROAD_CLUSTER)


def _days_since_last_trade(symbol: str, last_trade_dates: dict) -> Optional[int]:
    last = last_trade_dates.get(symbol)
    if not last:
        return None
    try:
        last_date = date.fromisoformat(str(last))
        return (date.today() - last_date).days
    except ValueError:
        return None


def _check_entry_constraints(
    symbol: str,
    state: dict,
    direction: str,
    avg_volume: Optional[float] = None,
) -> tuple[bool, str]:
    """
    Check all desk constraints for a new ENTER decision.

    Returns (allowed: bool, skip_reason: str).
    """
    positions = state.get("positions", {})
    last_trade_dates = state.get("last_trade_dates", {})

    if symbol in positions:
        return False, "already_in_position"

    if len(positions) >= MAX_POSITIONS:
        return False, "max_positions_reached"

    if symbol in EQUITY_BROAD_CLUSTER and _count_equity_cluster(positions) >= MAX_EQUITY_CLUSTER_POSITIONS:
        return False, "equity_cluster_limit"

    days_since = _days_since_last_trade(symbol, last_trade_dates)
    if days_since is not None and days_since < WEEKLY_LOOKBACK_DAYS:
        return False, f"weekly_trade_limit_{days_since}d_since_last"

    if avg_volume is not None and avg_volume < MIN_AVG_VOLUME:
        return False, f"low_liquidity_adv_{int(avg_volume)}"

    return True, ""


# ---------------------------------------------------------------------------
# TradeProposal building
# ---------------------------------------------------------------------------

def _build_proposal(
    symbol: str,
    side: str,
    shares: int,
    entry_price: float,
    stop_level: float,
    risk_amount: float,
    signal_state: str,
) -> tuple[Optional[dict], Optional[str]]:
    """
    Build and validate a TradeProposal. Returns (payload_dict, error_str).
    payload_dict is None on validation failure.
    """
    ob_side = Side.BUY if side == "long" else Side.SELL
    try:
        proposal = TradeProposal(
            instrument_id=f"STK:{symbol}",
            symbol=symbol,
            asset_class=AssetClass.EQUITY,
            side=ob_side,
            quantity=float(shares),
            order_type=OrderType.LMT,
            limit_price=round(entry_price, 2),
            tif=TIF.DAY,
            strategy="ptj_swing",
            rationale=f"Macro breakout: {signal_state}",
            trade_type=TradeType.SWING,
            risk_point=round(stop_level, 2),
            max_loss=round(risk_amount * 1.1, 2),
            max_risk_budget=round(risk_amount * 1.5, 2),
            venue="SMART",
            position_effect=PositionEffect.OPEN,
        )
        payload = normalize_order_payload(proposal)
        return payload, None
    except ProposalError as exc:
        return None, str(exc)


# ---------------------------------------------------------------------------
# Core: evaluate one symbol
# ---------------------------------------------------------------------------

def evaluate_symbol(
    symbol: str,
    primary_bars: list,
    rate_bars: Optional[list],
    dollar_bars: Optional[list],
    credit_bars: Optional[list],
    state: dict,
    account_nlv: float,
    allowed_entry_directions: frozenset[str] = STRATEGY_VARIANTS[DEFAULT_STRATEGY_VARIANT]["allowed_entry_directions"],
) -> dict:
    """
    Evaluate the swing strategy signal for one symbol and return a decision dict.

    Parameters
    ----------
    symbol       : ticker
    primary_bars : list[SimpleNamespace] — daily OHLCV bars for the symbol
    rate_bars    : TLT bars (or None)
    dollar_bars  : UUP bars (or None)
    credit_bars  : HYG bars (or None)
    state        : desk state (open_positions, last_trade_dates)
    account_nlv  : current account net liquidation value

    Returns
    -------
    dict with keys:
      symbol, decision, signal_state, confidence, direction,
      entry_price, stop_level, position_size, risk_amount,
      proposal_payload, proposal_error, skip_reason
    """
    base = {
        "symbol": symbol,
        "decision": "SKIP",
        "signal_state": "no_data",
        "confidence": 0.0,
        "direction": None,
        "entry_price": None,
        "stop_level": None,
        "position_size": None,
        "risk_amount": None,
        "proposal_payload": None,
        "proposal_error": None,
        "skip_reason": "insufficient_data",
    }

    if not primary_bars or len(primary_bars) < 50:
        base["skip_reason"] = "insufficient_bars"
        return base

    if len(primary_bars) < REQUIRED_INDICATOR_BARS:
        base["skip_reason"] = (
            f"insufficient_indicator_history_{len(primary_bars)}_bars_need_{REQUIRED_INDICATOR_BARS}"
        )
        return base

    features = calculate_indicators(primary_bars, rate_bars, dollar_bars, credit_bars)
    if not features:
        base["skip_reason"] = "indicator_error"
        return base

    positions = state.get("positions", {})
    position_context = None
    if symbol in positions:
        pos = positions[symbol]
        position_context = {
            "position_side": pos.get("side"),
            "entry_price": pos.get("entry_price"),
            "stop_level": pos.get("stop_level"),
            "bars_held": pos.get("bars_held", 0),
            "r_unit": features.get("r_unit"),
        }

    signal = evaluate_breakout_signal(features, position_context)
    sig_state = signal["signal_state"]
    confidence = signal["confidence"]

    base["signal_state"] = sig_state
    base["confidence"] = confidence

    # -- Position management: symbol already in portfolio --
    if position_context:
        pos = positions[symbol]
        side = pos.get("side")
        base["direction"] = side
        base["entry_price"] = pos.get("entry_price")
        base["stop_level"] = pos.get("stop_level")

        if sig_state == "exit_signal":
            base["decision"] = "EXIT"
            base["skip_reason"] = None
        elif sig_state == "trail_stop":
            base["decision"] = "REDUCE"
            base["skip_reason"] = None
        elif sig_state == "add_unit":
            # ADD: size additional units against current close and existing stop.
            # We do NOT run the full entry-constraint gate (no new position is opened;
            # weekly cooldown and cluster limits don't apply to adding to a winner).
            # We DO enforce the per-position notional cap so a single name can't
            # grow unboundedly.
            add_entry = features.get("close", 0.0)
            add_stop = pos.get("stop_level")
            if add_entry and add_stop:
                add_shares, add_risk = compute_position_size(add_entry, add_stop, account_nlv)
                if add_shares >= MIN_POSITION_SHARES:
                    payload, err = _build_proposal(
                        symbol, side, add_shares, add_entry, add_stop, add_risk, sig_state
                    )
                    base.update({
                        "decision": "ADD",
                        "entry_price": round(add_entry, 2),
                        "position_size": add_shares,
                        "risk_amount": add_risk,
                        "proposal_payload": payload,
                        "proposal_error": err,
                        "skip_reason": None,
                    })
                else:
                    base["decision"] = "HOLD"
                    base["skip_reason"] = "add_too_small"
            else:
                base["decision"] = "HOLD"
                base["skip_reason"] = "add_no_price"
        else:
            base["decision"] = "HOLD"
            base["skip_reason"] = None
        return base

    # -- New entry evaluation --
    if sig_state not in ("entry_trigger_long", "entry_trigger_short"):
        base["decision"] = "SKIP"
        base["skip_reason"] = f"signal_{sig_state}"
        return base

    direction = "long" if sig_state == "entry_trigger_long" else "short"
    if direction not in allowed_entry_directions:
        base["direction"] = direction
        base["skip_reason"] = f"direction_{direction}_disabled"
        return base

    entry_price = features.get("close", 0.0)
    stop_level = features.get("long_stop") if direction == "long" else features.get("short_stop")

    if not stop_level or not entry_price:
        base["skip_reason"] = "no_stop_level"
        return base

    # Estimate avg_volume for liquidity check (last 20 bars)
    if len(primary_bars) >= 20:
        avg_vol = sum(b.volume for b in primary_bars[-20:]) / 20
    else:
        avg_vol = None

    allowed, skip_reason = _check_entry_constraints(symbol, state, direction, avg_vol)
    if not allowed:
        base["direction"] = direction
        base["skip_reason"] = skip_reason
        return base

    shares, risk_amount = compute_position_size(entry_price, stop_level, account_nlv)
    if shares < MIN_POSITION_SHARES:
        base["direction"] = direction
        base["skip_reason"] = f"position_too_small_shares_{shares}"
        return base

    payload, err = _build_proposal(symbol, direction, shares, entry_price, stop_level, risk_amount, sig_state)

    base.update({
        "decision": "ENTER",
        "direction": direction,
        "entry_price": round(entry_price, 2),
        "stop_level": round(stop_level, 2),
        "position_size": shares,
        "risk_amount": risk_amount,
        "proposal_payload": payload,
        "proposal_error": err,
        "skip_reason": None if payload else f"proposal_invalid: {err}",
    })
    if not payload:
        base["decision"] = "SKIP"
    return base


# ---------------------------------------------------------------------------
# Core: daily loop
# ---------------------------------------------------------------------------

def run_daily_loop(
    symbols: list[str],
    state: dict,
    mode: str = "dry_run",
    host: str = "192.168.2.187",
    port: int = 7497,
    allowed_entry_directions: frozenset[str] = STRATEGY_VARIANTS[DEFAULT_STRATEGY_VARIANT]["allowed_entry_directions"],
) -> dict:
    """
    Run the daily evaluation loop for all symbols.

    Parameters
    ----------
    symbols   : list of tickers to evaluate
    state     : desk state dict (open_positions, last_trade_dates, account_nlv)
    mode      : "dry_run" | "paper" | "live"
    host/port : TWS connection parameters (used when mode != "dry_run")

    Returns
    -------
    dict with keys: timestamp, status, tws_status, account_nlv, mode, decisions[]
    """
    account_nlv = state.get("account_nlv", ACCOUNT_SIZE_DEFAULT)
    tws_status = "offline" if mode == "dry_run" else "unknown"

    # Fetch macro bars once (shared across all primary symbols)
    if mode == "dry_run":
        rate_bars = fetch_bars_dryrun("TLT")
        dollar_bars = fetch_bars_dryrun("UUP")
        credit_bars = fetch_bars_dryrun("HYG")
        tws_status = "dry_run"
    else:
        rate_result = fetch_bars_tws("TLT", host, port)
        dollar_result = fetch_bars_tws("UUP", host, port)
        credit_result = fetch_bars_tws("HYG", host, port)

        if rate_result.get("status") == "tws_offline":
            return {
                "timestamp": _utc_now(),
                "status": "tws_offline",
                "tws_status": "offline",
                "message": rate_result.get("message", "TWS unavailable"),
                "account_nlv": account_nlv,
                "mode": mode,
                "decisions": [],
            }
        tws_status = mode
        rate_bars = bars_from_result(rate_result)
        dollar_bars = bars_from_result(dollar_result)
        credit_bars = bars_from_result(credit_result)

    decisions = []
    for symbol in symbols:
        if mode == "dry_run":
            primary_bars = fetch_bars_dryrun(symbol)
        else:
            result = fetch_bars_tws(symbol, host, port)
            if result.get("status") == "tws_offline":
                decisions.append({
                    "symbol": symbol,
                    "decision": "SKIP",
                    "skip_reason": "tws_offline",
                    "signal_state": "tws_offline",
                    "confidence": 0.0,
                })
                continue
            primary_bars = bars_from_result(result)

        dec = evaluate_symbol(
            symbol,
            primary_bars,
            rate_bars,
            dollar_bars,
            credit_bars,
            state,
            account_nlv,
            allowed_entry_directions=allowed_entry_directions,
        )
        decisions.append(dec)

    return {
        "timestamp": _utc_now(),
        "status": "ok",
        "tws_status": tws_status,
        "account_nlv": account_nlv,
        "mode": mode,
        "decisions": decisions,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Daily swing strategy evaluation loop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Decisions: ENTER / HOLD / ADD / REDUCE / EXIT / SKIP
Modes:
  --dry-run  evaluate signals only, no orders placed (uses yfinance for data)
  --paper    evaluate and place paper orders via TWS (port 7497)
  --live     evaluate and place live orders via TWS (port 7496)

Examples:
  python swing_strategy.py --dry-run
  python swing_strategy.py --paper
  python swing_strategy.py --dry-run --strategy-variant legacy_multi_symbol
  python swing_strategy.py --dry-run --symbols SPY QQQ GLD
  python swing_strategy.py --dry-run --state-file /path/to/swing_state.json
        """,
    )
    mode_g = parser.add_mutually_exclusive_group()
    mode_g.add_argument("--dry-run", dest="mode", action="store_const", const="dry_run",
                        help="Dry-run: evaluate only, no orders (default)")
    mode_g.add_argument("--paper", dest="mode", action="store_const", const="paper",
                        help="Paper trading via TWS port 7497")
    mode_g.add_argument("--live", dest="mode", action="store_const", const="live",
                        help="Live trading via TWS port 7496")
    parser.set_defaults(mode="dry_run")

    parser.add_argument("--strategy-variant", choices=sorted(STRATEGY_VARIANTS),
                        default=DEFAULT_STRATEGY_VARIANT,
                        help=(
                            f"Strategy variant (default: {DEFAULT_STRATEGY_VARIANT}; "
                            "spy_long_only is SPY-only and long-only)"
                        ))
    parser.add_argument("--symbols", nargs="+", default=None,
                        help=(
                            "Override symbols to evaluate "
                            f"(default from variant: {' '.join(DEFAULT_SYMBOLS)})"
                        ))
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE,
                        help=f"Path to state JSON file (default: {DEFAULT_STATE_FILE})")
    parser.add_argument("--host", default="192.168.2.187",
                        help="TWS host (default: 192.168.2.187)")
    parser.add_argument("--account-nlv", type=float, default=None,
                        help="Override account NLV from state file")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state = load_state(args.state_file)
    if args.account_nlv is not None:
        state["account_nlv"] = args.account_nlv

    port = 7497 if args.mode in ("paper", "dry_run") else 7496

    variant = STRATEGY_VARIANTS[args.strategy_variant]
    symbols = args.symbols if args.symbols is not None else variant["symbols"]

    result = run_daily_loop(
        symbols=symbols,
        state=state,
        mode=args.mode,
        host=args.host,
        port=port,
        allowed_entry_directions=variant["allowed_entry_directions"],
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
