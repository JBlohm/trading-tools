#!/usr/bin/env python3
"""
detect_crash_liquidity_breakdown.py — PTJ Crash Playbook / Liquidity Breakdown Detector.

Reads historical market data from TWS via ib_async and evaluates the PTJ-inspired
Crash Playbook / Liquidity Breakdown strategy. Outputs human-readable text followed
by a fenced JSON block for LLM consumption. Read-only: never places, modifies, or
cancels orders.

Connection ID: 1018 (see tools/connection_ids.json)

Exit codes:
  0 — signal computed (may be no_setup)
  2 — blocked: insufficient primary data
  3 — TWS unavailable / connection failure

Usage:
    python detect_crash_liquidity_breakdown.py
    python detect_crash_liquidity_breakdown.py --symbol SPY --live
    python detect_crash_liquidity_breakdown.py --symbol ES --sec-type FUT --expiry 202506
    python detect_crash_liquidity_breakdown.py --vix-symbol VIX --credit-symbol HYG \\
        --breadth-symbol RSP
    python detect_crash_liquidity_breakdown.py --position-side short \\
        --entry-price 540 --risk-high 555 --breakdown-level 530
    python detect_crash_liquidity_breakdown.py --policy-stop true
"""

import argparse
import asyncio
import contextlib
import io
import json
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

try:
    from ib_async import IB, Stock, Future, Contract, Index
except ImportError:
    print(
        json.dumps(
            {
                "error": "ib_async not installed. Run: pip install ib_async",
                "status": "dependency_missing",
            }
        ),
        file=sys.stderr,
    )
    sys.exit(1)

DEFAULT_HOST = "192.168.2.187"
PORT_PAPER = 7497
PORT_LIVE = 7496
CLIENT_ID = 1018
CONNECT_TIMEOUT = 15
BAR_TIMEOUT = 30
MIN_PRIMARY_BARS = 260


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def _sma(values: list, n: int) -> Optional[float]:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _roc(values: list, period: int) -> Optional[float]:
    """Rate of change over ``period`` bars as percentage."""
    if len(values) < period + 1:
        return None
    base = values[-(period + 1)]
    if base == 0:
        return None
    return (values[-1] - base) / abs(base) * 100.0


# ---------------------------------------------------------------------------
# Pure function: calculate_indicators
# ---------------------------------------------------------------------------


