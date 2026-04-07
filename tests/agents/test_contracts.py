# -*- coding: utf-8 -*-
"""agents/contracts.py 的单元测试。"""
from __future__ import annotations

import pytest
from agents.contracts import (
    AgentResult,
    AnalysisReport,
    MarketContext,
    PipelineStatus,
    Regime,
    ScreenResult,
    StockCandidate,
    StrategyDecision,
)


# ---------------------------------------------------------------------------
# Regime
# ---------------------------------------------------------------------------

class TestRegime:
    def test_from_str_normal(self):
        assert Regime.from_str("RISK_ON") == Regime.RISK_ON
        assert Regime.from_str("risk_off") == Regime.RISK_OFF
        assert Regime.from_str("  CRASH  ") == Regime.CRASH

    def test_from_str_fallback(self):
        assert Regime.from_str("") == Regime.NEUTRAL
        assert Regime.from_str("UNKNOWN") == Regime.NEUTRAL
        assert Regime.from_str(None) == Regime.NEUTRAL


# ---------------------------------------------------------------------------
# MarketContext
# ---------------------------------------------------------------------------

class TestMarketContext:
    SAMPLE_BENCHMARK = {
        "regime": "RISK_OFF",
        "close": 3880.10,
        "ma50": 4065.15,
        "ma200": 3853.14,
        "recent3_cum_pct": -0.30,
        "main_today_pct": -0.99,
        "main_code": "000001",
        "smallcap_code": "399006",
        "smallcap_close": 2100.0,
        "smallcap_recent3_cum_pct": -0.50,
        "breadth": {"ratio_pct": 35.0},
        "sector_rotation": {"headline": "test"},
    }

    def test_from_legacy_roundtrip(self):
        ctx = MarketContext.from_legacy_dict(self.SAMPLE_BENCHMARK)
        assert ctx.regime == Regime.RISK_OFF
        assert ctx.benchmark_metrics["close"] == 3880.10
        assert ctx.breadth["ratio_pct"] == 35.0

        # Roundtrip
        restored = ctx.to_legacy_dict()
        assert restored["regime"] == "RISK_OFF"
        assert restored["close"] == 3880.10
        assert restored["sector_rotation"] == {"headline": "test"}

    def test_from_empty_dict(self):
        ctx = MarketContext.from_legacy_dict({})
        assert ctx.regime == Regime.NEUTRAL


# ---------------------------------------------------------------------------
# StockCandidate
# ---------------------------------------------------------------------------

class TestStockCandidate:
    SAMPLE_DICT = {
        "code": "600056",
        "name": "中国医药",
        "tag": "SOS",
        "track": "Trend",
        "stage": "Markup",
        "score": 9.94,
        "priority_score": 9.94,
        "industry": "医药商业",
        "sector_state": "LEADING",
        "exit_signal": "",
        "extra_field": "should_be_in_raw",
    }

    def test_from_legacy_roundtrip(self):
        c = StockCandidate.from_legacy_dict(self.SAMPLE_DICT)
        assert c.code == "600056"
        assert c.name == "中国医药"
        assert c.score == 9.94
        assert c.track == "Trend"
        # raw 保留完整原始 dict
        assert c.raw["extra_field"] == "should_be_in_raw"

        # Roundtrip
        restored = c.to_legacy_dict()
        assert restored["code"] == "600056"
        assert restored["extra_field"] == "should_be_in_raw"

    def test_default_values(self):
        c = StockCandidate.from_legacy_dict({})
        assert c.code == ""
        assert c.score == 0.0


# ---------------------------------------------------------------------------
# ScreenResult
# ---------------------------------------------------------------------------

class TestScreenResult:
    def test_from_legacy(self):
        symbols_info = [
            {"code": "600056", "name": "中国医药", "score": 9.94},
            {"code": "300632", "name": "光莆股份", "score": 5.93},
        ]
        screen = ScreenResult.from_legacy(symbols_info, total_scanned=4417)
        assert len(screen.candidates) == 2
        assert screen.total_scanned == 4417
        assert screen.candidates[0].code == "600056"

    def test_to_legacy_symbols_info(self):
        symbols_info = [
            {"code": "600056", "name": "中国医药", "score": 9.94, "tag": "SOS"},
        ]
        screen = ScreenResult.from_legacy(symbols_info)
        restored = screen.to_legacy_symbols_info()
        assert len(restored) == 1
        assert restored[0]["code"] == "600056"
        assert restored[0]["tag"] == "SOS"


# ---------------------------------------------------------------------------
# AnalysisReport
# ---------------------------------------------------------------------------

class TestAnalysisReport:
    def test_defaults(self):
        r = AnalysisReport()
        assert r.report_text == ""
        assert r.springboard_codes == []


# ---------------------------------------------------------------------------
# StrategyDecision
# ---------------------------------------------------------------------------

class TestStrategyDecision:
    def test_defaults(self):
        d = StrategyDecision()
        assert d.reason == "ok"
        assert d.decisions == []


# ---------------------------------------------------------------------------
# AgentResult
# ---------------------------------------------------------------------------

class TestAgentResult:
    def test_ok_property(self):
        r = AgentResult(agent_name="test", status=PipelineStatus.COMPLETED)
        assert r.ok is True

        r2 = AgentResult(agent_name="test", status=PipelineStatus.FAILED, error="boom")
        assert r2.ok is False

    def test_to_checkpoint_dict(self):
        r = AgentResult(
            agent_name="screener",
            status=PipelineStatus.COMPLETED,
            duration_ms=1234,
            retries=1,
        )
        d = r.to_checkpoint_dict()
        assert d["agent_name"] == "screener"
        assert d["status"] == "completed"
        assert d["duration_ms"] == 1234
        assert d["retries"] == 1
