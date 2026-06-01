"""Tests for tools/trade_proposal.py — strict trade proposal schema and validation."""

import pytest

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from tools.trade_proposal import (
    AssetClass,
    OptionRight,
    OrderType,
    PositionEffect,
    ProposalError,
    Side,
    TIF,
    TradeProposal,
    TradeType,
    normalize_order_payload,
    validate_proposal,
)


# ---------------------------------------------------------------------------
# Helpers — minimal valid proposals per asset class
# ---------------------------------------------------------------------------

def _equity_proposal(**overrides) -> TradeProposal:
    defaults = dict(
        instrument_id="STK:AAPL",
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
        side=Side.BUY,
        quantity=10.0,
        order_type=OrderType.MKT,
        tif=TIF.DAY,
        strategy="momentum",
        rationale="Strong breakout above 200-day MA",
        trade_type=TradeType.DAY,
        risk_point=165.0,
        max_loss=500.0,
        venue="SMART",
    )
    defaults.update(overrides)
    return TradeProposal(**defaults)


def _future_proposal(**overrides) -> TradeProposal:
    defaults = dict(
        instrument_id="FUT:ES:20250321",
        symbol="ES",
        asset_class=AssetClass.FUTURE,
        side=Side.BUY,
        quantity=1.0,
        order_type=OrderType.MKT,
        tif=TIF.DAY,
        strategy="trend_follow",
        rationale="Continuation above prior day high",
        trade_type=TradeType.DAY,
        risk_point=5200.0,
        max_loss=1000.0,
        venue="CME",
        expiry="20250321",
    )
    defaults.update(overrides)
    return TradeProposal(**defaults)


def _fx_proposal(**overrides) -> TradeProposal:
    defaults = dict(
        instrument_id="FX:EURUSD",
        symbol="EURUSD",
        asset_class=AssetClass.FX,
        side=Side.BUY,
        quantity=100_000.0,
        order_type=OrderType.LMT,
        tif=TIF.GTC,
        strategy="mean_reversion",
        rationale="Oversold at key support",
        trade_type=TradeType.SWING,
        risk_point=1.0800,
        max_loss=500.0,
        venue="IDEALPRO",
        limit_price=1.0850,
        currency_pair="EURUSD",
    )
    defaults.update(overrides)
    return TradeProposal(**defaults)


def _option_proposal(**overrides) -> TradeProposal:
    defaults = dict(
        instrument_id="OPT:AAPL:20250117:170:C",
        symbol="AAPL",
        asset_class=AssetClass.OPTION,
        side=Side.BUY,
        quantity=5.0,
        order_type=OrderType.LMT,
        tif=TIF.DAY,
        strategy="directional_call",
        rationale="Earnings catalyst, defined risk",
        trade_type=TradeType.SCALP,
        risk_point=0.0,
        max_loss=750.0,
        venue="SMART",
        limit_price=3.50,
        expiry="20250117",
        strike=170.0,
        right=OptionRight.CALL,
        multiplier=100.0,
    )
    defaults.update(overrides)
    return TradeProposal(**defaults)


# ---------------------------------------------------------------------------
# Valid proposal tests — one per asset class / order type
# ---------------------------------------------------------------------------

class TestValidProposals:

    def test_equity_market_order(self):
        p = _equity_proposal(order_type=OrderType.MKT)
        validate_proposal(p)

    def test_equity_limit_order(self):
        p = _equity_proposal(order_type=OrderType.LMT, limit_price=170.0)
        validate_proposal(p)

    def test_equity_stop_order(self):
        p = _equity_proposal(order_type=OrderType.STP, stop_price=165.0)
        validate_proposal(p)

    def test_equity_stop_limit_order(self):
        p = _equity_proposal(
            order_type=OrderType.STP_LMT,
            stop_price=165.0,
            limit_price=164.50,
        )
        validate_proposal(p)

    def test_equity_opening_position_effect(self):
        p = _equity_proposal(position_effect=PositionEffect.OPEN)
        validate_proposal(p)

    def test_equity_closing_reduce_only(self):
        p = _equity_proposal(
            side=Side.SELL,
            position_effect=PositionEffect.CLOSE,
            reduce_only=True,
        )
        validate_proposal(p)

    def test_future_market_order(self):
        p = _future_proposal(order_type=OrderType.MKT)
        validate_proposal(p)

    def test_future_limit_order(self):
        p = _future_proposal(order_type=OrderType.LMT, limit_price=5250.0)
        validate_proposal(p)

    def test_future_closing(self):
        p = _future_proposal(
            side=Side.SELL,
            position_effect=PositionEffect.CLOSE,
            reduce_only=True,
        )
        validate_proposal(p)

    def test_fx_limit_order(self):
        p = _fx_proposal()
        validate_proposal(p)

    def test_option_call_limit_buy(self):
        p = _option_proposal()
        validate_proposal(p)

    def test_option_put_limit_buy(self):
        p = _option_proposal(right=OptionRight.PUT, strike=160.0)
        validate_proposal(p)

    def test_option_closing_sell(self):
        p = _option_proposal(
            side=Side.SELL,
            position_effect=PositionEffect.CLOSE,
            reduce_only=True,
        )
        validate_proposal(p)

    def test_optional_target_and_trailing_stop(self):
        p = _equity_proposal(target_price=185.0, trailing_stop=2.0)
        validate_proposal(p)

    def test_swing_trade_gtc_tif(self):
        p = _equity_proposal(
            trade_type=TradeType.SWING,
            tif=TIF.GTC,
            order_type=OrderType.LMT,
            limit_price=169.0,
        )
        validate_proposal(p)