def calculate_indicators(
    bars: list,
    vix_bars: Optional[list] = None,
    credit_bars: Optional[list] = None,
    breadth_bars: Optional[list] = None,
    config: Optional[dict] = None,
) -> dict:
    """Compute all strategy indicators from bar data.

    Each bar must have ``.open``, ``.high``, ``.low``, ``.close``, ``.volume``.
    Returns a dict with all computed features, or a dict with ``blocked=True``
    when the primary series is too short for the 200-day SMA / shelf calculation.
    """
    cfg = config or {}
    shelf_lookback = int(cfg.get("shelf_lookback", 63))
    break_threshold = float(cfg.get("break_threshold", 0.005))
    vix_warn_level = float(cfg.get("vix_warn_level", 25.0))
    vix_confirm_level = float(cfg.get("vix_confirm_level", 30.0))
    vix_roc_threshold = float(cfg.get("vix_roc_threshold", 20.0))
    tr_expansion_mult = float(cfg.get("tr_expansion_mult", 1.5))

    dq: dict = {}

    if len(bars) < MIN_PRIMARY_BARS:
        return {
            "blocked": True,
            "reason": "insufficient_primary_data",
            "bar_count": len(bars),
            "min_required": MIN_PRIMARY_BARS,
            "data_quality": {"primary_bars": len(bars), "min_required": MIN_PRIMARY_BARS},
        }

    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    opens = [b.open for b in bars]

    close = closes[-1]
    sma_200 = _sma(closes, 200)
    sma_50 = _sma(closes, 50)

    # Support shelf: 20th-percentile close in the lookback window.
    # This represents the floor that held ~80% of sessions — a meaningful support.
    shelf_window = sorted(closes[-shelf_lookback:])
    shelf_idx = max(0, int(len(shelf_window) * 0.20) - 1)
    support_shelf = shelf_window[shelf_idx]

    below_200dma = close < sma_200 if sma_200 is not None else None
    below_50dma = close < sma_50 if sma_50 is not None else None
    below_support_shelf = close < support_shelf

    # Shelf break: close is more than break_threshold below the shelf
    shelf_break = close < support_shelf * (1.0 - break_threshold)

    # Failed retest: price bounced above shelf at some point in last 20 bars
    # but the current close is back below shelf
    recent_20_closes = closes[-21:-1]
    recent_bounced = any(c > support_shelf for c in recent_20_closes)
    failed_retest = recent_bounced and below_support_shelf

    # Lower low / lower high (5-bar proxy)
    lower_low = len(closes) >= 6 and closes[-1] < closes[-6]
    lower_high = len(closes) >= 10 and max(closes[-5:-2]) < max(closes[-10:-7])

    # ATR and true range expansion
    trs = [
        _true_range(bars[i].high, bars[i].low, bars[i - 1].close)
        for i in range(1, len(bars))
    ]
    atr_20 = sum(trs[-20:]) / 20 if len(trs) >= 20 else None
    current_tr = trs[-1] if trs else None
    tr_expansion = (
        current_tr > tr_expansion_mult * atr_20
        if current_tr is not None and atr_20 is not None and atr_20 > 0
        else None
    )

    # Gap-down open: today's open is below the shelf (do_not_short_open_print territory)
    gap_down_open = opens[-1] < support_shelf

    # --- VIX indicators ---
    vix_level = vix_warning = vix_confirmation = None
    vix_roc_5d = vix_roc_confirmation = None
    if vix_bars and len(vix_bars) >= 2:
        vix_closes = [b.close for b in vix_bars]
        vix_level = vix_closes[-1]
        vix_warning = vix_level >= vix_warn_level
        vix_confirmation = vix_level >= vix_confirm_level
        vix_roc_5d = _roc(vix_closes, 5)
        vix_roc_confirmation = (
            vix_roc_5d >= vix_roc_threshold if vix_roc_5d is not None else None
        )
    else:
        dq["vix"] = "missing"

    # --- Credit indicators (HYG/JNK) ---
    credit_below_50dma = credit_below_200dma = credit_underperforming = None
    if credit_bars and len(credit_bars) >= 50:
        cr_closes = [b.close for b in credit_bars]
        cr_sma50 = _sma(cr_closes, 50)
        cr_sma200 = _sma(cr_closes, 200) if len(cr_closes) >= 200 else None
        credit_below_50dma = cr_closes[-1] < cr_sma50 if cr_sma50 else None
        credit_below_200dma = cr_closes[-1] < cr_sma200 if cr_sma200 else None
        cr_roc20 = _roc(cr_closes, 20)
        spy_roc20 = _roc(closes, 20)
        credit_underperforming = (
            cr_roc20 is not None
            and spy_roc20 is not None
            and cr_roc20 < spy_roc20 - 2.0
        )
    else:
        dq["credit"] = "missing"

    # --- Breadth proxy (RSP vs SPY) ---
    breadth_weak = None
    rsp_vs_spy_10d = rsp_vs_spy_20d = None
    if breadth_bars and len(breadth_bars) >= 21 and len(closes) >= 21:
        rsp_closes = [b.close for b in breadth_bars]
        rsp_roc10 = _roc(rsp_closes, 10)
        spy_roc10 = _roc(closes, 10)
        rsp_roc20 = _roc(rsp_closes, 20)
        spy_roc20 = _roc(closes, 20)
        if rsp_roc10 is not None and spy_roc10 is not None:
            rsp_vs_spy_10d = round(rsp_roc10 - spy_roc10, 4)
        if rsp_roc20 is not None and spy_roc20 is not None:
            rsp_vs_spy_20d = round(rsp_roc20 - spy_roc20, 4)
        breadth_weak = (
            (rsp_vs_spy_10d is not None and rsp_vs_spy_10d < -1.0)
            or (rsp_vs_spy_20d is not None and rsp_vs_spy_20d < -1.0)
        )
    else:
        dq["breadth"] = "missing"

    return {
        "close": close,
        "sma_200": sma_200,
        "sma_50": sma_50,
        "support_shelf": support_shelf,
        "below_200dma": below_200dma,
        "below_50dma": below_50dma,
        "below_support_shelf": below_support_shelf,
        "shelf_break": shelf_break,
        "failed_retest": failed_retest,
        "lower_low": lower_low,
        "lower_high": lower_high,
        "gap_down_open": gap_down_open,
        "atr_20": atr_20,
        "current_tr": current_tr,
        "tr_expansion": tr_expansion,
        "vix_level": vix_level,
        "vix_warning": vix_warning,
        "vix_confirmation": vix_confirmation,
        "vix_roc_5d": vix_roc_5d,
        "vix_roc_confirmation": vix_roc_confirmation,
        "credit_below_50dma": credit_below_50dma,
        "credit_below_200dma": credit_below_200dma,
        "credit_underperforming": credit_underperforming,
        "rsp_vs_spy_10d": rsp_vs_spy_10d,
        "rsp_vs_spy_20d": rsp_vs_spy_20d,
        "breadth_weak": breadth_weak,
        "bar_count": len(bars),
        "data_quality": dq,
    }


