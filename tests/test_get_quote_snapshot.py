"""Tests for get_quote_snapshot.py.

Covers all mocked scenarios required by TRA-23:
  - Normal stock quote
  - Wide spread warning
  - Stale quote (stale-data flag + rejection)
  - Halted symbol (hard rejection)
  - Closed session (warning, no rejection)
  - Options chain snapshot with Greeks
  - Liquidity / order-size-as-%%ADV checks
"""

import asyncio
import json
import sys
import types
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers: fake ib_async module injection
# ---------------------------------------------------------------------------

def _inject_fake_ib_async(mock_ib_class=None):
    fake = types.ModuleType("ib_async")
    fake.IB = mock_ib_class or MagicMock

    class FakeContract:
        def __init__(self, *a, **kw):
            self.symbol = kw.get("symbol", "")
            self.secType = kw.get("secType", "STK")
            self.currency = kw.get("currency", "USD")
            self.exchange = kw.get("exchange", "SMART")

    class Stock(FakeContract):
        def __init__(self, symbol, exchange="SMART", currency="USD"):
            self.symbol = symbol
            self.secType = "STK"
            self.currency = currency
            self.exchange = exchange

    class Option(FakeContract):
        def __init__(self, symbol, expiry, strike, right, exchange, currency="USD"):
            self.symbol = symbol
            self.secType = "OPT"
            self.lastTradeDateOrContractMonth = expiry
            self.strike = strike
            self.right = right
            self.exchange = exchange
            self.currency = currency

    class Future(FakeContract):
        def __init__(self, symbol, expiry, exchange, currency="USD"):
            self.symbol = symbol
            self.secType = "FUT"
            self.lastTradeDateOrContractMonth = expiry
            self.exchange = exchange
            self.currency = currency

    fake.Contract = FakeContract
    fake.Stock = Stock
    fake.Option = Option
    fake.Future = Future

    sys.modules["ib_async"] = fake
    if "tools.get_quote_snapshot" in sys.modules:
        del sys.modules["tools.get_quote_snapshot"]
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    import tools.get_quote_snapshot as mod
    return mod, fake