# ---------------------------------------------------------------------------
# Invalid proposal tests — reject with reason codes
# ---------------------------------------------------------------------------

class TestMissingCoreFields:

    def _assert_code(self, proposal: TradeProposal, *codes: str) -> None:
        with pytest.raises(ProposalError) as exc_info:
            validate_proposal(proposal)
        for code in codes:
            assert code in exc_info.value.reason_codes, (
                f"Expected reason code '{code}', got {exc_info.value.reason_codes}"
            )

    def test_missing_instrument_id(self):
        self._assert_code(_equity_proposal(instrument_id=""), "MISSING_INSTRUMENT_ID")

    def test_missing_symbol(self):
        self._assert_code(_equity_proposal(symbol=""), "MISSING_SYMBOL")

    def test_zero_quantity(self):
        self._assert_code(_equity_proposal(quantity=0.0), "INVALID_QUANTITY")

    def test_negative_quantity(self):
        self._assert_code(_equity_proposal(quantity=-5.0), "INVALID_QUANTITY")

    def test_missing_strategy(self):
        self._assert_code(_equity_proposal(strategy=""), "MISSING_STRATEGY")

    def test_missing_rationale(self):
        self._assert_code(_equity_proposal(rationale=""), "MISSING_RATIONALE")

    def test_zero_max_loss(self):
        self._assert_code(_equity_proposal(max_loss=0.0), "INVALID_MAX_LOSS")

    def test_negative_max_loss(self):
        self._assert_code(_equity_proposal(max_loss=-100.0), "INVALID_MAX_LOSS")

    def test_multiple_core_errors_reported_together(self):
        p = _equity_proposal(instrument_id="", symbol="", quantity=0.0, max_loss=0.0)
        with pytest.raises(ProposalError) as exc_info:
            validate_proposal(p)
        codes = exc_info.value.reason_codes
        assert "MISSING_INSTRUMENT_ID" in codes
        assert "MISSING_SYMBOL" in codes
        assert "INVALID_QUANTITY" in codes
        assert "INVALID_MAX_LOSS" in codes


class TestOrderTypePriceRequirements:

    def _assert_code(self, proposal: TradeProposal, *codes: str) -> None:
        with pytest.raises(ProposalError) as exc_info:
            validate_proposal(proposal)
        for code in codes:
            assert code in exc_info.value.reason_codes

    def test_lmt_without_limit_price(self):
        p = _equity_proposal(order_type=OrderType.LMT, limit_price=None)
        self._assert_code(p, "MISSING_LIMIT_PRICE_FOR_LMT")

    def test_stp_without_stop_price(self):
        p = _equity_proposal(order_type=OrderType.STP, stop_price=None)
        self._assert_code(p, "MISSING_STOP_PRICE_FOR_STP")

    def test_stp_lmt_without_limit_price(self):
        p = _equity_proposal(
            order_type=OrderType.STP_LMT,
            stop_price=165.0,
            limit_price=None,
        )
        self._assert_code(p, "MISSING_LIMIT_PRICE_FOR_STP_LMT")

    def test_stp_lmt_without_stop_price(self):
        p = _equity_proposal(
            order_type=OrderType.STP_LMT,
            stop_price=None,
            limit_price=164.50,
        )
        self._assert_code(p, "MISSING_STOP_PRICE_FOR_STP_LMT")

    def test_stp_lmt_missing_both_prices(self):
        p = _equity_proposal(order_type=OrderType.STP_LMT, stop_price=None, limit_price=None)
        self._assert_code(p, "MISSING_LIMIT_PRICE_FOR_STP_LMT", "MISSING_STOP_PRICE_FOR_STP_LMT")

    def test_mkt_needs_no_prices(self):
        # MKT with no limit/stop price is valid
        p = _equity_proposal(order_type=OrderType.MKT)
        validate_proposal(p)