# ---------------------------------------------------------------------------
# Pure function: evaluate_crash_playbook
# ---------------------------------------------------------------------------


def evaluate_crash_playbook(
    features: dict,
    position_context: Optional[dict] = None,
    policy_context: Optional[dict] = None,
) -> dict:
    """Evaluate PTJ crash playbook state from computed features.

    Signal states (in priority order):
      blocked_missing_data → insufficient primary data
      de_risk_exit         → exit conditions met (reclaim, policy stop, vol compression)
      entry_trigger_short  → break + failed retest + ≥2 confirmations
      setup_armed          → below 200dma or shelf + ≥2 confirmations
      manage_open_short    → open short, profitable, lower highs still in play
      watchlist_deterioration → partial deterioration; no full setup
      no_setup             → no deterioration cluster
    """
    pos = position_context or {}
    pol = policy_context or {}

    if features.get("blocked"):
        return {
            "signal_state": "blocked_missing_data",
            "confidence": 0.0,
            "reason": features.get("reason", ""),
            "trade_posture": "no_trade",
            "scorecard": {},
            "market_snapshot": {},
            "risk_points": ["insufficient primary data — cannot compute indicators"],
            "actions": [],
            "data_quality": {
                "missing_optional": [],
                "degraded": True,
                "bar_count": features.get("bar_count"),
                "min_required": features.get("min_required"),
            },
        }

    dq = features.get("data_quality", {})
    missing = list(dq.keys())

    # --- Count confirmations ---
    # Volatility: VIX ≥30, or VIX 5d ROC ≥20%, or TR > 1.5x ATR
    vol_confirmed = bool(
        features.get("vix_confirmation")
        or features.get("vix_roc_confirmation")
        or features.get("tr_expansion")
    )
    vol_warned = bool(features.get("vix_warning"))

    # Breadth: RSP underperforming SPY
    breadth_confirmed = bool(features.get("breadth_weak")) if features.get("breadth_weak") is not None else False

    # Credit: HYG/JNK below SMA or underperforming
    credit_confirmed = bool(
        features.get("credit_below_50dma")
        or features.get("credit_below_200dma")
        or features.get("credit_underperforming")
    )

    confirmations = sum([vol_confirmed, breadth_confirmed, credit_confirmed])

    scorecard = {
        "price_below_200dma": features.get("below_200dma"),
        "price_below_support_shelf": features.get("below_support_shelf"),
        "shelf_break": features.get("shelf_break"),
        "failed_retest": features.get("failed_retest"),
        "lower_low": features.get("lower_low"),
        "lower_high": features.get("lower_high"),
        "volatility_confirmed": vol_confirmed,
        "breadth_weak": features.get("breadth_weak"),
        "credit_stressed": credit_confirmed,
        "confirmations": confirmations,
        "gap_down_open": features.get("gap_down_open"),
        "tr_expansion": features.get("tr_expansion"),
    }

    market_snap = {
        "close": features.get("close"),
        "sma_200": round(features["sma_200"], 4) if features.get("sma_200") is not None else None,
        "sma_50": round(features["sma_50"], 4) if features.get("sma_50") is not None else None,
        "support_shelf": (
            round(features["support_shelf"], 4) if features.get("support_shelf") is not None else None
        ),
        "vix_level": features.get("vix_level"),
        "atr_20": round(features["atr_20"], 4) if features.get("atr_20") is not None else None,
    }

    data_quality = {
        "missing_optional": missing,
        "degraded": bool(missing),
        "bar_count": features.get("bar_count"),
    }

    # Policy stop always overrides — highest priority
    if pol.get("policy_stop"):
        return _build_result(
            state="de_risk_exit",
            scorecard=scorecard,
            market_snap=market_snap,
            data_quality=data_quality,
            pos=pos,
            features=features,
            reason="policy_stop_triggered",
            posture="exit_all_positions",
            confidence=1.0,
            missing=missing,
        )

    position_side = pos.get("position_side")
    entry_price = pos.get("entry_price")
    risk_high = pos.get("risk_high")

    # De-risk exits for open shorts
    if position_side == "short":
        close = features.get("close", 0.0)
        # risk_high stop does not require entry_price — price comparison is self-contained
        if risk_high is not None and close > risk_high:
            return _build_result(
                state="de_risk_exit",
                scorecard=scorecard,
                market_snap=market_snap,
                data_quality=data_quality,
                pos=pos,
                features=features,
                reason="close_above_risk_high",
                posture="exit_short",
                confidence=0.95,
                missing=missing,
            )
        if entry_price is not None:
            # Volatility compression: VIX well below warning level (two-session proxy)
            vix = features.get("vix_level")
            if vix is not None and vix < 20.0:
                return _build_result(
                    state="de_risk_exit",
                    scorecard=scorecard,
                    market_snap=market_snap,
                    data_quality=data_quality,
                    pos=pos,
                    features=features,
                    reason="volatility_compressed",
                    posture="reduce_short",
                    confidence=0.75,
                    missing=missing,
                )

    # Primary signal evaluation
    # entry_trigger_short requires an actual shelf break; 200DMA-only is setup_armed territory
    price_broken = features.get("shelf_break")
    price_deteriorating = features.get("below_support_shelf") or features.get("below_200dma")

    # Manage an open short before checking for new entry signals — an existing position
    # in a still-bearish market should receive management actions, not entry actions.
    if position_side == "short" and price_deteriorating:
        profitable = (entry_price is not None and features.get("close", 0) < entry_price)
        if profitable and features.get("lower_high"):
            confidence = 0.80 * max(0.5, 1.0 - len(missing) * 0.10)
            state = "manage_open_short"
            posture = "trail_stop_above_lower_high"
        else:
            confidence = 0.50
            state = "watchlist_deterioration"
            posture = "monitor_closely"

    elif (
        price_broken
        and confirmations >= 2
        and not features.get("gap_down_open")
        and (features.get("failed_retest") or features.get("tr_expansion"))
    ):
        # Entry trigger: break + failed retest or decisive range expansion + ≥2 confirmations, no gap open
        confidence = min(0.92, 0.60 + confirmations * 0.10 + (0.08 if features.get("lower_high") else 0))
        confidence *= max(0.5, 1.0 - len(missing) * 0.10)
        state = "entry_trigger_short"
        posture = "initiate_short_0.33_unit"

    elif price_broken and features.get("gap_down_open") and confirmations >= 1:
        # Gap below support: armed but waiting for reclaim/retest confirmation
        confidence = 0.55 * max(0.5, 1.0 - len(missing) * 0.10)
        state = "setup_armed"
        posture = "prepare_short_entry_post_gap"

    elif price_deteriorating and confirmations >= 2:
        # Setup armed: deterioration cluster forming
        confidence = min(0.78, 0.50 + confirmations * 0.10)
        confidence *= max(0.5, 1.0 - len(missing) * 0.10)
        state = "setup_armed"
        posture = "prepare_short_entry"

    elif price_deteriorating or vol_warned or confirmations >= 1:
        # Watchlist: some deterioration but not enough for full setup
        confidence = min(0.60, 0.30 + confirmations * 0.12)
        confidence *= max(0.5, 1.0 - len(missing) * 0.08)
        state = "watchlist_deterioration"
        posture = "monitor_closely"

    else:
        # No setup
        confidence = 0.85 * max(0.5, 1.0 - len(missing) * 0.05)
        state = "no_setup"
        posture = "flat_no_trade"

    return _build_result(
        state=state,
        scorecard=scorecard,
        market_snap=market_snap,
        data_quality=data_quality,
        pos=pos,
        features=features,
        posture=posture,
        confidence=confidence,
        missing=missing,
    )


