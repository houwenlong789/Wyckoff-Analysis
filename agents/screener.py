# -*- coding: utf-8 -*-
"""
ScreenerAgent — 4 层 Wyckoff 漏斗筛选。

Phase 1: 整体调用 run_funnel()，同时产出 ScreenResult + benchmark_context。
Phase 2: 拆解为 run_layer1/2/3/4 独立 Tool 调用。
"""
from __future__ import annotations

import logging
import time

from agents.contracts import AgentResult, MarketContext, PipelineStatus, ScreenResult

logger = logging.getLogger(__name__)


class ScreenerAgent:
    """
    确定性 Agent：执行 4 层 Wyckoff 漏斗筛选。

    Phase 1: 直接调用 core/funnel_pipeline.run_funnel()。
    同时产出:
      - ScreenResult (候选股列表)
      - benchmark_context (大盘环境, 存入 context 供 MarketContextAgent 转换)
    """

    name = "screener"

    def __init__(self, webhook_url: str = "", notify: bool = True):
        self.webhook_url = webhook_url
        self.notify = notify

    def run(self, context: dict) -> AgentResult:
        """
        执行漏斗筛选。

        Phase 1: 调用 run_funnel(webhook_url) 获取完整结果。
        将 benchmark_context 存入 context["_benchmark_context_raw"] 供后续 Agent 使用。
        """
        t0 = time.monotonic()
        try:
            from core.funnel_pipeline import run_funnel

            ok, symbols_info, benchmark_context = run_funnel(
                self.webhook_url,
                notify=self.notify,
            )

            # 存储 raw benchmark_context 供 MarketContextAgent 使用
            context["_benchmark_context_raw"] = benchmark_context

            if not ok:
                return AgentResult(
                    agent_name=self.name,
                    status=PipelineStatus.FAILED,
                    error="run_funnel returned ok=False",
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )

            screen = ScreenResult.from_legacy(
                symbols_info=symbols_info,
                total_scanned=0,  # Phase 2 才有精确统计
            )
            logger.info(
                "ScreenerAgent: %d candidates selected",
                len(screen.candidates),
            )
            return AgentResult(
                agent_name=self.name,
                status=PipelineStatus.COMPLETED,
                payload=screen,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception as e:
            logger.exception("ScreenerAgent failed")
            return AgentResult(
                agent_name=self.name,
                status=PipelineStatus.FAILED,
                error=str(e),
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
