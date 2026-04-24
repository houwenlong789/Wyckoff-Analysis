# -*- coding: utf-8 -*-
"""Shared guardrails for incomplete agent turns."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class TurnExpectation:
    """A tool call the loop considers mandatory for the current turn."""

    required_tool: str
    reason: str


_PORTFOLIO_VIEW_PHRASES = (
    "我有什么持仓",
    "我的持仓有什么",
    "持仓列表",
    "我买了啥",
    "我买了什么",
    "持仓情况",
    "仓位情况",
)

_PORTFOLIO_DIAGNOSE_PHRASES = (
    "我持仓怎么样",
    "帮我看看持仓",
    "帮我看下持仓",
    "持仓健康吗",
    "持仓体检",
    "体检一下持仓",
    "帮我审一下持仓",
    "审判我的持仓",
    "审一下持仓",
)

_GENERIC_DIAGNOSE_HINTS = (
    "做一下体检",
    "做个体检",
    "体检一下",
    "体检",
    "审判",
    "健康吗",
    "健康",
    "诊断",
    "审一下",
)

_PORTFOLIO_CONTEXT_MARKERS = (
    "持仓",
    "仓位",
    "持股",
    "成本价",
    "买入日",
    "代码 | 名称 | 持股",
    "总可用",
    "现金",
    "portfolio",
)

_TOOL_CN_NAMES = {
    "diagnose_portfolio": "持仓审判",
    "get_portfolio": "查看持仓",
}


def _normalize_text(text: str) -> str:
    return str(text or "").strip().lower()


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    return str(content) if isinstance(content, str) else ""


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            text = _message_text(message)
            if text:
                return _normalize_text(text)
    return ""


def _recent_context_text(messages: list[dict[str, Any]], *, limit: int = 4) -> str:
    pieces: list[str] = []
    for message in messages[-limit:]:
        text = _message_text(message)
        if text:
            pieces.append(_normalize_text(text))
    return "\n".join(pieces)


def resolve_turn_expectation(messages: list[dict[str, Any]]) -> TurnExpectation | None:
    """Infer whether this turn must call a specific tool before answering."""

    if not messages:
        return None

    last_user = _last_user_text(messages)
    if not last_user:
        return None

    if any(phrase in last_user for phrase in _PORTFOLIO_VIEW_PHRASES):
        return TurnExpectation(
            required_tool="get_portfolio",
            reason="持仓列表查询必须先拉真实持仓数据。",
        )

    if any(phrase in last_user for phrase in _PORTFOLIO_DIAGNOSE_PHRASES):
        return TurnExpectation(
            required_tool="diagnose_portfolio",
            reason="持仓体检必须先调用持仓诊断工具。",
        )

    previous_context = _recent_context_text(messages[:-1], limit=4)
    if (
        any(hint in last_user for hint in _GENERIC_DIAGNOSE_HINTS)
        and any(marker in previous_context for marker in _PORTFOLIO_CONTEXT_MARKERS)
    ):
        return TurnExpectation(
            required_tool="diagnose_portfolio",
            reason="上一轮上下文已经明确在讨论持仓，这一轮体检需要继续做持仓诊断。",
        )

    return None


def missing_required_tool(expectation: TurnExpectation | None, used_tools: Iterable[str]) -> bool:
    if expectation is None:
        return False
    return expectation.required_tool not in set(used_tools)


def build_retry_user_message(expectation: TurnExpectation, assistant_text: str = "") -> str:
    """Synthetic follow-up injected when the model skipped a mandatory tool."""

    tool_name = _TOOL_CN_NAMES.get(expectation.required_tool, expectation.required_tool)
    body = str(assistant_text or "").strip()
    if body:
        if _looks_like_plan_only(body):
            lead = "你刚才只给了计划，还没有真正执行。"
        else:
            lead = "你刚才直接给了文本回答，但没有先拿真实数据。"
    else:
        lead = "这一轮没有返回有效工具调用。"
    return (
        f"{lead}{expectation.reason}"
        f" 现在必须先调用 `{expectation.required_tool}`（{tool_name}）拿到真实数据，"
        "再继续回答。不要重复计划，直接执行第一步。"
    )


def build_retry_exhausted_warning(expectation: TurnExpectation, retries: int) -> str:
    tool_name = _TOOL_CN_NAMES.get(expectation.required_tool, expectation.required_tool)
    return (
        f"⚠ 模型连续 {retries} 次没有调用必需工具 `{expectation.required_tool}`"
        f"（{tool_name}），以下回答可能不可靠。"
    )


def _looks_like_plan_only(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if "计划" in normalized:
        return True
    markers = (
        "第一步",
        "第二步",
        "第三步",
        "先",
        "再",
        "然后",
        "接着",
        "现在开第一刀",
    )
    numbered = any(token in normalized for token in ("1.", "1、", "2.", "2、", "3.", "3、"))
    return numbered and any(marker in normalized for marker in markers)