class TestRiskFieldRequirements:

    def test_risk_point_is_required(self):
        p = _equity_proposal()
        p.risk_point = None  # bypass dataclass type hint for test
        with pytest.raises(ProposalError) as exc_info:
            validate_proposal(p)
        assert "MISSING_RISK_POINT" in exc_info.value.reason_codes

    def test_max_loss_is_required(self):
        p = _equity_proposal(max_loss=None)
        with pytest.raises(ProposalError) as exc_info:
            validate_proposal(p)
        assert "MISSING_RISK_BUDGET" in exc_info.value.reason_codes

    def test_max_risk_budget_can_satisfy_risk_budget_requirement(self):
        p = _equity_proposal(max_loss=None, max_risk_budget=500.0)
        validate_proposal(p)

    def test_invalid_max_risk_budget_is_rejected(self):
        p = _equity_proposal(max_loss=None, max_risk_budget=0.0)
        with pytest.raises(ProposalError) as exc_info:
            validate_proposal(p)
        assert "INVALID_MAX_RISK_BUDGET" in exc_info.value.reason_codes

    def test_risk_point_not_silently_inferred(self):
        """Missing risk_point must always be a hard error, never inferred."""
        p = _equity_proposal()
        p.risk_point = None
        with pytest.raises(ProposalError) as exc_info:
            validate_proposal(p)
        assert "MISSING_RISK_POINT" in exc_info.value.reason_codes


class TestAssetClassValidation:

    def _assert_code(self, proposal: TradeProposal, *codes: str) -> None:
        with pytest.raises(ProposalError) as exc_info:
            validate_proposal(proposal)
        for code in codes:
            assert code in exc_info.value.reason_codes

    # --- OPTION ---

    def test_option_missing_expiry(self):
        self._assert_code(_option_proposal(expiry=None), "MISSING_OPTION_EXPIRY")

    def test_option_missing_strike(self):
        self._assert_code(_option_proposal(strike=None), "MISSING_OPTION_STRIKE")

    def test_option_missing_right(self):
        self._assert_code(_option_proposal(right=None), "MISSING_OPTION_RIGHT")

    def test_option_missing_multiplier(self):
        self._assert_code(_option_proposal(multiplier=None), "MISSING_OPTION_MULTIPLIER")

    def test_option_all_fields_missing_at_once(self):
        p = _option_proposal(expiry=None, strike=None, right=None, multiplier=None)
        with pytest.raises(ProposalError) as exc_info:
            validate_proposal(p)
        codes = exc_info.value.reason_codes
        assert "MISSING_OPTION_EXPIRY" in codes
        assert "MISSING_OPTION_STRIKE" in codes
        assert "MISSING_OPTION_RIGHT" in codes
        assert "MISSING_OPTION_MULTIPLIER" in codes

    # --- FUTURE ---

    def test_future_missing_expiry(self):
        self._assert_code(_future_proposal(expiry=None), "MISSING_FUTURE_EXPIRY")

    def test_future_missing_venue(self):
        self._assert_code(_future_proposal(venue=None), "MISSING_FUTURE_VENUE")

    # --- FX ---

    def test_fx_missing_currency_pair(self):
        self._assert_code(_fx_proposal(currency_pair=None), "MISSING_FX_CURRENCY_PAIR")

    def test_fx_missing_venue(self):
        self._assert_code(_fx_proposal(venue=None), "MISSING_FX_VENUE")


class TestPositionEffectValidation:

    def test_reduce_only_requires_close_effect(self):
        p = _equity_proposal(reduce_only=True, position_effect=PositionEffect.OPEN)
        with pytest.raises(ProposalError) as exc_info:
            validate_proposal(p)
        assert "REDUCE_ONLY_REQUIRES_CLOSE" in exc_info.value.reason_codes

    def test_reduce_only_without_position_effect_fails(self):
        p = _equity_proposal(reduce_only=True, position_effect=None)
        with pytest.raises(ProposalError) as exc_info:
            validate_proposal(p)
        assert "REDUCE_ONLY_REQUIRES_CLOSE" in exc_info.value.reason_codes

    def test_reduce_only_with_close_passes(self):
        p = _equity_proposal(
            side=Side.SELL,
            reduce_only=True,
            position_effect=PositionEffect.CLOSE,
        )
        validate_proposal(p)


# ---------------------------------------------------------------------------
# Normalization tests
# ---------------------------------------------------------------------------