def _build_result(
    state: str,
    scorecard: dict,
    market_snap: dict,
    data_quality: dict,
    pos: dict,
    features: dict,
    missing: list,
    reason: str = "",
    posture: str = "",
    confidence: float = 0.0,
) -> dict:
    risk_points: List[str] = []

    if features.get("gap_down_open"):
        risk_points.append(
            "gap_down_open: do not short open print — "
            "wait for failed VWAP reclaim or use defined-risk options"
        )
    risk_high = pos.get("risk_high")
    if risk_high is not None:
        risk_points.append(f"stop: close above {risk_high:.2f} triggers de_risk_exit")
    elif state in ("entry_trigger_short", "setup_armed") and features.get("support_shelf"):
        risk_points.append(
            f"invalidation: close above support shelf {features['support_shelf']:.2f}"
        )
    risk_points.append(
        "policy stop: central-bank / fiscal intervention or short-sale restriction → force exit"
    )

    actions = _derive_actions(state, features, pos)

    return {
        "signal_state": state,
        "confidence": round(confidence, 3),
        "reason": reason,
        "trade_posture": posture,
        "scorecard": scorecard,
        "market_snapshot": market_snap,
        "risk_points": risk_points,
        "actions": actions,
        "data_quality": data_quality,
    }


def _derive_actions(state: str, features: dict, pos: dict) -> list:
    if state == "no_setup":
        return ["watch_for_deterioration_cluster"]
    if state == "watchlist_deterioration":
        return ["monitor_vix_credit_breadth", "mark_support_shelf_levels"]
    if state == "setup_armed":
        if features.get("gap_down_open"):
            return [
                "wait_for_failed_vwap_reclaim_or_failed_retest",
                "consider_defined_risk_options_after_iv_normalises",
            ]
        return ["wait_for_failed_retest_below_shelf", "define_stop_before_entry"]
    if state == "entry_trigger_short":
        return [
            "short_0.33_to_0.50_risk_unit_on_failed_retest",
            "set_stop_above_retest_high",
            "add_only_on_lower_high_after_lower_low",
        ]
    if state == "manage_open_short":
        return [
            "trail_stop_above_most_recent_lower_high",
            "partial_cover_into_2r_3r_extension",
            "do_not_add_on_first_vertical_selloff",
        ]
    if state == "de_risk_exit":
        return [
            "exit_short_position_or_reduce_to_flat",
            "do_not_re_enter_without_new_setup_confirmation",
        ]
    return []


