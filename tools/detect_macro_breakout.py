#!/usr/bin/env python3
"""
detect_macro_breakout.py — Signal detector for the PTJ Macro Breakout With
Asymmetric Risk strategy.

Strategy thesis: When a major macro market breaks from a well-defined range in the
direction of the dominant fundamental trend, take a liquid directional position with
a nearby invalidation point and add only if price confirms.

Pure-function module: no live trading, no IB connection required for backtesting.
Import and call calculate_indicators() + evaluate_breakout_signal() directly.

Signal states emitted by evaluate_breakout_signal():
  no_setup             : Market not compressing; no macro driver aligned.
  range_forming        : Price compressing (ATR contraction and/or narrow range).
  breakout_candidate   : Range tight + macro driver aligned; watching for break.
  entry_trigger_long   : Two-step confirmed bullish breakout; probe long.
  entry_trigger_short  : Two-step confirmed bearish breakdown; probe short.
  add_unit             : In a profitable position; add is appropriate.
  trail_stop           : Position profitable beyond 2R; trail stops.
  exit_signal          : Stop hit or price closed back inside range (failed break).
"""

from typing import Optional


# ---------------------------------------------------------------------------
# Defaults — all tunable but not optimised for backtesting
# ---------------------------------------------------------------------------

RANGE_LOOKBACK = 20         # bars defining the consolidation range (≈1 month)
RANGE_TIGHT_PCT = 0.06      # range_width / range_mid < 6% → tight compression
RANGE_MODERATE_PCT = 0.10   # range_width / range_mid < 10% → moderate compression
ATR_PERIOD_SHORT = 20       # short ATR period
ATR_PERIOD_LONG = 60        # long ATR period for compression ratio
ATR_COMPRESS_RATIO = 0.85   # ATR_short/ATR_long < 0.85 → compression
VOL_EXPANSION_MULT = 1.5    # volume > 1.5x 20-day avg → expansion
MA_LONG = 200               # long-term trend filter
MA_MEDIUM = 50              # medium-term trend filter
MA_SLOPE_LOOKBACK = 10      # bars to measure MA slope
CONFIRM_LOOKBACK = 5        # bars for confirmation asset momentum
CONFIRM_THRESHOLD = 0.005   # |momentum| > 0.5% to count as a signal
MIN_CONFIRMS_LONG = 3       # macro confirms needed for long entry
MIN_CONFIRMS_SHORT = 3      # macro confirms needed for short entry
GAP_THRESHOLD = 0.005       # 0.5% gap to exclude entry direction
RETEST_TOLERANCE = 0.005    # 0.5% tolerance when checking retest hold


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sma(series: list, period: int) -> Optional[float]:
    if len(series) < period:
        return None
    return sum(series[-period:]) / period


def _atr(bars: list, period: int) -> Optional[float]:
    if len(bars) < period + 1:
        return None
    trs = []
    start = max(1, len(bars) - period)
    for i in range(start, len(bars)):
        tr = max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - bars[i - 1].close),
            abs(bars[i].low - bars[i - 1].close),
        )
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None


def _momentum(closes: list, period: int) -> Optional[float]:
    """Return fractional change: (last - prev) / prev over `period` bars."""
    if len(closes) <= period:
        return None
    old = closes[-(period + 1)]
    if old == 0:
        return None
    return (closes[-1] - old) / old


# ---------------------------------------------------------------------------
# Public API: calculate_indicators
# ---------------------------------------------------------------------------

