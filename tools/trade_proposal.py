#!/usr/bin/env python3
"""
trade_proposal.py — Strict trade proposal schema and validation.

Turns candidate trade instructions into a validated, normalized desk proposal
object before any order can leave the desk.  Rejects ambiguous or underspecified
proposals with explicit reason codes; never silently infers missing risk points
or sizing.

Typical flow
------------
    from tools.trade_proposal import TradeProposal, normalize_order_payload, ProposalError

    try:
        payload = normalize_order_payload(proposal)   # validates + normalizes
    except ProposalError as exc:
        print(exc.reason_codes)   # list of rejection codes
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    FUTURE = "FUTURE"
    FX = "FX"
    OPTION = "OPTION"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MKT = "MKT"
    LMT = "LMT"
    STP = "STP"
    STP_LMT = "STP_LMT"


class TIF(str, Enum):
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"


class TradeType(str, Enum):
    SCALP = "SCALP"
    DAY = "DAY"
    SWING = "SWING"


class PositionEffect(str, Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"


class OptionRight(str, Enum):
    CALL = "C"
    PUT = "P"


# ---------------------------------------------------------------------------
# Proposal dataclass
# ---------------------------------------------------------------------------

@dataclass
class TradeProposal:
    """
    Desk-level trade proposal.  All required fields must be supplied explicitly;
    optional fields are typed Optional and default to None.

    Required for all asset classes
    --------------------------------
    instrument_id   Broker/venue instrument identifier
    symbol          Human-readable ticker / symbol
    asset_class     AssetClass enum value
    side            BUY or SELL
    quantity        Number of shares / contracts / lots (must be > 0)
    order_type      MKT | LMT | STP | STP_LMT
    tif             Time in force
    strategy        Tag identifying the desk strategy (e.g. "momentum", "pairs")
    rationale       Free-text context for the trade idea
    trade_type      SCALP | DAY | SWING — expected hold duration
    risk_point      Explicit invalidation / stop-loss price (never inferred)
    max_loss        Maximum dollar loss budget for this trade (must be > 0)

    Conditional
    -----------
    limit_price     Required for LMT and STP_LMT order types
    stop_price      Required for STP and STP_LMT order types
    venue           Exchange/ECN; required for FUTURE and FX
    expiry          YYYYMMDD contract expiry; required for OPTION, FUTURE
    strike          Required for OPTION
    right           OptionRight.CALL / PUT; required for OPTION
    multiplier      Contract multiplier; required for OPTION (defaults to 100)
    currency_pair   e.g. "EURUSD"; required for FX
    """

    # Core — always required
    instrument_id: str
    symbol: str
    asset_class: AssetClass
    side: Side
    quantity: float
    order_type: OrderType
    tif: TIF
    strategy: str
    rationale: str
    trade_type: TradeType
    risk_point: float
    max_loss: float

    # Conditional / optional
    venue: Optional[str] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    trailing_stop: Optional[float] = None
    position_effect: Optional[PositionEffect] = None
    reduce_only: bool = False

    # Options
    expiry: Optional[str] = None
    strike: Optional[float] = None
    right: Optional[OptionRight] = None
    multiplier: Optional[float] = None

    # FX
    currency_pair: Optional[str] = None


# ---------------------------------------------------------------------------
# Rejection error
# ---------------------------------------------------------------------------

class ProposalError(Exception):
    """Raised when a proposal fails validation.

    Attributes
    ----------
    reason_codes : list[str]
        Machine-readable rejection codes (see REJECTION_CODES below).
    """

    def __init__(self, reason_codes: List[str]) -> None:
        self.reason_codes = reason_codes
        super().__init__(f"Proposal rejected: {', '.join(reason_codes)}")


# ---------------------------------------------------------------------------
# Rejection codes (documentary)
# ---------------------------------------------------------------------------

REJECTION_CODES = {
    "MISSING_INSTRUMENT_ID": "instrument_id is empty",
    "MISSING_SYMBOL": "symbol is empty",
    "INVALID_QUANTITY": "quantity must be > 0",
    "MISSING_LIMIT_PRICE_FOR_LMT": "limit_price is required for LMT orders",
    "MISSING_STOP_PRICE_FOR_STP": "stop_price is required for STP orders",
    "MISSING_LIMIT_PRICE_FOR_STP_LMT": "limit_price is required for STP_LMT orders",
    "MISSING_STOP_PRICE_FOR_STP_LMT": "stop_price is required for STP_LMT orders",
    "MISSING_RISK_POINT": "risk_point (invalidation price) must be specified explicitly",
    "INVALID_MAX_LOSS": "max_loss must be > 0",
    "MISSING_STRATEGY": "strategy tag is required",
    "MISSING_RATIONALE": "rationale/context is required",
    "MISSING_OPTION_EXPIRY": "expiry (YYYYMMDD) is required for OPTION",
    "MISSING_OPTION_STRIKE": "strike is required for OPTION",
    "MISSING_OPTION_RIGHT": "right (C/P) is required for OPTION",
    "MISSING_OPTION_MULTIPLIER": "multiplier is required for OPTION",
    "MISSING_FUTURE_EXPIRY": "expiry (YYYYMMDD) is required for FUTURE",
    "MISSING_FUTURE_VENUE": "venue is required for FUTURE",
    "MISSING_FX_CURRENCY_PAIR": "currency_pair (e.g. EURUSD) is required for FX",
    "MISSING_FX_VENUE": "venue is required for FX",
    "REDUCE_ONLY_REQUIRES_CLOSE": "reduce_only=True requires position_effect=CLOSE",
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_proposal(proposal: TradeProposal) -> None:
    """Validate *proposal* against all desk rules.

    Raises
    ------
    ProposalError
        Contains a list of all failing reason codes.  All rules are checked
        before raising so callers receive the complete set of errors at once.
    """
    errors: List[str] = []

    # --- Core identity ---
    if not proposal.instrument_id or not proposal.instrument_id.strip():
        errors.append("MISSING_INSTRUMENT_ID")
    if not proposal.symbol or not proposal.symbol.strip():
        errors.append("MISSING_SYMBOL")

    # --- Quantity ---
    if proposal.quantity <= 0:
        errors.append("INVALID_QUANTITY")

    # --- Order-type price requirements ---
    if proposal.order_type == OrderType.LMT:
        if proposal.limit_price is None:
            errors.append("MISSING_LIMIT_PRICE_FOR_LMT")
    elif proposal.order_type == OrderType.STP:
        if proposal.stop_price is None:
            errors.append("MISSING_STOP_PRICE_FOR_STP")
    elif proposal.order_type == OrderType.STP_LMT:
        if proposal.limit_price is None:
            errors.append("MISSING_LIMIT_PRICE_FOR_STP_LMT")
        if proposal.stop_price is None:
            errors.append("MISSING_STOP_PRICE_FOR_STP_LMT")

    # --- Risk fields (never inferred) ---
    if proposal.risk_point is None:
        errors.append("MISSING_RISK_POINT")
    if proposal.max_loss is None or proposal.max_loss <= 0:
        errors.append("INVALID_MAX_LOSS")

    # --- Strategy / context ---
    if not proposal.strategy or not proposal.strategy.strip():
        errors.append("MISSING_STRATEGY")
    if not proposal.rationale or not proposal.rationale.strip():
        errors.append("MISSING_RATIONALE")

    # --- Asset-class specific ---
    if proposal.asset_class == AssetClass.OPTION:
        if not proposal.expiry:
            errors.append("MISSING_OPTION_EXPIRY")
        if proposal.strike is None:
            errors.append("MISSING_OPTION_STRIKE")
        if proposal.right is None:
            errors.append("MISSING_OPTION_RIGHT")
        if proposal.multiplier is None:
            errors.append("MISSING_OPTION_MULTIPLIER")

    elif proposal.asset_class == AssetClass.FUTURE:
        if not proposal.expiry:
            errors.append("MISSING_FUTURE_EXPIRY")
        if not proposal.venue:
            errors.append("MISSING_FUTURE_VENUE")

    elif proposal.asset_class == AssetClass.FX:
        if not proposal.currency_pair:
            errors.append("MISSING_FX_CURRENCY_PAIR")
        if not proposal.venue:
            errors.append("MISSING_FX_VENUE")

    # --- Reduce-only consistency ---
    if proposal.reduce_only and proposal.position_effect != PositionEffect.CLOSE:
        errors.append("REDUCE_ONLY_REQUIRES_CLOSE")

    if errors:
        raise ProposalError(errors)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_order_payload(proposal: TradeProposal) -> dict:
    """Validate *proposal* and return a normalized order payload dict.

    The returned dict is consumed by pre-trade risk and the execution layer.
    Raises :exc:`ProposalError` if validation fails.

    The payload always contains every field; fields that are not applicable
    to the asset class are ``None``.
    """
    validate_proposal(proposal)

    payload: dict = {
        "instrument_id": proposal.instrument_id,
        "symbol": proposal.symbol,
        "asset_class": proposal.asset_class.value,
        "venue": proposal.venue,
        "side": proposal.side.value,
        "quantity": proposal.quantity,
        "order_type": proposal.order_type.value,
        "limit_price": proposal.limit_price,
        "stop_price": proposal.stop_price,
        "tif": proposal.tif.value,
        "strategy": proposal.strategy,
        "rationale": proposal.rationale,
        "trade_type": proposal.trade_type.value,
        "risk_point": proposal.risk_point,
        "max_loss": proposal.max_loss,
        "target_price": proposal.target_price,
        "trailing_stop": proposal.trailing_stop,
        "position_effect": proposal.position_effect.value if proposal.position_effect else None,
        "reduce_only": proposal.reduce_only,
        # Options
        "expiry": proposal.expiry,
        "strike": proposal.strike,
        "right": proposal.right.value if proposal.right else None,
        "multiplier": proposal.multiplier,
        # FX
        "currency_pair": proposal.currency_pair,
    }

    return payload
