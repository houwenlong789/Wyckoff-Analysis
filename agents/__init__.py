# -*- coding: utf-8 -*-
"""
Wyckoff Agent 层 — 基于 OpenAI Agents SDK + LiteLLM 的多 Agent 编排。

Phase 1: 每个 Agent 是对现有 pipeline 函数的薄包装。
Phase 2: Agent 内部拆解为细粒度 Tool 调用。
Phase 3: 结构化 LLM 输出 + 交互式单 Agent 调用。
"""