def calculate_indicators(
    primary_bars: list,
    rate_bars: Optional[list] = None,
    dollar_bars: Optional[list] = None,
    credit_bars: Optional[list] = None,
) -> dict:
    """
    Compute all macro-breakout indicators from daily bar history.

    Parameters
    ----------
    primary_bars : list[SimpleNamespace]
        Daily OHLCV bars (date, open, high, low, close, volume).
        At least MA_LONG + RANGE_LOOKBACK + 10 bars required for full output.
    rate_bars    : list[SimpleNamespace] | None
        TLT daily bars.  Rising TLT = falling yields = accommodative (bullish).
    dollar_bars  : list[SimpleNamespace] | None
        UUP daily bars.  Rising UUP = stronger dollar = risk-off (bearish).
    credit_bars  : list[SimpleNamespace] | None
        HYG daily bars.  Rising HYG = tightening spreads = risk-on (bullish).

    Returns
    -------
    dict
        All intermediate values consumed by evaluate_breakout_signal().
        Returns empty dict when primary_bars is too short.
    """
    features: dict = {}

    min_bars = MA_LONG + RANGE_LOOKBACK + 5
    if not primary_bars or len(primary_bars) < min_bars:
        return features

    closes = [b.close for b in primary_bars]
    highs = [b.high for b in primary_bars]
    lows = [b.low for b in primary_bars]
    volumes = [b.volume for b in primary_bars]
    n = len(closes)

    close = closes[-1]
    open_ = primary_bars[-1].open
    prev_close = closes[-2]

    # -------------------------------------------------------------------
    # Moving averages and trend
    # -------------------------------------------------------------------
    ma200 = _sma(closes, MA_LONG)
    ma50 = _sma(closes, MA_MEDIUM)
    ma20 = _sma(closes, RANGE_LOOKBACK)

    features["close"] = close
    features["ma200"] = ma200
    features["ma50"] = ma50
    features["ma20"] = ma20
    features["above_200dma"] = (close > ma200) if ma200 is not None else None
    features["above_50dma"] = (close > ma50) if ma50 is not None else None

    # MA slope: compare to MA value MA_SLOPE_LOOKBACK bars ago
    if len(closes) >= MA_LONG + MA_SLOPE_LOOKBACK:
        ma200_prev = _sma(closes[:-MA_SLOPE_LOOKBACK], MA_LONG)
        features["ma200_slope_up"] = (ma200 > ma200_prev) if (ma200 and ma200_prev) else None
    else:
        features["ma200_slope_up"] = None

    if len(closes) >= MA_MEDIUM + MA_SLOPE_LOOKBACK:
        ma50_prev = _sma(closes[:-MA_SLOPE_LOOKBACK], MA_MEDIUM)
        features["ma50_slope_up"] = (ma50 > ma50_prev) if (ma50 and ma50_prev) else None
    else:
        features["ma50_slope_up"] = None

    # Higher highs / lower lows over recent 10 bars (split into two halves)
    if n >= 10:
        half = 5
        early_highs = highs[-10:-half]
        late_highs = highs[-half:]
        early_lows = lows[-10:-half]
        late_lows = lows[-half:]
        features["higher_high"] = max(late_highs) > max(early_highs)
        features["higher_low"] = min(late_lows) > min(early_lows)
        features["lower_high"] = max(late_highs) < max(early_highs)
        features["lower_low"] = min(late_lows) < min(early_lows)
    else:
        features["higher_high"] = None
        features["higher_low"] = None
        features["lower_high"] = None
        features["lower_low"] = None

    # -------------------------------------------------------------------
    # Range / consolidation detection
    # Excludes yesterday AND today: the range is the consolidation PRIOR to any
    # breakout attempt.  Using highs[-(RANGE_LOOKBACK+2):-2] means the last bar
    # in the range is bars[-3] (2 trading days before today), so yesterday's
    # close can cleanly be compared against this level for the retest-hold check
    # without circular dependency.
    # -------------------------------------------------------------------
    lb_start = -(RANGE_LOOKBACK + 2)
    range_highs = highs[lb_start:-2] if len(highs) >= RANGE_LOOKBACK + 2 else []
    range_lows = lows[lb_start:-2] if len(lows) >= RANGE_LOOKBACK + 2 else []

    range_high = max(range_highs) if range_highs else None
    range_low = min(range_lows) if range_lows else None
    range_mid = ((range_high + range_low) / 2) if (range_high and range_low) else None

    features["range_high"] = range_high
    features["range_low"] = range_low
    features["range_mid"] = range_mid

    range_width_pct: Optional[float] = None
    if range_high and range_low and range_mid and range_mid > 0:
        range_width_pct = (range_high - range_low) / range_mid
    features["range_width_pct"] = range_width_pct
    features["range_tight"] = (range_width_pct < RANGE_TIGHT_PCT) if range_width_pct is not None else None
    features["range_moderate"] = (range_width_pct < RANGE_MODERATE_PCT) if range_width_pct is not None else None

    # -------------------------------------------------------------------
    # ATR compression
    # -------------------------------------------------------------------
    atr20 = _atr(primary_bars, ATR_PERIOD_SHORT)
    atr60 = _atr(primary_bars, ATR_PERIOD_LONG)
    features["atr20"] = atr20
    features["atr60"] = atr60
    features["atr_compressed"] = (
        (atr20 / atr60 < ATR_COMPRESS_RATIO)
        if (atr20 and atr60 and atr60 > 0)
        else None
    )

    # -------------------------------------------------------------------
    # Breakout detection — close vs range boundary
    # -------------------------------------------------------------------
    features["breakout_long"] = (close > range_high) if range_high else None
    features["breakout_short"] = (close < range_low) if range_low else None

    # -------------------------------------------------------------------
    # Gap detection
    # -------------------------------------------------------------------
    features["gap_up_open"] = bool(open_ > prev_close * (1 + GAP_THRESHOLD))
    features["gap_down_open"] = bool(open_ < prev_close * (1 - GAP_THRESHOLD))

    # -------------------------------------------------------------------
    # Two-step confirmation: retest hold
    # Pattern: yesterday closed outside range; today's session held the level
    # Long: prev_close > range_high AND today's low stayed above (range_high - tol)
    # Short: prev_close < range_low  AND today's high stayed below (range_low + tol)
    # -------------------------------------------------------------------
    today_low = primary_bars[-1].low
    today_high = primary_bars[-1].high

    if range_high and n >= 2:
        prev_above = closes[-2] > range_high
        today_held = today_low >= range_high * (1 - RETEST_TOLERANCE)
        features["retest_hold_long"] = bool(prev_above and today_held and not features["gap_down_open"])
    else:
        features["retest_hold_long"] = False

    if range_low and n >= 2:
        prev_below = closes[-2] < range_low
        today_held = today_high <= range_low * (1 + RETEST_TOLERANCE)
        features["retest_hold_short"] = bool(prev_below and today_held and not features["gap_up_open"])
    else:
        features["retest_hold_short"] = False

    # Failed breakout: price returned inside range after a prior breakout
    if range_high and range_low:
        features["failed_breakout_long"] = bool(
            closes[-2] > range_high and close < range_high
        )
        features["failed_breakout_short"] = bool(
            closes[-2] < range_low and close > range_low
        )
    else:
        features["failed_breakout_long"] = False
        features["failed_breakout_short"] = False

    # -------------------------------------------------------------------
    # Volume expansion
    # -------------------------------------------------------------------
    avg_vol = _sma(volumes[:-1], ATR_PERIOD_SHORT)
    features["volume_expansion"] = (
        (volumes[-1] > avg_vol * VOL_EXPANSION_MULT)
        if (avg_vol and avg_vol > 0)
        else None
    )

    # -------------------------------------------------------------------
    # Macro confirmation: rates (TLT)
    # Rising TLT = falling yields = dovish = bullish confirmation for longs
    # Falling TLT = rising yields = hawkish = bearish confirmation for shorts
    # -------------------------------------------------------------------
    if rate_bars and len(rate_bars) > CONFIRM_LOOKBACK:
        rate_closes = [b.close for b in rate_bars]
        rate_mom = _momentum(rate_closes, CONFIRM_LOOKBACK)
        features["rate_momentum"] = rate_mom
        features["rates_rising"] = (rate_mom > CONFIRM_THRESHOLD) if rate_mom is not None else None
        features["rates_falling"] = (rate_mom < -CONFIRM_THRESHOLD) if rate_mom is not None else None
    else:
        features["rate_momentum"] = None
        features["rates_rising"] = None
        features["rates_falling"] = None

    # -------------------------------------------------------------------
    # Macro confirmation: dollar (UUP)
    # Falling dollar = risk-on = bullish for equities (long confirm)
    # Rising dollar = risk-off = bearish for equities (short confirm)
    # -------------------------------------------------------------------
    if dollar_bars and len(dollar_bars) > CONFIRM_LOOKBACK:
        dollar_closes = [b.close for b in dollar_bars]
        dollar_mom = _momentum(dollar_closes, CONFIRM_LOOKBACK)
        features["dollar_momentum"] = dollar_mom
        features["dollar_weakening"] = (dollar_mom < -CONFIRM_THRESHOLD) if dollar_mom is not None else None
        features["dollar_strengthening"] = (dollar_mom > CONFIRM_THRESHOLD) if dollar_mom is not None else None
    else:
        features["dollar_momentum"] = None
        features["dollar_weakening"] = None
        features["dollar_strengthening"] = None

    # -------------------------------------------------------------------
    # Macro confirmation: credit spreads (HYG)
    # Rising HYG = tightening spreads = risk-on = bullish for equities (long)
    # Falling HYG = widening spreads = risk-off = bearish (short confirm)
    # -------------------------------------------------------------------
    if credit_bars and len(credit_bars) > CONFIRM_LOOKBACK:
        credit_closes = [b.close for b in credit_bars]
        credit_mom = _momentum(credit_closes, CONFIRM_LOOKBACK)
        features["credit_momentum"] = credit_mom
        features["credit_supportive"] = (credit_mom > CONFIRM_THRESHOLD) if credit_mom is not None else None
        features["credit_deteriorating"] = (credit_mom < -CONFIRM_THRESHOLD) if credit_mom is not None else None
    else:
        features["credit_momentum"] = None
        features["credit_supportive"] = None
        features["credit_deteriorating"] = None

    # -------------------------------------------------------------------
    # Confirmation tallies
    # -------------------------------------------------------------------
    long_confirms = 0
    short_confirms = 0

    # Trend: above 200dma
    if features.get("above_200dma") is True:
        long_confirms += 1
    elif features.get("above_200dma") is False:
        short_confirms += 1

    # MA slope
    if features.get("ma200_slope_up") is True:
        long_confirms += 1
    elif features.get("ma200_slope_up") is False:
        short_confirms += 1

    # Price structure: higher highs/lows
    if features.get("higher_high") and features.get("higher_low"):
        long_confirms += 1
    if features.get("lower_high") and features.get("lower_low"):
        short_confirms += 1

    # Rates
    if features.get("rates_rising") is True:
        long_confirms += 1
    elif features.get("rates_falling") is True:
        short_confirms += 1

    # Dollar
    if features.get("dollar_weakening") is True:
        long_confirms += 1
    elif features.get("dollar_strengthening") is True:
        short_confirms += 1

    # Credit
    if features.get("credit_supportive") is True:
        long_confirms += 1
    elif features.get("credit_deteriorating") is True:
        short_confirms += 1

    features["long_confirmations"] = long_confirms
    features["short_confirmations"] = short_confirms

    # -------------------------------------------------------------------
    # Stop levels (structural, not ATR-based)
    # Long stop: below range_high - noise (0.5 ATR wiggle room)
    # Short stop: above range_low + noise
    # -------------------------------------------------------------------
    if range_high and atr20:
        features["long_stop"] = range_high - atr20 * 0.5
    else:
        features["long_stop"] = None

    if range_low and atr20:
        features["short_stop"] = range_low + atr20 * 0.5
    else:
        features["short_stop"] = None

    features["r_unit"] = atr20  # 1R ≈ ATR(20) for stop sizing

    return features


