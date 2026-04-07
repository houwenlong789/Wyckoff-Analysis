# -*- coding: utf-8 -*-
"""
OrchestratorAgent — Pipeline 编排 + Supabase checkpoint。

协调所有 Agent 的执行顺序，管理状态，处理错误和重试。
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from agents.analyst import WyckoffAnalystAgent
from agents.contracts import AgentResult, PipelineStatus
from agents.market_context import MarketContextAgent
from agents.notifier import NotifierAgent
from agents.screener import ScreenerAgent
from agents.strategist import StrategyAgent

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")


def _now_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


class OrchestratorAgent:
    """
    确定性 Agent：协调 pipeline 各阶段的执行。

    执行顺序:
      1. ScreenerAgent — 漏斗筛选（同时产出 benchmark_context）
      2. MarketContextAgent — 标准化大盘环境
      3. WyckoffAnalystAgent — LLM 三阵营研报
      4. StrategyAgent — LLM OMS 决策
      5. NotifierAgent — 汇总通知

    每个 stage 执行后 checkpoint 到 Supabase（Phase 1 先写日志，Phase 2 写 DB）。
    """

    name = "orchestrator"

    def __init__(
        self,
        *,
        # Screener config
        webhook_url: str = "",
        notify: bool = True,
        # Analyst config
        api_key: str = "",
        model: str = "",
        provider: str = "gemini",
        llm_base_url: str = "",
        wecom_webhook: str = "",
        dingtalk_webhook: str = "",
        # Strategist config
        portfolio_id: str = "",
        tg_bot_token: str = "",
        tg_chat_id: str = "",
        # Global
        skip_step4: bool = False,
        max_retries: int = 2,
    ):
        self.max_retries = max_retries
        self.skip_step4 = skip_step4

        self.screener = ScreenerAgent(
            webhook_url=webhook_url,
            notify=notify,
        )
        self.market_context = MarketContextAgent()
        self.analyst = WyckoffAnalystAgent(
            webhook_url=webhook_url,
            api_key=api_key,
            model=model,
            provider=provider,
            llm_base_url=llm_base_url,
            wecom_webhook=wecom_webhook,
            dingtalk_webhook=dingtalk_webhook,
            notify=notify,
        )
        self.strategist = StrategyAgent(
            api_key=api_key,
            model=model,
            portfolio_id=portfolio_id,
            tg_bot_token=tg_bot_token,
            tg_chat_id=tg_chat_id,
        )
        self.notifier = NotifierAgent(
            feishu_webhook=webhook_url,
            wecom_webhook=wecom_webhook,
            dingtalk_webhook=dingtalk_webhook,
            tg_bot_token=tg_bot_token,
            tg_chat_id=tg_chat_id,
        )

    @classmethod
    def from_env(cls) -> "OrchestratorAgent":
        """从环境变量构建 OrchestratorAgent（用于 CLI 和 GH Actions）。"""
        from integrations.llm_client import DEFAULT_GEMINI_MODEL, OPENAI_COMPATIBLE_BASE_URLS

        provider = os.getenv("DEFAULT_LLM_PROVIDER", "gemini").strip().lower() or "gemini"
        api_key = (
            os.getenv(f"{provider.upper()}_API_KEY")
            or os.getenv("GEMINI_API_KEY")
            or ""
        ).strip()
        model = (
            os.getenv(f"{provider.upper()}_MODEL")
            or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        ).strip() or DEFAULT_GEMINI_MODEL
        llm_base_url = (
            os.getenv(f"{provider.upper()}_BASE_URL")
            or OPENAI_COMPATIBLE_BASE_URLS.get(provider, "")
            or ""
        ).strip()

        skip_step4 = os.getenv("DAILY_JOB_SKIP_STEP4", "").strip().lower() in {
            "1", "true", "yes", "on",
        }

        # Portfolio target
        user_id = os.getenv("SUPABASE_USER_ID", "").strip()
        portfolio_id = f"USER_LIVE:{user_id}" if user_id else ""

        return cls(
            webhook_url=os.getenv("FEISHU_WEBHOOK_URL", "").strip(),
            api_key=api_key,
            model=model,
            provider=provider,
            llm_base_url=llm_base_url,
            wecom_webhook=os.getenv("WECOM_WEBHOOK_URL", "").strip(),
            dingtalk_webhook=os.getenv("DINGTALK_WEBHOOK_URL", "").strip(),
            portfolio_id=portfolio_id,
            tg_bot_token=os.getenv("TG_BOT_TOKEN", "").strip(),
            tg_chat_id=os.getenv("TG_CHAT_ID", "").strip(),
            skip_step4=skip_step4,
        )

    def run(self, trigger: dict | None = None) -> AgentResult:
        """
        执行完整 pipeline。

        Args:
            trigger: 触发信息 dict，如 {"trigger": "cron", "run_id": "..."}

        Returns:
            AgentResult wrapping pipeline 执行结果。
        """
        trigger = trigger or {}
        run_id = trigger.get("run_id", f"run_{datetime.now(TZ):%Y%m%d_%H%M%S}")
        trigger_type = trigger.get("trigger", "manual")

        logger.info("[%s] Pipeline started (trigger=%s)", run_id, trigger_type)
        pipeline_t0 = time.monotonic()

        ctx: dict = {"_run_id": run_id, "_trigger": trigger_type}
        stages_executed: list[dict] = []

        # ------------------------------------------------------------------
        # Stage 1: Screener (同时产出 benchmark_context)
        # ------------------------------------------------------------------
        result = self._run_stage(self.screener, ctx)
        stages_executed.append(result.to_checkpoint_dict())
        if not result.ok:
            self.notifier.send_failure(result)
            return self._make_pipeline_result(
                run_id, PipelineStatus.FAILED, stages_executed, pipeline_t0,
            )
        ctx["screener"] = result

        # ------------------------------------------------------------------
        # Stage 2: MarketContext (从 screener 产出的 benchmark_context 转换)
        # ------------------------------------------------------------------
        result = self._run_stage(self.market_context, ctx)
        stages_executed.append(result.to_checkpoint_dict())
        if not result.ok:
            # MarketContext 失败不致命，后续 agent 可容忍
            logger.warning("MarketContextAgent failed, continuing without regime info")
        ctx["market_context"] = result

        # ------------------------------------------------------------------
        # Stage 3: Analyst (LLM 三阵营研报)
        # ------------------------------------------------------------------
        screen = ctx.get("screener")
        if screen and screen.ok and screen.payload and screen.payload.candidates:
            result = self._run_stage(self.analyst, ctx)
            stages_executed.append(result.to_checkpoint_dict())
            if not result.ok:
                logger.warning("WyckoffAnalystAgent failed: %s", result.error)
                # 不致命：Funnel 结果已推送，研报失败只影响 step4
            ctx["analyst"] = result
        else:
            logger.info("No candidates, skipping analyst")
            ctx["analyst"] = AgentResult(
                agent_name="analyst",
                status=PipelineStatus.COMPLETED,
                payload=None,
            )

        # ------------------------------------------------------------------
        # Stage 4: Strategist (LLM OMS 决策)
        # ------------------------------------------------------------------
        if not self.skip_step4:
            result = self._run_stage(self.strategist, ctx)
            stages_executed.append(result.to_checkpoint_dict())
            ctx["strategist"] = result
        else:
            logger.info("Step4 skipped (DAILY_JOB_SKIP_STEP4=1)")
            ctx["strategist"] = AgentResult(
                agent_name="strategist",
                status=PipelineStatus.COMPLETED,
                payload=None,
            )

        # ------------------------------------------------------------------
        # Stage 5: Notifier (汇总)
        # ------------------------------------------------------------------
        result = self._run_stage(self.notifier, ctx)
        stages_executed.append(result.to_checkpoint_dict())

        # ------------------------------------------------------------------
        # Pipeline 完成
        # ------------------------------------------------------------------
        all_ok = all(
            s.get("status") == PipelineStatus.COMPLETED.value
            for s in stages_executed
        )
        status = PipelineStatus.COMPLETED if all_ok else PipelineStatus.PARTIAL

        pipeline_result = self._make_pipeline_result(
            run_id, status, stages_executed, pipeline_t0,
        )
        logger.info(
            "[%s] Pipeline finished: status=%s elapsed=%dms",
            run_id, status.value, pipeline_result.duration_ms,
        )
        return pipeline_result

    def _run_stage(self, agent, ctx: dict) -> AgentResult:
        """带重试的 stage 执行。"""
        last_result = None
        for attempt in range(1, self.max_retries + 1):
            try:
                result = agent.run(ctx)
                result.retries = attempt - 1
                if result.ok:
                    return result
                last_result = result
                if attempt < self.max_retries:
                    logger.warning(
                        "%s failed (attempt %d/%d): %s, retrying...",
                        agent.name, attempt, self.max_retries, result.error,
                    )
            except Exception as e:
                last_result = AgentResult(
                    agent_name=agent.name,
                    status=PipelineStatus.FAILED,
                    error=str(e),
                    retries=attempt - 1,
                )
                if attempt < self.max_retries:
                    logger.warning(
                        "%s exception (attempt %d/%d): %s, retrying...",
                        agent.name, attempt, self.max_retries, e,
                    )
        return last_result or AgentResult(
            agent_name=agent.name,
            status=PipelineStatus.FAILED,
            error="max retries exceeded",
        )

    def _make_pipeline_result(
        self,
        run_id: str,
        status: PipelineStatus,
        stages: list[dict],
        t0: float,
    ) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            status=status,
            payload={
                "run_id": run_id,
                "stages": stages,
            },
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
