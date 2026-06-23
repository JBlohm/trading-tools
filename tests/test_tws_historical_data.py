"""Unit tests for tools/tws_historical_data.py.

Tests cover:
  - bars_to_namespace: dict → SimpleNamespace conversion
  - offline detection: asyncio.TimeoutError → status tws_offline
  - bar list empty → status no_data
  - malformed bars skipped gracefully
  - fetch_bars success path (mocked IB)
"""

import asyncio
import sys
import types
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── stub ib_async ──────────────────────────────────────────────────────────────

def _make_ib_stub():
    stub = types.ModuleType("ib_async")

    class _Base:
        def __init__(self, *a, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    class Stock(_Base):
        pass

    class IB:
        async def connectAsync(self, *a, **kw):
            pass
        async def qualifyContractsAsync(self, *a, **kw):
            pass
        async def reqHistoricalDataAsync(self, *a, **kw):
            return []
        def disconnect(self):
            pass

    stub.IB = IB
    stub.Stock = Stock
    return stub


if "ib_async" not in sys.modules:
    sys.modules["ib_async"] = _make_ib_stub()

# ── path setup ────────────────────────────────────────────────────────────────

_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from tools.tws_historical_data import bars_to_namespace, fetch_bars


# ── bars_to_namespace ──────────────────────────────────────────────────────────

class TestBarsToNamespace:
    def test_converts_list(self):
        bar_dicts = [
            {"date": "2026-01-02", "open": 100.0, "high": 105.0, "low": 98.0, "close": 103.0, "volume": 1000},
            {"date": "2026-01-03", "open": 103.0, "high": 107.0, "low": 102.0, "close": 106.0, "volume": 1500},
        ]
        result = bars_to_namespace(bar_dicts)
        assert len(result) == 2
        assert result[0].date == "2026-01-02"
        assert result[0].close == 103.0
        assert result[1].volume == 1500

    def test_empty_list(self):
        assert bars_to_namespace([]) == []

    def test_missing_volume_defaults_zero(self):
        bar_dicts = [{"date": "2026-01-02", "open": 100.0, "high": 105.0, "low": 98.0, "close": 103.0}]
        result = bars_to_namespace(bar_dicts)
        assert result[0].volume == 0

    def test_namespace_attributes(self):
        bar_dict = {"date": "2026-01-02", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100}
        ns = bars_to_namespace([bar_dict])[0]
        assert hasattr(ns, "date")
        assert hasattr(ns, "open")
        assert hasattr(ns, "high")
        assert hasattr(ns, "low")
        assert hasattr(ns, "close")
        assert hasattr(ns, "volume")


# ── offline detection ─────────────────────────────────────────────────────────

class TestFetchBarsOffline:
    @pytest.mark.asyncio
    async def test_timeout_returns_tws_offline(self, monkeypatch):
        """asyncio.TimeoutError during connect → tws_offline."""
        async def _fake_connect(*a, **kw):
            raise asyncio.TimeoutError()

        # Patch wait_for to raise
        monkeypatch.setattr("asyncio.wait_for", AsyncMock(side_effect=asyncio.TimeoutError()))

        result = await fetch_bars("SPY", host="127.0.0.1", port=9999)
        assert result["status"] == "tws_offline"
        assert result["symbol"] == "SPY"
        assert "bars" in result

    @pytest.mark.asyncio
    async def test_os_error_returns_tws_offline(self, monkeypatch):
        """OSError during connect → tws_offline."""
        monkeypatch.setattr("asyncio.wait_for", AsyncMock(side_effect=OSError("refused")))

        result = await fetch_bars("SPY", host="127.0.0.1", port=9999)
        assert result["status"] == "tws_offline"

    @pytest.mark.asyncio
    async def test_generic_exception_returns_tws_offline(self, monkeypatch):
        """Any other exception during connect → tws_offline."""
        monkeypatch.setattr("asyncio.wait_for", AsyncMock(side_effect=Exception("unexpected")))

        result = await fetch_bars("SPY", host="127.0.0.1", port=9999)
        assert result["status"] == "tws_offline"


# ── success path ──────────────────────────────────────────────────────────────

def _make_fake_bar(date_str: str, o: float, h: float, l: float, c: float, v: int):
    class _Bar:
        date = date_str
        open = o
        high = h
        low = l
        close = c
        volume = v
    return _Bar()


class _FakeStockBase:
    """Minimal Stock stand-in accepted by fetch_bars."""
    def __init__(self, *a, **kw):
        pass


class TestFetchBarsSuccess:
    def _patch_for_success(self, monkeypatch, fake_bars):
        """Common patches needed for success-path tests."""

        class _FakeIB:
            async def connectAsync(self, *a, **kw):
                pass
            async def qualifyContractsAsync(self, *a, **kw):
                pass
            async def reqHistoricalDataAsync(self, *a, **kw):
                return fake_bars
            def disconnect(self):
                pass

        async def _passthrough(coro, timeout=None):
            return await coro

        monkeypatch.setattr("asyncio.wait_for", _passthrough)
        monkeypatch.setattr("tools.tws_historical_data.IB", _FakeIB)
        monkeypatch.setattr("tools.tws_historical_data.Stock", _FakeStockBase)
        return _FakeIB

    @pytest.mark.asyncio
    async def test_returns_ok_with_bars(self, monkeypatch):
        """When IB returns bars, fetch_bars returns status ok."""
        fake_bars = [
            _make_fake_bar("2026-01-02", 100.0, 105.0, 98.0, 103.0, 1000),
            _make_fake_bar("2026-01-03", 103.0, 107.0, 102.0, 106.0, 1500),
        ]
        self._patch_for_success(monkeypatch, fake_bars)
        result = await fetch_bars("SPY")
        assert result["status"] == "ok"
        assert result["count"] == 2
        assert len(result["bars"]) == 2
        assert result["bars"][0]["date"] == "2026-01-02"

    @pytest.mark.asyncio
    async def test_empty_bars_returns_no_data(self, monkeypatch):
        """When IB returns empty list → no_data."""
        self._patch_for_success(monkeypatch, [])
        result = await fetch_bars("SPY")
        assert result["status"] == "no_data"

    @pytest.mark.asyncio
    async def test_malformed_bars_skipped(self, monkeypatch):
        """Bars with None values are skipped; valid bars still returned."""
        class _BadBar:
            date = "2026-01-02"
            open = None
            high = None
            low = None
            close = None
            volume = None

        class _GoodBar:
            date = "2026-01-03"
            open = 100.0
            high = 105.0
            low = 98.0
            close = 103.0
            volume = 1000

        self._patch_for_success(monkeypatch, [_BadBar(), _GoodBar()])
        result = await fetch_bars("SPY")
        # _BadBar has None values so float(None) raises TypeError → skipped
        # Only _GoodBar should be in result
        assert result["status"] == "ok"
        assert result["count"] == 1
        assert result["bars"][0]["date"] == "2026-01-03"