def _make_args(**kwargs):
    defaults = {
        "host": "127.0.0.1",
        "port": 7497,
        "symbol": "AAPL",
        "sec_type": "STK",
        "currency": "USD",
        "exchange": "SMART",
        "expiry": None,
        "strike": None,
        "right": None,
        "quantity": None,
        "stale_threshold": 60,
        "max_spread_pct": 0.5,
        "max_order_pct_adv": 10.0,
        "hard_reject_order_pct_adv": 25.0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_ticker(**kwargs):
    defaults = {
        "bid": None,
        "ask": None,
        "last": None,
        "bidSize": None,
        "askSize": None,
        "volume": None,
        "avVolume": None,
        "close": None,
        "time": None,
        "halted": 0,
        "modelGreeks": None,
        "lastGreeks": None,
        "shortableShares": None,
        "openInterest": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_greeks(**kwargs):
    defaults = {
        "impliedVol": None,
        "delta": None,
        "gamma": None,
        "theta": None,
        "vega": None,
        "undPrice": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_mock_ib(ticker=None):
    mock_ib_class = MagicMock()
    mock_instance = MagicMock()
    mock_instance.connectAsync = AsyncMock()
    mock_instance.qualifyContractsAsync = AsyncMock()
    mock_instance.reqMktData = MagicMock(return_value=ticker or _make_ticker())
    mock_instance.cancelMktData = MagicMock()
    mock_instance.disconnect = MagicMock()
    mock_instance.client = MagicMock()
    mock_ib_class.return_value = mock_instance
    return mock_ib_class, mock_instance


# ---------------------------------------------------------------------------
# Normal stock quote
# ---------------------------------------------------------------------------

class TestNormalQuote:
    def setup_method(self):
        now = datetime.now(timezone.utc)
        ticker = _make_ticker(
            bid=173.50, ask=173.52, last=173.51,
            bidSize=100, askSize=200,
            volume=45_000_000, avVolume=78_000_000,
            close=172.00, time=now, halted=0,
        )
        mock_ib_class, _ = _make_mock_ib(ticker)
        self.mod, _ = _inject_fake_ib_async(mock_ib_class)

    def test_bid_ask_last_present(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        assert result["quote"]["bid"] == pytest.approx(173.50)
        assert result["quote"]["ask"] == pytest.approx(173.52)
        assert result["quote"]["last"] == pytest.approx(173.51)

    def test_spread_calculated(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        assert result["quote"]["spread"] == pytest.approx(0.02, abs=1e-5)
        assert result["quote"]["spread_pct"] is not None

    def test_not_halted(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        assert result["session"]["is_halted"] is False

    def test_not_stale(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        assert result["staleness"]["is_stale"] is False

    def test_not_rejected(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        assert result["rejected"] is False

    def test_no_spread_or_halt_warning_on_normal_quote(self):
        args = _make_args()
        with patch.object(self.mod, "_detect_session_state", return_value="regular"):
            result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, args))
        codes = [w["code"] for w in result["warnings"]]
        assert "HALTED" not in codes
        assert "STALE_QUOTE" not in codes
        assert "WIDE_SPREAD" not in codes

    def test_options_null_for_stock(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        assert result["options"] is None

    def test_result_json_serialisable(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        json.dumps(result)

    def test_required_top_level_fields_present(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        for field in ("timestamp", "symbol", "sec_type", "currency", "exchange",
                      "quote", "session", "staleness", "liquidity", "options",
                      "warnings", "rejected", "rejection_reason"):
            assert field in result


# ---------------------------------------------------------------------------
# Wide spread warning
# ---------------------------------------------------------------------------

class TestWideSpread:
    def setup_method(self):
        now = datetime.now(timezone.utc)
        ticker = _make_ticker(bid=100.00, ask=101.50, last=100.75, time=now, halted=0)
        mock_ib_class, _ = _make_mock_ib(ticker)
        self.mod, _ = _inject_fake_ib_async(mock_ib_class)

    def test_wide_spread_warning_emitted(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006,
                                                         _make_args(max_spread_pct=0.5)))
        assert any(w["code"] == "WIDE_SPREAD" for w in result["warnings"])

    def test_no_warning_within_threshold(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006,
                                                         _make_args(max_spread_pct=5.0)))
        assert not any(w["code"] == "WIDE_SPREAD" for w in result["warnings"])

    def test_wide_spread_does_not_hard_reject(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006,
                                                         _make_args(max_spread_pct=0.5)))
        assert result["rejected"] is False

    def test_spread_pct_value_in_quote(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        # (101.50 - 100.00) / 100.75 * 100 ≈ 1.49%
        assert result["quote"]["spread_pct"] == pytest.approx(1.4888, abs=0.01)


# ---------------------------------------------------------------------------
# Stale quote
# ---------------------------------------------------------------------------

class TestStaleQuote:
    def setup_method(self):
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        ticker = _make_ticker(bid=50.00, ask=50.05, last=50.02, time=stale_time, halted=0)
        mock_ib_class, _ = _make_mock_ib(ticker)
        self.mod, _ = _inject_fake_ib_async(mock_ib_class)

    def test_stale_flag_set(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006,
                                                         _make_args(stale_threshold=60)))
        assert result["staleness"]["is_stale"] is True

    def test_stale_age_reported(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006,
                                                         _make_args(stale_threshold=60)))
        assert result["staleness"]["age_seconds"] is not None
        assert result["staleness"]["age_seconds"] > 60

    def test_stale_warning_emitted(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006,
                                                         _make_args(stale_threshold=60)))
        assert any(w["code"] == "STALE_QUOTE" for w in result["warnings"])

    def test_stale_causes_rejection(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006,
                                                         _make_args(stale_threshold=60)))
        assert result["rejected"] is True
        assert result["rejection_reason"] == "STALE_QUOTE"

    def test_fresh_quote_not_stale(self):
        fresh_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        ticker = _make_ticker(bid=50.00, ask=50.05, last=50.02, time=fresh_time, halted=0)
        mock_ib_class, _ = _make_mock_ib(ticker)
        mod, _ = _inject_fake_ib_async(mock_ib_class)
        result = asyncio.run(mod.get_quote_snapshot("127.0.0.1", 7497, 1006,
                                                    _make_args(stale_threshold=60)))
        assert result["staleness"]["is_stale"] is False
        assert result["rejected"] is False

    def test_threshold_stored_in_output(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006,
                                                         _make_args(stale_threshold=30)))
        assert result["staleness"]["stale_threshold_seconds"] == 30


# ---------------------------------------------------------------------------
# Halted symbol
# ---------------------------------------------------------------------------

class TestHaltedSymbol:
    def setup_method(self):
        now = datetime.now(timezone.utc)
        ticker = _make_ticker(bid=20.00, ask=20.10, last=20.05, time=now, halted=1)
        mock_ib_class, _ = _make_mock_ib(ticker)
        self.mod, _ = _inject_fake_ib_async(mock_ib_class)

    def test_halt_flag_detected(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        assert result["session"]["is_halted"] is True
        assert result["session"]["halt_flag"] == 1

    def test_halted_warning_emitted(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        assert any(w["code"] == "HALTED" for w in result["warnings"])

    def test_halted_causes_rejection(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        assert result["rejected"] is True
        assert result["rejection_reason"] == "HALTED"

    def test_halt_end_pending_also_rejected(self):
        now = datetime.now(timezone.utc)
        ticker = _make_ticker(bid=20.00, ask=20.10, last=20.05, time=now, halted=2)
        mock_ib_class, _ = _make_mock_ib(ticker)
        mod, _ = _inject_fake_ib_async(mock_ib_class)
        result = asyncio.run(mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        assert result["rejected"] is True
        assert result["session"]["is_halted"] is True

    def test_not_halted_when_flag_zero(self):
        now = datetime.now(timezone.utc)
        ticker = _make_ticker(bid=20.00, ask=20.10, last=20.05, time=now, halted=0)
        mock_ib_class, _ = _make_mock_ib(ticker)
        mod, _ = _inject_fake_ib_async(mock_ib_class)
        result = asyncio.run(mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        assert result["session"]["is_halted"] is False


# ---------------------------------------------------------------------------
# Closed session
# ---------------------------------------------------------------------------

class TestClosedSession:
    def setup_method(self):
        now = datetime.now(timezone.utc)
        ticker = _make_ticker(bid=100.0, ask=100.10, last=100.05, time=now, halted=0)
        mock_ib_class, _ = _make_mock_ib(ticker)
        self.mod, _ = _inject_fake_ib_async(mock_ib_class)

    def test_closed_session_warning_emitted(self):
        with patch.object(self.mod, "_detect_session_state", return_value="closed"):
            result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        assert any(w["code"] == "CLOSED_SESSION" for w in result["warnings"])

    def test_closed_session_state_in_output(self):
        with patch.object(self.mod, "_detect_session_state", return_value="closed"):
            result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        assert result["session"]["state"] == "closed"

    def test_closed_session_does_not_reject(self):
        with patch.object(self.mod, "_detect_session_state", return_value="closed"):
            result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        assert result["rejected"] is False

    def test_premarket_emits_extended_hours_warning(self):
        with patch.object(self.mod, "_detect_session_state", return_value="premarket"):
            result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        assert any(w["code"] == "EXTENDED_HOURS" for w in result["warnings"])

    def test_regular_session_no_session_warning(self):
        with patch.object(self.mod, "_detect_session_state", return_value="regular"):
            result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))
        codes = [w["code"] for w in result["warnings"]]
        assert "CLOSED_SESSION" not in codes
        assert "EXTENDED_HOURS" not in codes


# ---------------------------------------------------------------------------
# Options chain snapshot
# ---------------------------------------------------------------------------

class TestOptionsSnapshot:
    def setup_method(self):
        now = datetime.now(timezone.utc)
        greeks = _make_greeks(
            impliedVol=0.32, delta=0.55, gamma=0.04,
            theta=-0.12, vega=0.18, undPrice=175.50,
        )
        ticker = _make_ticker(
            bid=2.50, ask=2.60, last=2.55,
            volume=3500, close=2.40,
            time=now, halted=0,
            modelGreeks=greeks, openInterest=15000,
        )
        mock_ib_class, _ = _make_mock_ib(ticker)
        self.mod, _ = _inject_fake_ib_async(mock_ib_class)
        self.opt_args = _make_args(sec_type="OPT", expiry="20260619", strike=175.0, right="C")

    def test_options_block_present(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, self.opt_args))
        assert result["options"] is not None

    def test_greeks_populated(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, self.opt_args))
        opts = result["options"]
        assert opts["delta"] == pytest.approx(0.55)
        assert opts["gamma"] == pytest.approx(0.04)
        assert opts["theta"] == pytest.approx(-0.12)
        assert opts["vega"] == pytest.approx(0.18)
        assert opts["iv"] == pytest.approx(0.32)

    def test_underlying_price_present(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, self.opt_args))
        assert result["options"]["underlying_price"] == pytest.approx(175.50)

    def test_open_interest_present(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, self.opt_args))
        assert result["options"]["open_interest"] == pytest.approx(15000)

    def test_option_volume_in_options_block(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, self.opt_args))
        assert result["options"]["option_volume"] == pytest.approx(3500)

    def test_missing_greeks_returned_as_null(self):
        now = datetime.now(timezone.utc)
        ticker = _make_ticker(bid=1.0, ask=1.10, last=1.05, time=now,
                              modelGreeks=None, lastGreeks=None)
        mock_ib_class, _ = _make_mock_ib(ticker)
        mod, _ = _inject_fake_ib_async(mock_ib_class)
        result = asyncio.run(mod.get_quote_snapshot("127.0.0.1", 7497, 1006, self.opt_args))
        opts = result["options"]
        assert opts["delta"] is None
        assert opts["iv"] is None
        assert opts["underlying_price"] is None

    def test_lastgreeks_used_as_fallback(self):
        now = datetime.now(timezone.utc)
        greeks = _make_greeks(delta=0.40, impliedVol=0.25, undPrice=150.0)
        ticker = _make_ticker(bid=1.0, ask=1.10, last=1.05, time=now,
                              modelGreeks=None, lastGreeks=greeks)
        mock_ib_class, _ = _make_mock_ib(ticker)
        mod, _ = _inject_fake_ib_async(mock_ib_class)
        result = asyncio.run(mod.get_quote_snapshot("127.0.0.1", 7497, 1006, self.opt_args))
        assert result["options"]["delta"] == pytest.approx(0.40)

    def test_options_block_json_serialisable(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, self.opt_args))
        json.dumps(result["options"])


