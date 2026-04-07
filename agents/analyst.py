# -*- coding: utf-8 -*-
"""
WyckoffAnalystAgent — LLM 驱动的三阵营研报生成。

Phase 1: 整体调用 run_step3() + extract_operation_pool_codes()。
Phase 2: 拆分为 build_prompt → call_llm → parse_report 三个 Tool。
Phase 3: 结构化 JSON 输出替代 regex 解析。
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
)

logger = logging.getLogger(__name__)


class WyckoffAnalystAgent:
    """
    LLM Agent：生成威科夫三阵营研报。

    使用 WYCKOFF_FUNNEL_SYSTEM_PROMPT 让 LLM 对候选股做深度审判，
    产出逻辑破产 / 储备营地 / 起跳板三阵营分类。
    """

    name = "analyst"

    def __init__(
        self,
        webhook_url: str = "",
        api_key: str = "",
        model: str = "",
        provider: str = "gemini",
        llm_base_url: str = "",
        wecom_webhook: str = "",
        dingtalk_webhook: str = "",
        notify: bool = True,
    ):
        self.webhook_url = webhook_url
        self.api_key = api_key
        self.model = model
        self.provider = provider
        self.llm_base_url = llm_base_url
        self.wecom_webhook = wecom_webhook
        self.dingtalk_webhook = dingtalk_webhook
        self.notify = notify

    def run(self, context: dict) -> AgentResult:
        """
        Phase 1: 调用 run_step3() 生成研报 + 提取起跳板代码。

        依赖：
          context["screener"].payload -> ScreenResult
          context["market_context"].payload -> MarketContext
        """
        t0 = time.monotonic()
        try:
            screen_result: AgentResult = context.get("screener")
            market_result: AgentResult = context.get("market_context")

            if not screen_result or not screen_result.ok:
                return AgentResult(
                    agent_name=self.name,
                    status=PipelineStatus.FAILED,
                    error="screener not available or failed",
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )

            screen: ScreenResult = screen_result.payload
            symbols_info = screen.to_legacy_symbols_info()

            if not symbols_info:
                return AgentResult(
                    agent_name=self.name,
                    status=PipelineStatus.COMPLETED,
                    payload=AnalysisReport(report_text="", springboard_codes=[]),
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )

            benchmark_context = None
            if market_result and market_result.ok:
                mctx: MarketContext = market_result.payload
                benchmark_context = mctx.to_legacy_dict()

            from core.batch_report import extract_operation_pool_codes, run_step3

            ok, reason, report_text = run_step3(
                symbols_info,
                self.webhook_url,
                self.api_key,
                self.model,
                benchmark_context=benchmark_context,
                notify=self.notify,
                provider=self.provider,
                llm_base_url=self.llm_base_url,
                wecom_webhook=self.wecom_webhook,
                dingtalk_webhook=self.dingtalk_webhook,
            )

            springboard_codes: list[str] = []
            if ok and report_text:
                allowed_codes = [str(c.code) for c in screen.candidates]
                try:
                    springboard_codes = extract_operation_pool_codes(
                        report=report_text,
                        allowed_codes=allowed_codes,
                    )
                except Exception as e:
                    logger.warning("Failed to extract springboard codes: %s", e)

            if not ok:
                return AgentResult(
                    agent_name=self.name,
                    status=PipelineStatus.FAILED,
                    payload=AnalysisReport(
                        report_text=report_text or "",
                        springboard_codes=springboard_codes,
                        model_used=self.model,
                    ),
                    error=reason,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )

            logger.info(
                "WyckoffAnalystAgent: report=%d chars, springboard=%d codes",
                len(report_text or ""), len(springboard_codes),
            )
            return AgentResult(
                agent_name=self.name,
                status=PipelineStatus.COMPLETED,
                payload=AnalysisReport(
                    report_text=report_text or "",
                    springboard_codes=springboard_codes,
                    model_used=self.model,
                ),
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception as e:
            logger.exception("WyckoffAnalystAgent failed")
            return AgentResult(
                agent_name=self.name,
                status=PipelineStatus.FAILED,
                error=str(e),
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