# ---------------------------------------------------------------------------
# Pure function: format_llm_output
# ---------------------------------------------------------------------------

_STATE_LABELS = {
    "no_setup": "NO SETUP — market not in crash/liquidity breakdown pattern",
    "watchlist_deterioration": "WATCHLIST — early deterioration signals; no full setup yet",
    "setup_armed": "SETUP ARMED — deterioration cluster forming; awaiting trigger",
    "entry_trigger_short": "ENTRY TRIGGER — failed retest confirmed; short entry valid",
    "manage_open_short": "MANAGE OPEN SHORT — position intact; trail stops, partial cover",
    "de_risk_exit": "DE-RISK / EXIT — exit conditions met; reduce or flatten",
    "blocked_missing_data": "BLOCKED — insufficient primary data for evaluation",
}


def format_llm_output(evaluation: dict) -> str:
    """Return human-readable text followed by a fenced JSON block."""
    state = evaluation.get("signal_state", "unknown")
    confidence = evaluation.get("confidence", 0.0)
    posture = evaluation.get("trade_posture", "undetermined")
    snap = evaluation.get("market_snapshot", {})
    sc = evaluation.get("scorecard", {})
    risk_pts = evaluation.get("risk_points", [])
    actions = evaluation.get("actions", [])
    dq = evaluation.get("data_quality", {})
    reason = evaluation.get("reason", "")

    lines = [
        "=" * 64,
        "PTJ CRASH PLAYBOOK / LIQUIDITY BREAKDOWN DETECTOR",
        "=" * 64,
        f"State:      {_STATE_LABELS.get(state, state)}",
        f"Confidence: {confidence:.0%}",
    ]
    if reason:
        lines.append(f"Reason:     {reason}")
    lines.append("")

    lines.append("── Desk Read ──")
    if snap.get("close") is not None:
        lines.append(f"  Close:          {snap['close']:.2f}")
    if snap.get("sma_200") is not None:
        flag = " ⚠ BELOW" if sc.get("price_below_200dma") else ""
        lines.append(f"  200-day SMA:    {snap['sma_200']:.2f}{flag}")
    if snap.get("support_shelf") is not None:
        if sc.get("shelf_break"):
            flag = " ⚠ BROKEN"
        elif sc.get("price_below_support_shelf"):
            flag = " (price below)"
        else:
            flag = ""
        lines.append(f"  Support shelf:  {snap['support_shelf']:.2f}{flag}")
    if snap.get("vix_level") is not None:
        vix = snap["vix_level"]
        if sc.get("volatility_confirmed"):
            flag = " 🔴 CONFIRMED"
        elif vix >= 25:
            flag = " ⚠ WARNING"
        else:
            flag = ""
        lines.append(f"  VIX:            {vix:.1f}{flag}")
    confirmations = sc.get("confirmations", 0)
    lines.append(f"  Confirmations:  {confirmations}/3  (volatility · breadth · credit)")
    if dq.get("missing_optional"):
        lines.append(
            f"  Missing data:   {', '.join(dq['missing_optional'])}  (degraded confidence)"
        )
    lines.append("")

    lines.append("── Trade Posture ──")
    lines.append(f"  {posture}")
    lines.append("")

    if risk_pts:
        lines.append("── Risk Points ──")
        for rp in risk_pts:
            lines.append(f"  • {rp}")
        lines.append("")

    if actions:
        lines.append("── Next Actions ──")
        for a in actions:
            lines.append(f"  → {a}")
        lines.append("")

    payload = {
        "tool": "detect_crash_liquidity_breakdown",
        "version": "1.0.0",
        "timestamp": utc_timestamp(),
        "strategy": "ptj_crash_playbook_liquidity_breakdown",
        "signal_state": state,
        "confidence": confidence,
        "trade_posture": posture,
        "market_snapshot": snap,
        "scorecard": sc,
        "risk_points": risk_pts,
        "actions": actions,
        "data_quality": dq,
    }

    lines.append("```json")
    lines.append(json.dumps(payload, indent=2))
    lines.append("```")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# TWS adapter
