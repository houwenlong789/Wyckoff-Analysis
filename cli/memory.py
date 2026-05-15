"""
Agent 跨会话记忆 — 会话摘要提取 + 记忆注入。
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_SESSION_SUMMARY_PROMPT = """请将以下对话提取为 L1 原子记忆（中文，≤300字）：
1. 讨论了哪些股票（代码+结论）
2. 用户的操作意图和决策
3. 重要的市场判断
4. 用户表达的偏好或禁忌（如"不要推荐ST股"、"不追涨"等）
每条记忆一行，前缀标注类型：[股票] / [决策] / [市场] / [偏好]
每条只写一个事实或结论，忽略寒暄和工具调用细节。"""

_LAYER_REFRESH_PROMPT = """请基于以下 L1 原子记忆，生成更高层的长期记忆：
- [画像] 用户稳定偏好/风险边界/工作习惯，最多3条
- [场景] 可复用的交易/复盘场景，最多3条
每条一行，保留股票代码、条件和结论，不要编造。"""

_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_CJK_RE = re.compile(r"[一-鿿]{2,4}")
_STOPWORDS = frozenset(
    list("的了吗呢啊哦呀吧嘛是不在有我你他它这那都也就要会")
    + [
        "可以",
        "一个",
        "什么",
        "怎么",
        "如何",
        "看看",
        "一下",
        "帮我",
        "请问",
        "能否",
        "可否",
        "这个",
        "那个",
        "我的",
        "你的",
        "现在",
    ]
)

_SUMMARY_TYPES = {
    "股票": "stock_opinion",
    "决策": "decision",
    "市场": "market_view",
    "偏好": "preference",
}

_LAYER_TYPES = {
    "画像": ("persona", "L3"),
    "场景": ("scenario", "L2"),
}


def extract_stock_codes(text: str) -> list[str]:
    return list(dict.fromkeys(_CODE_RE.findall(text)))


def _extract_keywords(text: str) -> list[str]:
    text = _CODE_RE.sub("", text)
    segments = _CJK_RE.findall(text)
    # 长片段拆成 2-gram 提升召回率
    bigrams: list[str] = []
    for seg in segments:
        if len(seg) <= 2:
            bigrams.append(seg)
        else:
            for i in range(len(seg) - 1):
                bigrams.append(seg[i : i + 2])
    return [s for s in dict.fromkeys(bigrams) if s not in _STOPWORDS][:5]


def _has_tool_calls(messages: list[dict]) -> bool:
    return any(m.get("tool_calls") for m in messages)


def _parse_prefixed_line(line: str, mapping: dict[str, Any]) -> tuple[Any, str] | None:
    stripped = line.strip().lstrip("-* ").strip()
    match = re.match(r"^\[([^\]]+)\]\s*(.+)$", stripped)
    if not match:
        return None
    key = match.group(1).strip()
    content = match.group(2).strip()
    if not content or key not in mapping:
        return None
    return mapping[key], content


def _summary_memories(summary: str) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for line in summary.strip().splitlines():
        parsed = _parse_prefixed_line(line, _SUMMARY_TYPES)
        if parsed:
            items.append(parsed)
    return items


def _layer_memories(text: str) -> list[tuple[str, str, str]]:
    items: list[tuple[str, str, str]] = []
    for line in text.strip().splitlines():
        parsed = _parse_prefixed_line(line, _LAYER_TYPES)
        if parsed:
            (memory_type, level), content = parsed
            items.append((memory_type, level, content))
    return items


def _source_ref(session_id: str) -> str:
    return f"chat_log:{session_id}" if session_id else ""


def _provider_text(provider: Any, user_text: str, system_prompt: str) -> str:
    chunks = list(provider.chat_stream([{"role": "user", "content": user_text}], [], system_prompt))
    return "".join(c.get("text", "") for c in chunks if c.get("type") == "text_delta")


def _save_summary_memories(summary: str, codes: str, source_ref: str) -> int:
    from integrations.local_db import save_memory

    saved = 0
    for memory_type, content in _summary_memories(summary):
        saved += int(
            bool(
                save_memory(
                    memory_type,
                    content,
                    codes=codes,
                    source_ref=source_ref,
                    metadata={"extractor": "session_summary"},
                )
            )
        )
    return saved


def refresh_memory_layers(provider: Any) -> int:
    from integrations.local_db import get_recent_memories, save_memory

    atoms = [
        m
        for m in get_recent_memories(limit=30)
        if m.get("memory_type") in {"stock_opinion", "decision", "market_view", "preference", "fact"}
    ]
    if len(atoms) < 3:
        return 0
    lines = [f"- #{m.get('id')} [{m.get('memory_type')}] {m.get('content')}" for m in atoms]
    layered = _provider_text(provider, "\n".join(lines), _LAYER_REFRESH_PROMPT)
    saved = 0
    for memory_type, level, content in _layer_memories(layered):
        codes = ",".join(extract_stock_codes(content)[:20])
        saved += int(bool(save_memory(memory_type, content, codes=codes, memory_level=level)))
    return saved


def _memory_line(memory: dict) -> str:
    date_str = str(memory.get("created_at", ""))[:10]
    content = str(memory.get("content", "")).strip()
    if len(content) > 200:
        content = content[:200] + "…"
    source = str(memory.get("source_ref", "")).strip()
    suffix = f" | 源:{source}" if source else ""
    return f"- #{memory.get('id')} [{date_str}] {content}{suffix}"


def save_session_summary(
    messages: list[dict], provider: Any, *, session_id: str = "", skip_layers: bool = False
) -> None:
    if not messages or len(messages) < 4 or not _has_tool_calls(messages):
        return
    try:
        lines = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "tool":
                content = content[:200] + "..." if len(content) > 200 else content
            if content:
                lines.append(f"[{role}] {content}")
        dialog_text = "\n".join(lines[-40:])

        summary = _provider_text(provider, dialog_text, _SESSION_SUMMARY_PROMPT)
        if not summary or len(summary) < 10:
            return

        all_text = " ".join(m.get("content", "") or "" for m in messages)
        codes = extract_stock_codes(all_text)
        codes_str = ",".join(codes[:20])
        if _save_summary_memories(summary, codes_str, _source_ref(session_id)):
            if not skip_layers:
                refresh_memory_layers(provider)
    except Exception:
        logger.debug("save session summary failed", exc_info=True)


def build_memory_context(user_message: str) -> str:
    try:
        from integrations.local_db import (
            get_recent_memories,
            search_memory_hybrid,
        )

        codes = extract_stock_codes(user_message)
        keywords = _extract_keywords(user_message)

        # Hybrid search: FTS5 + 代码 + 关键词 + 时间衰减
        memories = search_memory_hybrid(
            query_text=user_message,
            codes=codes or None,
            keywords=keywords or None,
            limit=8,
            decay_half_life_days=30.0,
        )

        # 高层画像和偏好始终置顶（hybrid search 已包含，但确保完整性）
        personas = get_recent_memories(memory_type="persona", limit=1)
        prefs = get_recent_memories(memory_type="preference", limit=5)

        if not memories and not prefs and not personas:
            return ""

        lines = [""]
        if personas or prefs:
            lines.append("# 用户画像")
            for p in personas:
                content = str(p.get("content", "")).strip()
                if content:
                    lines.append(f"- {content}")
            for p in prefs:
                content = str(p.get("content", "")).strip()
                if content:
                    lines.append(f"- {content}")

        scenarios = [m for m in memories if m.get("memory_type") == "scenario"]
        if scenarios:
            lines.append("# 相关场景")
            lines.extend(_memory_line(m) for m in scenarios[:3])

        if memories:
            lines.append("# 历史原子记忆")
            atom_types = {"stock_opinion", "decision", "market_view", "fact", "session"}
            lines.extend(_memory_line(m) for m in memories if m.get("memory_type") in atom_types)
        return "\n".join(lines)
    except Exception:
        return ""
