from __future__ import annotations

from datetime import datetime

import pandas as pd

from core.intraday_analysis import (
    analyze_intraday,
    ensure_intraday_df,
    infer_session_vwap,
)


def _make_1m_df(bars: int = 180, start: float = 10.0, end: float = 10.5) -> pd.DataFrame:
    idx = pd.date_range(start=datetime(2026, 5, 27, 9, 30), periods=bars, freq="1min", tz="Asia/Shanghai")
    close = pd.Series([start + (end - start) * i / max(bars - 1, 1) for i in range(bars)])
    return pd.DataFrame(
        {
            "datetime": idx,
            "open": close * 0.999,
            "high": close * 1.003,
            "low": close * 0.997,
            "close": close,
            "volume": [1000.0] * bars,
            "amount": close * 1000.0,
        }
    )


def _make_5m_df(bars: int = 36, start: float = 10.0, end: float = 10.5) -> pd.DataFrame:
    idx = pd.date_range(start=datetime(2026, 5, 27, 9, 30), periods=bars, freq="5min", tz="Asia/Shanghai")
    close = pd.Series([start + (end - start) * i / max(bars - 1, 1) for i in range(bars)])
    return pd.DataFrame(
        {
            "datetime": idx,
            "open": close * 0.999,
            "high": close * 1.003,
            "low": close * 0.997,
            "close": close,
            "volume": [5000.0] * bars,
            "amount": close * 5000.0,
        }
    )


class TestEnsureIntradayDf:
    def test_empty_input(self):
        result = ensure_intraday_df(pd.DataFrame())
        assert result.empty

    def test_missing_datetime_col(self):
        df = pd.DataFrame({"close": [10.0, 10.1]})
        assert ensure_intraday_df(df).empty

    def test_timestamp_column_converted(self):
        now_ms = int(datetime(2026, 5, 27, 10, 0).timestamp() * 1000)
        df = pd.DataFrame(
            {
                "timestamp": [now_ms, now_ms + 60_000],
                "close": [10.0, 10.1],
            }
        )
        result = ensure_intraday_df(df)
        assert len(result) == 2
        assert "datetime" in result.columns

    def test_normal_df(self):
        df = _make_1m_df(bars=60)
        result = ensure_intraday_df(df)
        assert len(result) == 60


class TestInferSessionVwap:
    def test_zero_volume(self):
        close = pd.Series([10.0, 10.1, 10.2])
        vwap, scale = infer_session_vwap(close, 0.0, 0.0)
        assert vwap == 10.2

    def test_normal_vwap(self):
        close = pd.Series([10.0] * 30)
        vwap, scale = infer_session_vwap(close, 100000.0, 1000000.0)
        assert abs(vwap - 10.0) < 0.5


class TestAnalyzeIntraday:
    def test_empty_df_returns_zero_profile(self):
        profile = analyze_intraday(pd.DataFrame())
        assert profile.bars == 0
        assert profile.strength_score == 0.0

    def test_too_few_bars(self):
        df = _make_1m_df(bars=5)
        profile = analyze_intraday(df)
        assert profile.bars == 5
        assert profile.strength_score == 0.0

    def test_uptrend_profile(self):
        df_1m = _make_1m_df(bars=180, start=10.0, end=11.0)
        df_5m = _make_5m_df(bars=36, start=10.0, end=11.0)
        profile = analyze_intraday(df_1m, df_5m)
        assert profile.bars == 180
        assert profile.trend_short == "up"
        assert profile.close_pos > 0.8
        assert profile.strength_score > 60

    def test_downtrend_profile(self):
        df_1m = _make_1m_df(bars=180, start=11.0, end=10.0)
        df_5m = _make_5m_df(bars=36, start=11.0, end=10.0)
        profile = analyze_intraday(df_1m, df_5m)
        assert profile.trend_short == "down"
        assert profile.close_pos < 0.2
        assert profile.strength_score < 40

    def test_flat_profile(self):
        df_1m = _make_1m_df(bars=180, start=10.0, end=10.0)
        profile = analyze_intraday(df_1m)
        assert profile.trend_short == "flat"

    def test_to_dict(self):
        df_1m = _make_1m_df(bars=60)
        profile = analyze_intraday(df_1m)
        d = profile.to_dict()
        assert isinstance(d, dict)
        assert "strength_score" in d
        assert "vwap_pos" in d

    def test_spring_quality_with_context(self):
        df_1m = _make_1m_df(bars=180, start=10.0, end=10.5)
        df_1m.loc[10:15, "low"] = 9.5
        df_1m.loc[10:15, "close"] = 9.6
        df_1m.loc[16:20, "close"] = 10.1
        context = {"support_level": 10.0}
        profile = analyze_intraday(df_1m, daily_context=context)
        assert profile.spring_quality is not None
        assert profile.spring_quality > 0

    def test_spring_quality_none_without_context(self):
        df_1m = _make_1m_df(bars=60)
        profile = analyze_intraday(df_1m)
        assert profile.spring_quality is None