class TestNormalizeOrderPayload:

    def test_equity_mkt_normalized_keys(self):
        p = _equity_proposal()
        payload = normalize_order_payload(p)
        for key in ("instrument_id", "symbol", "asset_class", "side", "quantity",
                    "order_type", "tif", "strategy", "rationale", "trade_type",
                    "risk_point", "max_loss"):
            assert key in payload, f"Missing key: {key}"

    def test_equity_mkt_normalized_values(self):
        p = _equity_proposal()
        payload = normalize_order_payload(p)
        assert payload["asset_class"] == "EQUITY"
        assert payload["side"] == "BUY"
        assert payload["order_type"] == "MKT"
        assert payload["tif"] == "DAY"
        assert payload["trade_type"] == "DAY"

    def test_limit_price_included_for_lmt(self):
        p = _equity_proposal(order_type=OrderType.LMT, limit_price=172.50)
        payload = normalize_order_payload(p)
        assert payload["limit_price"] == pytest.approx(172.50)

    def test_stop_price_included_for_stp(self):
        p = _equity_proposal(order_type=OrderType.STP, stop_price=164.0)
        payload = normalize_order_payload(p)
        assert payload["stop_price"] == pytest.approx(164.0)

    def test_stop_lmt_both_prices(self):
        p = _equity_proposal(
            order_type=OrderType.STP_LMT,
            stop_price=164.0,
            limit_price=163.50,
        )
        payload = normalize_order_payload(p)
        assert payload["stop_price"] == pytest.approx(164.0)
        assert payload["limit_price"] == pytest.approx(163.50)

    def test_option_fields_normalized(self):
        p = _option_proposal()
        payload = normalize_order_payload(p)
        assert payload["expiry"] == "20250117"
        assert payload["strike"] == pytest.approx(170.0)
        assert payload["right"] == "C"
        assert payload["multiplier"] == pytest.approx(100.0)

    def test_option_put_right_normalized(self):
        p = _option_proposal(right=OptionRight.PUT)
        payload = normalize_order_payload(p)
        assert payload["right"] == "P"

    def test_future_fields_normalized(self):
        p = _future_proposal()
        payload = normalize_order_payload(p)
        assert payload["expiry"] == "20250321"
        assert payload["venue"] == "CME"
        assert payload["asset_class"] == "FUTURE"

    def test_fx_fields_normalized(self):
        p = _fx_proposal()
        payload = normalize_order_payload(p)
        assert payload["currency_pair"] == "EURUSD"
        assert payload["venue"] == "IDEALPRO"

    def test_position_effect_serialized(self):
        p = _equity_proposal(
            side=Side.SELL,
            position_effect=PositionEffect.CLOSE,
            reduce_only=True,
        )
        payload = normalize_order_payload(p)
        assert payload["position_effect"] == "CLOSE"
        assert payload["reduce_only"] is True

    def test_no_position_effect_is_none(self):
        p = _equity_proposal(position_effect=None)
        payload = normalize_order_payload(p)
        assert payload["position_effect"] is None

    def test_target_and_trailing_stop_included(self):
        p = _equity_proposal(target_price=190.0, trailing_stop=2.5)
        payload = normalize_order_payload(p)
        assert payload["target_price"] == pytest.approx(190.0)
        assert payload["trailing_stop"] == pytest.approx(2.5)

    def test_max_risk_budget_included(self):
        p = _equity_proposal(max_loss=None, max_risk_budget=500.0)
        payload = normalize_order_payload(p)
        assert payload["max_loss"] is None
        assert payload["max_risk_budget"] == pytest.approx(500.0)

    def test_normalize_raises_on_invalid_proposal(self):
        p = _equity_proposal(order_type=OrderType.LMT, limit_price=None)
        with pytest.raises(ProposalError) as exc_info:
            normalize_order_payload(p)
        assert "MISSING_LIMIT_PRICE_FOR_LMT" in exc_info.value.reason_codes

    def test_payload_is_json_serializable(self):
        import json
        p = _option_proposal()
        payload = normalize_order_payload(p)
        # Should not raise
        json.dumps(payload)

    def test_enum_values_are_strings_in_payload(self):
        p = _equity_proposal()
        payload = normalize_order_payload(p)
        assert isinstance(payload["asset_class"], str)
        assert isinstance(payload["side"], str)
        assert isinstance(payload["order_type"], str)
        assert isinstance(payload["tif"], str)
        assert isinstance(payload["trade_type"], str)


# ---------------------------------------------------------------------------
# ProposalError tests
# ---------------------------------------------------------------------------

class TestProposalError:

    def test_error_contains_all_codes(self):
        p = _equity_proposal(instrument_id="", symbol="", quantity=-1.0, max_loss=0.0)
        with pytest.raises(ProposalError) as exc_info:
            validate_proposal(p)
        err = exc_info.value
        assert len(err.reason_codes) >= 4
        assert isinstance(err.reason_codes, list)

    def test_error_message_includes_codes(self):
        p = _equity_proposal(order_type=OrderType.LMT, limit_price=None)
        with pytest.raises(ProposalError) as exc_info:
            validate_proposal(p)
        assert "MISSING_LIMIT_PRICE_FOR_LMT" in str(exc_info.value)

    def test_valid_proposal_raises_nothing(self):
        p = _equity_proposal()
        validate_proposal(p)  # must not raise