# ---------------------------------------------------------------------------
# Liquidity checks
# ---------------------------------------------------------------------------

class TestLiquidityChecks:
    def setup_method(self):
        now = datetime.now(timezone.utc)
        ticker = _make_ticker(
            bid=50.0, ask=50.05, last=50.02,
            volume=5_000_000, avVolume=10_000_000,
            time=now, halted=0,
        )
        mock_ib_class, _ = _make_mock_ib(ticker)
        self.mod, _ = _inject_fake_ib_async(mock_ib_class)

    def test_order_pct_adv_calculated(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006,
                                                         _make_args(quantity=1_000_000)))
        assert result["liquidity"]["order_pct_adv"] == pytest.approx(10.0)

    def test_low_liquidity_warning_above_warn_threshold(self):
        # 1.5M / 10M = 15% > 10% warn threshold < 25% hard reject
        args = _make_args(quantity=1_500_000, max_order_pct_adv=10.0,
                          hard_reject_order_pct_adv=25.0)
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, args))
        assert any(w["code"] == "LOW_LIQUIDITY" for w in result["warnings"])
        assert result["rejected"] is False

    def test_hard_reject_when_exceeds_adv_hard_limit(self):
        # 3M / 10M = 30% > 25% hard reject
        args = _make_args(quantity=3_000_000, max_order_pct_adv=10.0,
                          hard_reject_order_pct_adv=25.0)
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, args))
        assert result["rejected"] is True
        assert result["rejection_reason"] == "INSUFFICIENT_LIQUIDITY"

    def test_no_quantity_no_pct_adv(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006,
                                                         _make_args(quantity=None)))
        assert result["liquidity"]["order_pct_adv"] is None
        assert result["liquidity"]["order_size"] is None

    def test_quantity_stored_in_liquidity(self):
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006,
                                                         _make_args(quantity=500)))
        assert result["liquidity"]["order_size"] == pytest.approx(500)

    def test_no_liquidity_warning_within_threshold(self):
        # 500 / 10M = tiny, well under thresholds
        result = asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006,
                                                         _make_args(quantity=500)))
        codes = [w["code"] for w in result["warnings"]]
        assert "LOW_LIQUIDITY" not in codes
        assert "INSUFFICIENT_LIQUIDITY" not in codes


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

