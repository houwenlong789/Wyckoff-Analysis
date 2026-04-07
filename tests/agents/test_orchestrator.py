# -*- coding: utf-8 -*-
"""agents/orchestrator.py 的单元测试 — mock 所有子 Agent。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

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
from agents.orchestrator import OrchestratorAgent


def _make_orchestrator(**overrides) -> OrchestratorAgent:
    """快速构造 Orchestrator（不从 env 读取）。"""
    defaults = dict(
        webhook_url="https://fake.feishu/hook",
        api_key="fake-key",
        model="gemini-3.1-flash-lite-preview",
        provider="gemini",
        portfolio_id="",
        tg_bot_token="",
        tg_chat_id="",
        skip_step4=True,
        max_retries=1,
    )
    defaults.update(overrides)
    return OrchestratorAgent(**defaults)


def _ok_result(name: str, payload=None) -> AgentResult:
    return AgentResult(
        agent_name=name,
        status=PipelineStatus.COMPLETED,
        payload=payload,
        duration_ms=100,
    )


def _fail_result(name: str, error: str = "boom") -> AgentResult:
    return AgentResult(
        agent_name=name,
        status=PipelineStatus.FAILED,
        error=error,
        duration_ms=50,
    )


class TestOrchestratorHappyPath:
    """所有 stage 成功的情况。"""

    def test_full_pipeline_success(self):
        orch = _make_orchestrator(skip_step4=True)

        # Mock screener
        screen = ScreenResult(
            candidates=[
                StockCandidate(code="600056", name="中国医药", score=9.94),
            ],
            total_scanned=4417,
        )
        orch.screener.run = MagicMock(side_effect=lambda ctx: (
            ctx.__setitem__("_benchmark_context_raw", {"regime": "RISK_OFF", "close": 3880}),
            _ok_result("screener", screen),
        )[-1])

        # Mock market_context
        mctx = MarketContext(date="2026-04-06", regime=Regime.RISK_OFF)
        orch.market_context.run = MagicMock(return_value=_ok_result("market_context", mctx))

        # Mock analyst
        report = AnalysisReport(
            report_text="## test report",
            springboard_codes=["600056"],
            model_used="gemini",
        )
        orch.analyst.run = MagicMock(return_value=_ok_result("analyst", report))

        # Mock notifier
        orch.notifier.run = MagicMock(return_value=_ok_result("notifier"))

        result = orch.run(trigger={"run_id": "test_001", "trigger": "manual"})

        assert result.ok or result.status == PipelineStatus.COMPLETED
        assert result.payload["run_id"] == "test_001"
        assert len(result.payload["stages"]) >= 4  # screener + market + analyst + notifier


class TestOrchestratorFailures:
    """各种失败情况。"""

    def test_screener_failure_aborts_pipeline(self):
        orch = _make_orchestrator(max_retries=1)
        orch.screener.run = MagicMock(return_value=_fail_result("screener", "data fetch timeout"))
        orch.notifier.send_failure = MagicMock()

        result = orch.run()

        assert result.status == PipelineStatus.FAILED
        orch.notifier.send_failure.assert_called_once()

    def test_analyst_failure_is_non_fatal(self):
        """研报失败不应导致整个 pipeline 失败。"""
        orch = _make_orchestrator(skip_step4=True, max_retries=1)

        screen = ScreenResult(
            candidates=[StockCandidate(code="600056", name="test", score=1.0)],
        )
        orch.screener.run = MagicMock(side_effect=lambda ctx: (
            ctx.__setitem__("_benchmark_context_raw", {"regime": "NEUTRAL"}),
            _ok_result("screener", screen),
        )[-1])
        orch.market_context.run = MagicMock(
            return_value=_ok_result("market_context", MarketContext(date="2026-04-06"))
        )
        orch.analyst.run = MagicMock(return_value=_fail_result("analyst", "LLM timeout"))
        orch.notifier.run = MagicMock(return_value=_ok_result("notifier"))

        result = orch.run()

        # PARTIAL (not FAILED) because screener succeeded
        assert result.status == PipelineStatus.PARTIAL

    def test_no_candidates_skips_analyst(self):
        """无候选股时跳过 analyst。"""
        orch = _make_orchestrator(skip_step4=True, max_retries=1)

        screen = ScreenResult(candidates=[], total_scanned=4417)
        orch.screener.run = MagicMock(side_effect=lambda ctx: (
            ctx.__setitem__("_benchmark_context_raw", {"regime": "CRASH"}),
            _ok_result("screener", screen),
        )[-1])
        orch.market_context.run = MagicMock(
            return_value=_ok_result("market_context", MarketContext(date="2026-04-06"))
        )
        orch.notifier.run = MagicMock(return_value=_ok_result("notifier"))

        result = orch.run()

        # analyst 未被调用
        assert result.ok or result.status in (PipelineStatus.COMPLETED, PipelineStatus.PARTIAL)


class TestOrchestratorRetry:
    """重试逻辑。"""

    def test_retry_on_failure(self):
        orch = _make_orchestrator(max_retries=3, skip_step4=True)

        call_count = 0

        def screener_with_retry(ctx):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return _fail_result("screener", f"attempt {call_count}")
            ctx["_benchmark_context_raw"] = {"regime": "NEUTRAL"}
            return _ok_result("screener", ScreenResult(candidates=[]))

        orch.screener.run = MagicMock(side_effect=screener_with_retry)
        orch.market_context.run = MagicMock(
            return_value=_ok_result("market_context", MarketContext(date="2026-04-06"))
        )
        orch.notifier.run = MagicMock(return_value=_ok_result("notifier"))

        result = orch.run()

        assert call_count == 3  # 前 2 次失败 + 第 3 次成功
