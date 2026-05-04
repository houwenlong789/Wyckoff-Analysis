# -*- coding: utf-8 -*-
"""data_source 中 mootdx 最高优先级链路测试。"""
from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

import integrations.data_source as ds
import integrations.mootdx_source as ms


def _sample_cn_hist() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "日期": "2026-04-18",
                "开盘": 10.0,
                "最高": 10.5,
                "最低": 9.9,
                "收盘": 10.3,
                "成交量": 1000000.0,
                "成交额": 10000000.0,
                "涨跌幅": 1.2,
                "换手率": pd.NA,
                "振幅": 2.3,
            }
        ]
    )


def _disable_late_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_SOURCE_DISABLE_AKSHARE", "1")
    monkeypatch.setenv("DATA_SOURCE_DISABLE_BAOSTOCK", "1")
    monkeypatch.setenv("DATA_SOURCE_DISABLE_EFINANCE", "1")
    monkeypatch.delenv("DATA_SOURCE_DISABLE_MOOTDX", raising=False)
    monkeypatch.delenv("DATA_SOURCE_DISABLE_TICKFLOW", raising=False)


def test_fetch_stock_hist_prefers_mootdx_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_late_fallbacks(monkeypatch)
    monkeypatch.setenv("TICKFLOW_API_KEY", "dummy")
    monkeypatch.setattr(ms, "fetch_stock_mootdx", lambda *args, **kwargs: _sample_cn_hist())

    def _raise_tickflow_if_called(*args, **kwargs):
        raise RuntimeError("should_not_call_tickflow")

    monkeypatch.setattr(ds, "_fetch_stock_tickflow", _raise_tickflow_if_called)

    out = ds.fetch_stock_hist("600519", "2026-04-10", "2026-04-18", adjust="qfq")
    assert not out.empty
    assert out.attrs.get("source") == "mootdx"


def test_fetch_stock_hist_skips_mootdx_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_late_fallbacks(monkeypatch)
    monkeypatch.setenv("DATA_SOURCE_DISABLE_MOOTDX", "1")
    monkeypatch.setenv("TICKFLOW_API_KEY", "dummy")
    monkeypatch.setattr("integrations.tushare_client.get_pro", lambda: object())

    def _raise_mootdx_if_called(*args, **kwargs):
        raise RuntimeError("should_not_call_mootdx")

    monkeypatch.setattr(ms, "fetch_stock_mootdx", _raise_mootdx_if_called)
    monkeypatch.setattr(ds, "_fetch_stock_tickflow", lambda *args, **kwargs: _sample_cn_hist())

    out = ds.fetch_stock_hist("000001", "2026-04-10", "2026-04-18", adjust="qfq")
    assert not out.empty
    assert out.attrs.get("source") == "tickflow"


def test_fetch_stock_hist_falls_back_to_tickflow_when_mootdx_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_late_fallbacks(monkeypatch)
    monkeypatch.setenv("TICKFLOW_API_KEY", "dummy")
    monkeypatch.setattr("integrations.tushare_client.get_pro", lambda: object())

    def _raise_mootdx(*args, **kwargs):
        raise RuntimeError("mootdx timeout")

    monkeypatch.setattr(ms, "fetch_stock_mootdx", _raise_mootdx)
    monkeypatch.setattr(ds, "_fetch_stock_tickflow", lambda *args, **kwargs: _sample_cn_hist())

    out = ds.fetch_stock_hist("000001", "2026-04-10", "2026-04-18", adjust="qfq")
    assert not out.empty
    assert out.attrs.get("source") == "tickflow"


def test_fetch_stock_mootdx_normalizes_raw_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    class _FakeClient:
        def bars(self, **kwargs):
            calls.append(kwargs)
            return pd.DataFrame(
                [
                    {
                        "datetime": "2026-04-16",
                        "open": 9.8,
                        "high": 10.2,
                        "low": 9.7,
                        "close": 10.0,
                        "vol": 900000.0,
                        "amount": 9000000.0,
                    },
                    {
                        "datetime": "2026-04-17",
                        "open": 10.0,
                        "high": 11.2,
                        "low": 9.9,
                        "close": 11.0,
                        "vol": 1000000.0,
                        "amount": 11000000.0,
                    },
                    {
                        "datetime": "2026-04-18",
                        "open": 11.0,
                        "high": 12.4,
                        "low": 10.8,
                        "close": 12.1,
                        "vol": 1200000.0,
                        "amount": 14520000.0,
                    },
                ]
            )

        def close(self):
            calls.append({"closed": True})

    class _FakeQuotes:
        @staticmethod
        def factory(**kwargs):
            calls.append({"factory": kwargs})
            return _FakeClient()

    mootdx_pkg = types.ModuleType("mootdx")
    quotes_mod = types.ModuleType("mootdx.quotes")
    quotes_mod.Quotes = _FakeQuotes
    monkeypatch.setitem(sys.modules, "mootdx", mootdx_pkg)
    monkeypatch.setitem(sys.modules, "mootdx.quotes", quotes_mod)

    out = ms.fetch_stock_mootdx("000001", "20260417", "20260418", adjust="qfq")

    assert out["日期"].tolist() == ["2026-04-17", "2026-04-18"]
    assert list(out.columns) == [
        "日期",
        "开盘",
        "最高",
        "最低",
        "收盘",
        "成交量",
        "成交额",
        "涨跌幅",
        "换手率",
        "振幅",
    ]
    assert out.iloc[0]["涨跌幅"] == pytest.approx(10.0)
    assert calls[1]["adjust"] == "qfq"
    assert calls[-1] == {"closed": True}