# ---------------------------------------------------------------------------
# Public API: evaluate_breakout_signal
# ---------------------------------------------------------------------------

def evaluate_breakout_signal(
    features: dict,
    position_context: Optional[dict] = None,
) -> dict:
    """
    Evaluate the macro breakout signal state from pre-computed features.

    Parameters
    ----------
    features : dict
        Output of calculate_indicators().
    position_context : dict | None
        When in a live position, provide:
          {"position_side": "long"|"short",
           "entry_price": float,
           "stop_level": float,
           "bars_held": int,
           "r_unit": float}

    Returns
    -------
    dict
        {"signal_state": str, "confidence": float, "scorecard": dict}
    """
    if not features:
        return {"signal_state": "no_setup", "confidence": 0.0, "scorecard": {}}

    scorecard: dict = {
        "range_compressed": 0,
        "atr_compressed": 0,
        "long_confirms": features.get("long_confirmations", 0),
        "short_confirms": features.get("short_confirmations", 0),
        "breakout_long": int(bool(features.get("breakout_long"))),
        "breakout_short": int(bool(features.get("breakout_short"))),
        "volume_expansion": int(bool(features.get("volume_expansion"))),
        "retest_hold_long": int(bool(features.get("retest_hold_long"))),
        "retest_hold_short": int(bool(features.get("retest_hold_short"))),
    }

    if features.get("range_tight"):
        scorecard["range_compressed"] = 2
    elif features.get("range_moderate"):
        scorecard["range_compressed"] = 1

    if features.get("atr_compressed"):
        scorecard["atr_compressed"] = 1

    long_confirms: int = features.get("long_confirmations", 0)
    short_confirms: int = features.get("short_confirmations", 0)

    # -------------------------------------------------------------------
    # Position management signals (highest priority)
    # -------------------------------------------------------------------
    if position_context:
        side = position_context.get("position_side")
        entry = position_context.get("entry_price", 0)
        stop = position_context.get("stop_level")
        r_unit = position_context.get("r_unit", features.get("atr20") or 1)
        close = features.get("close", 0)

        if side == "long":
            # Stop hit
            if stop and close < stop:
                return _result("exit_signal", 0.95, scorecard, "stop_hit_long")
            # Failed breakout
            if features.get("failed_breakout_long"):
                return _result("exit_signal", 0.90, scorecard, "failed_breakout_long")
            # Trail: > 2R in profit
            if r_unit and (close - entry) > r_unit * 2.0:
                return _result("trail_stop", 0.80, scorecard, "trail_long_2r")
            # Add: still breaking out, confirms remain high
            if (
                scorecard["breakout_long"]
                and long_confirms >= MIN_CONFIRMS_LONG + 1
                and not features.get("gap_down_open")
            ):
                return _result("add_unit", 0.70, scorecard, "add_long_continuation")

        elif side == "short":
            # Stop hit
            if stop and close > stop:
                return _result("exit_signal", 0.95, scorecard, "stop_hit_short")
            # Failed breakout
            if features.get("failed_breakout_short"):
                return _result("exit_signal", 0.90, scorecard, "failed_breakout_short")
            # Trail: > 2R in profit
            if r_unit and (entry - close) > r_unit * 2.0:
                return _result("trail_stop", 0.80, scorecard, "trail_short_2r")
            # Add
            if (
                scorecard["breakout_short"]
                and short_confirms >= MIN_CONFIRMS_SHORT + 1
                and not features.get("gap_up_open")
            ):
                return _result("add_unit", 0.70, scorecard, "add_short_continuation")

    # -------------------------------------------------------------------
    # Hard gates for entry triggers
    # Regime: only long when SPY above 200dma AND slope positive (None → pass)
    # Volume: volume_expansion must be True (explicit True, not just truthy)
    # Credit: credit_supportive must not be False when data available (None → pass)
    # -------------------------------------------------------------------
    regime_ok_long = (
        features.get("above_200dma") is not False
        and features.get("ma200_slope_up") is not False
    )
    credit_ok_long = features.get("credit_supportive") is not False
    volume_ok = features.get("volume_expansion") is True

    # -------------------------------------------------------------------
    # Entry trigger: retest hold (two-step — REQUIRED for entry trigger)
    # Hard gates: regime filter, volume expansion, credit confirmation
    # -------------------------------------------------------------------
    if (
        features.get("retest_hold_long")
        and long_confirms >= MIN_CONFIRMS_LONG
        and not features.get("gap_down_open")
        and regime_ok_long
        and volume_ok
        and credit_ok_long
    ):
        conf = 0.65 + min(long_confirms * 0.05, 0.25) + (0.05 if scorecard["volume_expansion"] else 0)
        return _result("entry_trigger_long", min(conf, 0.95), scorecard, "two_step_long")

    if (
        features.get("retest_hold_short")
        and short_confirms >= MIN_CONFIRMS_SHORT
        and not features.get("gap_up_open")
        and volume_ok
    ):
        conf = 0.65 + min(short_confirms * 0.05, 0.25) + (0.05 if scorecard["volume_expansion"] else 0)
        return _result("entry_trigger_short", min(conf, 0.95), scorecard, "two_step_short")

    # -------------------------------------------------------------------
    # Single-step breakout: downgrade to breakout_candidate
    # Retest hold required for entry; single-step = watching for retest
    # -------------------------------------------------------------------
    compressed = scorecard["range_compressed"] >= 1 or scorecard["atr_compressed"] >= 1

    if (
        compressed
        and scorecard["breakout_long"]
        and long_confirms >= MIN_CONFIRMS_LONG
        and not features.get("gap_up_open")
    ):
        conf = 0.45 + min(long_confirms * 0.04, 0.20)
        return _result(
            "breakout_candidate", min(conf, 0.70),
            {**scorecard, "dominant_side": "long"}, "single_step_long",
        )

    if (
        compressed
        and scorecard["breakout_short"]
        and short_confirms >= MIN_CONFIRMS_SHORT
        and not features.get("gap_down_open")
    ):
        conf = 0.45 + min(short_confirms * 0.04, 0.20)
        return _result(
            "breakout_candidate", min(conf, 0.70),
            {**scorecard, "dominant_side": "short"}, "single_step_short",
        )

    # -------------------------------------------------------------------
    # Breakout candidate: watching, not yet triggering
    # -------------------------------------------------------------------
    if compressed and (long_confirms >= MIN_CONFIRMS_LONG or short_confirms >= MIN_CONFIRMS_SHORT):
        dominant = "long" if long_confirms >= short_confirms else "short"
        return _result(
            "breakout_candidate",
            0.35 + 0.05 * max(long_confirms, short_confirms),
            {**scorecard, "dominant_side": dominant},
            "watching",
        )

    # -------------------------------------------------------------------
    # Range forming: compression without macro alignment
    # -------------------------------------------------------------------
    if compressed:
        return _result("range_forming", 0.25, scorecard, "compression_only")

    # -------------------------------------------------------------------
    # No setup
    # -------------------------------------------------------------------
    return _result("no_setup", 0.10, scorecard, "none")


def _result(state: str, confidence: float, scorecard: dict, reason: str = "") -> dict:
    return {
        "signal_state": state,
        "confidence": round(confidence, 3),
        "scorecard": {**scorecard, "reason": reason},
    }