# ---------------------------------------------------------------------------


def _build_contract(args) -> object:
    sec_type = args.sec_type.upper()
    if sec_type == "STK":
        return Stock(args.symbol, args.exchange or "SMART", args.currency)
    if sec_type == "FUT":
        return Future(
            args.symbol,
            args.expiry or "",
            args.exchange or "GLOBEX",
            currency=args.currency,
        )
    c = Contract()
    c.symbol = args.symbol
    c.secType = sec_type
    c.currency = args.currency
    c.exchange = args.exchange or "SMART"
    if args.expiry:
        c.lastTradeDateOrContractMonth = args.expiry
    return c


async def _fetch_daily_bars(ib, contract, duration: str = "2 Y") -> list:
    what_to_show = (
        "ADJUSTED_LAST"
        if getattr(contract, "secType", "STK") == "STK"
        else "TRADES"
    )
    bars = await ib.reqHistoricalDataAsync(
        contract,
        endDateTime="",
        durationStr=duration,
        barSizeSetting="1 day",
        whatToShow=what_to_show,
        useRTH=True,
        formatDate=1,
        keepUpToDate=False,
    )
    return list(bars) if bars else []


async def run_detector(host: str, port: int, args) -> dict:
    """Connect to TWS, fetch bars, run the crash playbook evaluation."""
    ib = IB()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            await asyncio.wait_for(
                ib.connectAsync(host, port, clientId=CLIENT_ID, readonly=True),
                timeout=CONNECT_TIMEOUT,
            )
    except asyncio.TimeoutError:
        raise ConnectionError(f"Timed out connecting to TWS at {host}:{port}")
    except Exception as exc:
        raise ConnectionError(f"Cannot reach TWS at {host}:{port} — {exc}") from exc

    try:
        contract = _build_contract(args)
        with contextlib.redirect_stdout(sys.stderr):
            try:
                await ib.qualifyContractsAsync(contract)
            except Exception:
                pass

        bars = await asyncio.wait_for(_fetch_daily_bars(ib, contract), timeout=BAR_TIMEOUT)

        vix_bars = None
        if args.vix_symbol:
            vix_contract = Index(args.vix_symbol, "CBOE", "USD")
            vix_bars = await asyncio.wait_for(
                _fetch_daily_bars(ib, vix_contract), timeout=BAR_TIMEOUT
            )

        credit_bars = None
        if args.credit_symbol:
            credit_contract = Stock(args.credit_symbol, "SMART", "USD")
            credit_bars = await asyncio.wait_for(
                _fetch_daily_bars(ib, credit_contract), timeout=BAR_TIMEOUT
            )

        breadth_bars = None
        if args.breadth_symbol:
            breadth_contract = Stock(args.breadth_symbol, "SMART", "USD")
            breadth_bars = await asyncio.wait_for(
                _fetch_daily_bars(ib, breadth_contract), timeout=BAR_TIMEOUT
            )

        config = {
            "shelf_lookback": args.shelf_lookback,
            "break_threshold": args.break_threshold,
        }

        position_context = None
        if args.position_side:
            position_context = {
                "position_side": args.position_side,
                "entry_price": args.entry_price,
                "risk_high": args.risk_high,
                "breakdown_level": args.breakdown_level,
            }

        policy_context = {"policy_stop": args.policy_stop}

        features = calculate_indicators(bars, vix_bars, credit_bars, breadth_bars, config)
        return evaluate_crash_playbook(features, position_context, policy_context)

    finally:
        ib.disconnect()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PTJ Crash Playbook / Liquidity Breakdown Detector.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python detect_crash_liquidity_breakdown.py
  python detect_crash_liquidity_breakdown.py --symbol SPY --live
  python detect_crash_liquidity_breakdown.py --symbol ES --sec-type FUT --expiry 202506
  python detect_crash_liquidity_breakdown.py \\
      --vix-symbol VIX --credit-symbol HYG --breadth-symbol RSP
  python detect_crash_liquidity_breakdown.py \\
      --position-side short --entry-price 540 --risk-high 555 --breakdown-level 530
  python detect_crash_liquidity_breakdown.py --policy-stop true
        """,
    )

    parser.add_argument("--host", default=DEFAULT_HOST, help="TWS hostname or IP")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--paper", dest="port", action="store_const", const=PORT_PAPER)
    mode.add_argument("--live", dest="port", action="store_const", const=PORT_LIVE)
    mode.add_argument("--port", dest="port", type=int, help="Custom port")
    parser.set_defaults(port=PORT_PAPER)

    parser.add_argument("--symbol", default="SPY", help="Primary index symbol (default: SPY)")
    parser.add_argument("--sec-type", default="STK", dest="sec_type",
                        help="Security type: STK or FUT (default: STK)")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--exchange", default="SMART")
    parser.add_argument("--expiry", default=None, help="Contract expiry YYYYMM (FUT only)")

    parser.add_argument("--vix-symbol", default=None, dest="vix_symbol",
                        help="Volatility index symbol, e.g. VIX")
    parser.add_argument("--credit-symbol", default=None, dest="credit_symbol",
                        help="Credit proxy ETF, e.g. HYG or JNK")
    parser.add_argument("--breadth-symbol", default=None, dest="breadth_symbol",
                        help="Breadth proxy ETF, e.g. RSP")

    parser.add_argument("--shelf-lookback", type=int, default=63, dest="shelf_lookback",
                        help="Session lookback for support shelf (default: 63)")
    parser.add_argument("--break-threshold", type=float, default=0.005, dest="break_threshold",
                        help="Shelf break fraction (default: 0.005 = 0.5%%)")

    parser.add_argument("--position-side", choices=["short", "flat"], default=None,
                        dest="position_side", help="Current position: short or flat")
    parser.add_argument("--entry-price", type=float, default=None, dest="entry_price",
                        help="Short entry price for de-risk evaluation")
    parser.add_argument("--risk-high", type=float, default=None, dest="risk_high",
                        help="Stop level: close above this triggers de_risk_exit")
    parser.add_argument("--breakdown-level", type=float, default=None, dest="breakdown_level",
                        help="Key breakdown level for management context")

    parser.add_argument(
        "--policy-stop",
        type=lambda x: x.lower() in ("1", "true", "yes"),
        default=False,
        dest="policy_stop",
        help="Emergency policy stop flag — forces de_risk_exit when true",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        evaluation = asyncio.run(run_detector(args.host, args.port, args))
    except ConnectionError as exc:
        print(
            json.dumps(
                {"error": str(exc), "status": "tws_unavailable", "timestamp": utc_timestamp()}
            ),
            file=sys.stderr,
        )
        sys.exit(3)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error": f"Unexpected error: {exc}",
                    "status": "error",
                    "timestamp": utc_timestamp(),
                }
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    print(format_llm_output(evaluation))

    if evaluation.get("signal_state") == "blocked_missing_data":
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
