# -*- coding: utf-8 -*-
"""
NotifierAgent — 通知推送（飞书/企微/钉钉/Telegram）。

Phase 1: 大部分通知在 ScreenerAgent / WyckoffAnalystAgent 内部已经通过
         run_funnel() / run_step3() 自带推送。NotifierAgent 在 Phase 1
         主要负责 pipeline 级别的成功/失败汇总通知。
Phase 2: 所有通知统一收归 NotifierAgent。
"""
from __future__ import annotations

import logging
import time

from agents.contracts import AgentResult, PipelineStatus

logger = logging.getLogger(__name__)


class NotifierAgent:
    """
    确定性 Agent：格式化并分发通知。
    """

    name = "notifier"

    def __init__(
        self,
        feishu_webhook: str = "",
        wecom_webhook: str = "",
        dingtalk_webhook: str = "",
        tg_bot_token: str = "",
        tg_chat_id: str = "",
    ):
        self.feishu_webhook = feishu_webhook
        self.wecom_webhook = wecom_webhook
        self.dingtalk_webhook = dingtalk_webhook
        self.tg_bot_token = tg_bot_token
        self.tg_chat_id = tg_chat_id

    def run(self, context: dict) -> AgentResult:
        """
        Phase 1: 发送 pipeline 汇总通知。

        通知内容包括每个 stage 的执行状态和耗时。
        具体的研报推送在 AnalystAgent / ScreenerAgent 内部已完成。
        """
        t0 = time.monotonic()
        try:
            summary_lines = self._build_summary(context)
            logger.info("NotifierAgent summary:\n%s", "\n".join(summary_lines))

            # Phase 1: 仅日志记录，不额外推送
            # （研报推送已在 step2/step3 内部完成）
            # Phase 2 会将所有推送逻辑移到这里

            return AgentResult(
                agent_name=self.name,
                status=PipelineStatus.COMPLETED,
                payload={"summary": summary_lines},
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception as e:
            logger.exception("NotifierAgent failed")
            return AgentResult(
                agent_name=self.name,
                status=PipelineStatus.FAILED,
                error=str(e),
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

    def send_failure(self, failed_result: AgentResult) -> None:
        """发送 pipeline 失败告警（Phase 1 简单实现）。"""
        msg = (
            f"Pipeline failure at [{failed_result.agent_name}]: "
            f"{failed_result.error or 'unknown error'}"
        )
        logger.error(msg)

        if self.feishu_webhook:
            try:
                from utils.feishu import send_feishu_notification
                send_feishu_notification(
                    self.feishu_webhook,
                    "Pipeline Failure Alert",
                    msg,
                )
            except Exception as e:
                logger.warning("Failed to send feishu alert: %s", e)

    def _build_summary(self, context: dict) -> list[str]:
        """从 pipeline context 构建阶段汇总。"""
        lines = ["=== Pipeline Summary ==="]
        stage_names = ["screener", "market_context", "analyst", "strategist"]
        for name in stage_names:
            result: AgentResult | None = context.get(name)
            if result is None:
                lines.append(f"  - {name}: skipped")
            elif result.ok:
                lines.append(f"  \u2705 {name}: {result.duration_ms}ms")
            else:
                lines.append(f"  \u274c {name}: {result.error} ({result.duration_ms}ms)")
        return lines
