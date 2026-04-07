# -*- coding: utf-8 -*-
"""
MarketContextAgent — 大盘水温 + regime 计算。

Phase 1: 调用现有 run_funnel() 的大盘分析部分（拆分自 wyckoff_funnel.run()）。
         由于 run_funnel() 内部大盘分析与股票筛选耦合，Phase 1 不拆分，
         只在 OrchestratorAgent 层面做数据传递。

实际逻辑：接收 benchmark_context dict（由 ScreenerAgent 一并产出），
         转换为 MarketContext dataclass。
"""
from __future__ import annotations

import logging
import time

from agents.contracts import AgentResult, MarketContext, PipelineStatus

logger = logging.getLogger(__name__)


class MarketContextAgent:
    """
    确定性 Agent：从 benchmark_context 原始 dict 构建 MarketContext。

    Phase 1 设计说明：
    当前 run_funnel() 内部同时产出 benchmark_context 和 symbols_info，
    无法独立调用大盘分析。因此 Phase 1 中 MarketContextAgent 的 run()
    接收已有的 benchmark_context dict 做转换即可。
    Phase 2 会将大盘分析逻辑提取为独立 Tool。
    """

    name = "market_context"

    def run(self, context: dict) -> AgentResult:
        """
        Phase 1: 从 context["_benchmark_context_raw"] 转换为 MarketContext。

        当 run_funnel() 已经执行过时，benchmark_context 已经存在于 context 中。
        此 Agent 负责将 raw dict 标准化为 typed dataclass。
        """
        t0 = time.monotonic()
        try:
            raw = context.get("_benchmark_context_raw")
            if not raw or not isinstance(raw, dict):
                return AgentResult(
                    agent_name=self.name,
                    status=PipelineStatus.FAILED,
                    error="benchmark_context not provided",
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )

            market_ctx = MarketContext.from_legacy_dict(raw)
            logger.info(
                "MarketContextAgent: date=%s regime=%s",
                market_ctx.date, market_ctx.regime.value,
            )
            return AgentResult(
                agent_name=self.name,
                status=PipelineStatus.COMPLETED,
                payload=market_ctx,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception as e:
            logger.exception("MarketContextAgent failed")
            return AgentResult(
                agent_name=self.name,
                status=PipelineStatus.FAILED,
                error=str(e),
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
