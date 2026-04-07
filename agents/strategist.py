# -*- coding: utf-8 -*-
"""
StrategyAgent — LLM 驱动的 OMS 决策。

Phase 1: 整体调用 step4_rebalancer.run()。
Phase 2: 拆分为 build_prompt → call_llm → parse_json → run_oms 独立 Tool。
"""
from __future__ import annotations

import logging
import time

from agents.contracts import (
    AgentResult,
    AnalysisReport,
    MarketContext,
    PipelineStatus,
    ScreenResult,
    StrategyDecision,
)

logger = logging.getLogger(__name__)


class StrategyAgent:
    """
    LLM Agent：生成持仓去留决策 + 新标的买入策略。

    使用 PRIVATE_PM_DECISION_JSON_PROMPT 让 LLM 以威科夫视角
    对持仓和候选标的做结构化 JSON 决策。
    """

    name = "strategist"

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        portfolio_id: str = "",
        tg_bot_token: str = "",
        tg_chat_id: str = "",
    ):
        self.api_key = api_key
        self.model = model
        self.portfolio_id = portfolio_id
        self.tg_bot_token = tg_bot_token
        self.tg_chat_id = tg_chat_id

    def run(self, context: dict) -> AgentResult:
        """
        Phase 1: 调用 step4_rebalancer.run() 做持仓再平衡。

        依赖：
          context["analyst"].payload -> AnalysisReport
          context["market_context"].payload -> MarketContext
          context["screener"].payload -> ScreenResult
        """
        t0 = time.monotonic()
        try:
            # 检查前置条件
            if not self.portfolio_id:
                return AgentResult(
                    agent_name=self.name,
                    status=PipelineStatus.COMPLETED,
                    payload=StrategyDecision(reason="skipped_no_portfolio"),
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )

            if not self.tg_bot_token or not self.tg_chat_id:
                return AgentResult(
                    agent_name=self.name,
                    status=PipelineStatus.COMPLETED,
                    payload=StrategyDecision(reason="skipped_telegram_unconfigured"),
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )

            analyst_result: AgentResult = context.get("analyst")
            market_result: AgentResult = context.get("market_context")
            screen_result: AgentResult = context.get("screener")

            # 构建 step4 输入
            report_text = ""
            if analyst_result and analyst_result.payload:
                report: AnalysisReport = analyst_result.payload
                report_text = report.report_text

            benchmark_context = None
            if market_result and market_result.ok:
                mctx: MarketContext = market_result.payload
                benchmark_context = mctx.to_legacy_dict()

            # 构建 candidate_meta: 仅起跳板代码
            candidate_meta: list[dict] = []
            if (
                analyst_result
                and analyst_result.payload
                and screen_result
                and screen_result.ok
            ):
                springboard_set = set(analyst_result.payload.springboard_codes)
                screen: ScreenResult = screen_result.payload
                for c in screen.candidates:
                    if c.code in springboard_set:
                        candidate_meta.append(c.to_legacy_dict())

            from scripts.step4_rebalancer import run as run_step4

            ok, reason = run_step4(
                external_report=report_text,
                benchmark_context=benchmark_context,
                api_key=self.api_key,
                model=self.model,
                candidate_meta=candidate_meta or None,
                portfolio_id=self.portfolio_id,
                tg_bot_token=self.tg_bot_token,
                tg_chat_id=self.tg_chat_id,
            )

            logger.info("StrategyAgent: ok=%s reason=%s", ok, reason)
            return AgentResult(
                agent_name=self.name,
                status=PipelineStatus.COMPLETED if ok else PipelineStatus.FAILED,
                payload=StrategyDecision(
                    model_used=self.model,
                    reason=reason,
                ),
                error=None if ok else reason,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception as e:
            logger.exception("StrategyAgent failed")
            return AgentResult(
                agent_name=self.name,
                status=PipelineStatus.FAILED,
                error=str(e),
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