class TestConnectionError:
    def setup_method(self):
        mock_ib_class, self.mock_instance = _make_mock_ib()
        self.mod, _ = _inject_fake_ib_async(mock_ib_class)

    def test_raises_connection_error_on_timeout(self):
        self.mock_instance.connectAsync = AsyncMock(side_effect=asyncio.TimeoutError())
        with pytest.raises(ConnectionError, match="Timed out"):
            asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))

    def test_raises_connection_error_on_exception(self):
        self.mock_instance.connectAsync = AsyncMock(side_effect=OSError("refused"))
        with pytest.raises(ConnectionError, match="Cannot reach TWS"):
            asyncio.run(self.mod.get_quote_snapshot("127.0.0.1", 7497, 1006, _make_args()))


# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------

class TestArgParsing:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_defaults(self):
        with patch("sys.argv", ["get_quote_snapshot.py", "--symbol", "AAPL"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_PAPER
        assert args.stale_threshold == self.mod.DEFAULT_STALE_THRESHOLD
        assert args.max_spread_pct == self.mod.DEFAULT_MAX_SPREAD_PCT
        assert args.quantity is None

    def test_missing_symbol_exits(self):
        with patch("sys.argv", ["get_quote_snapshot.py"]):
            with pytest.raises(SystemExit):
                self.mod.parse_args()

    def test_live_flag(self):
        with patch("sys.argv", ["get_quote_snapshot.py", "--symbol", "AAPL", "--live"]):
            args = self.mod.parse_args()
        assert args.port == self.mod.PORT_LIVE

    def test_opt_args_parsed(self):
        with patch("sys.argv", ["get_quote_snapshot.py",
                                "--symbol", "AAPL",
                                "--sec-type", "OPT",
                                "--expiry", "20260619",
                                "--strike", "180",
                                "--right", "C",
                                "--quantity", "10"]):
            args = self.mod.parse_args()
        assert args.sec_type == "OPT"
        assert args.strike == pytest.approx(180.0)
        assert args.right == "C"
        assert args.quantity == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Connection ID register
# ---------------------------------------------------------------------------

class TestConnectionIdRegister:
    def test_quote_snapshot_registered(self):
        import pathlib
        reg = pathlib.Path(__file__).parent.parent / "tools" / "connection_ids.json"
        data = json.loads(reg.read_text())
        assert "1009" in data["ids"]
        assert data["ids"]["1009"]["tool"] == "get_quote_snapshot.py"
        assert data["ids"]["1009"]["read_only"] is True


# ---------------------------------------------------------------------------
# Safe float helper
# ---------------------------------------------------------------------------

class TestSafeFloat:
    def setup_method(self):
        self.mod, _ = _inject_fake_ib_async()

    def test_none_returns_none(self):
        assert self.mod._safe_float(None) is None

    def test_nan_returns_none(self):
        import math
        assert self.mod._safe_float(float("nan")) is None

    def test_inf_returns_none(self):
        assert self.mod._safe_float(float("inf")) is None

    def test_ib_unset_returns_none(self):
        assert self.mod._safe_float(1.7976931348623157e308) is None

    def test_valid_float_passes_through(self):
        assert self.mod._safe_float(173.5) == pytest.approx(173.5)

    def test_zero_passes_through(self):
        assert self.mod._safe_float(0.0) == pytest.approx(0.0)
