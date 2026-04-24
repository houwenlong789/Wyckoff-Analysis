# -*- coding: utf-8 -*-
from __future__ import annotations

from cli.loop_guard import resolve_turn_expectation
from tests.helpers.agent_loop_harness import AgentLoopHarness


def test_resolve_turn_expectation_uses_portfolio_context_for_followup_checkup():
    messages = [
        {"role": "user", "content": "我的持仓有什么"},
        {
            "role": "assistant",
            "content": "你手里现在有 4 张牌，外加 1.16 万现金。\n\n| 代码 | 名称 | 持股 | 成本价 | 买入日 |",
        },
        {"role": "user", "content": "做一下体检"},
    ]

    expectation = resolve_turn_expectation(messages)

    assert expectation is not None
    assert expectation.required_tool == "diagnose_portfolio"


def test_agent_loop_retries_planning_only_portfolio_turn_until_tool_executes():
    def second_round(messages, tools, system_prompt):
        assert messages[-1]["role"] == "user"
        assert "diagnose_portfolio" in messages[-1]["content"]
        assert "不要重复计划" in messages[-1]["content"]
        return [
            {
                "type": "tool_calls",
                "tool_calls": [{"id": "tc_diag", "name": "diagnose_portfolio", "args": {}}],
                "text": "",
            },
            {"type": "usage", "input_tokens": 30, "output_tokens": 5},
        ]

    def third_round(messages, tools, system_prompt):
        assert any(m.get("role") == "tool" and m.get("name") == "diagnose_portfolio" for m in messages)
        return [
            {"type": "text_delta", "text": "持仓体检已完成：金螳螂偏弱，苏州银行可继续观察。"},
            {"type": "usage", "input_tokens": 40, "output_tokens": 18},
        ]

    harness = AgentLoopHarness(
        rounds=[
            [
                {"type": "text_delta", "text": "计划\n1. 逐只体检你的 4 只持仓\n2. 汇总去留建议\n现在开第一刀。"},
                {"type": "usage", "input_tokens": 20, "output_tokens": 12},
            ],
            second_round,
            third_round,
        ],
        tool_results={
            "diagnose_portfolio": {
                "message": "mock portfolio diagnosis",
                "positions": [{"code": "002081", "health": "WEAK"}],
            }
        },
    )

    outcome = harness.run_turn(
        [
            {"role": "user", "content": "我的持仓有什么"},
            {"role": "assistant", "content": "你手里现在有 4 张牌，外加 1.16 万现金。"},
            {"role": "user", "content": "做一下体检"},
        ]
    )

    assert outcome["result"]["text"] == "持仓体检已完成：金螳螂偏弱，苏州银行可继续观察。"
    assert [call["name"] for call in outcome["tool_calls"]] == ["diagnose_portfolio"]
    assert len(outcome["provider_calls"]) == 3
    assert outcome["messages"][-1]["role"] == "assistant"
    assert "持仓体检已完成" in outcome["messages"][-1]["content"]


def test_agent_loop_retries_hallucinated_portfolio_list_until_get_portfolio_runs():
    def second_round(messages, tools, system_prompt):
        assert messages[-1]["role"] == "user"
        assert "get_portfolio" in messages[-1]["content"]
        return [
            {
                "type": "tool_calls",
                "tool_calls": [{"id": "tc_pf", "name": "get_portfolio", "args": {}}],
                "text": "",
            },
            {"type": "usage", "input_tokens": 18, "output_tokens": 4},
        ]

    harness = AgentLoopHarness(
        rounds=[
            [
                {"type": "text_delta", "text": "你大概有几只股票和一些现金，我先给你总结一下。"},
                {"type": "usage", "input_tokens": 15, "output_tokens": 11},
            ],
            second_round,
            [
                {"type": "text_delta", "text": "你当前有 4 只持仓，现金 1.16 万。"},
                {"type": "usage", "input_tokens": 24, "output_tokens": 9},
            ],
        ],
        tool_results={"get_portfolio": {"positions": [1, 2, 3, 4], "free_cash": 11600}},
    )

    outcome = harness.run_turn([{"role": "user", "content": "我的持仓有什么"}])

    assert outcome["result"]["text"] == "你当前有 4 只持仓，现金 1.16 万。"
    assert [call["name"] for call in outcome["tool_calls"]] == ["get_portfolio"]


def test_agent_loop_does_not_retry_non_mandatory_plain_text_turn():
    harness = AgentLoopHarness(
        rounds=[
            [
                {"type": "text_delta", "text": "威科夫核心是供需与主力行为。"},
                {"type": "usage", "input_tokens": 8, "output_tokens": 7},
            ]
        ]
    )

    outcome = harness.run_turn([{"role": "user", "content": "简单讲讲威科夫方法"}])

    assert outcome["result"]["text"] == "威科夫核心是供需与主力行为。"
    assert outcome["tool_calls"] == []
    assert len(outcome["provider_calls"]) == 1


def test_agent_loop_warns_after_retry_budget_is_exhausted():
    harness = AgentLoopHarness(
        rounds=[
            [
                {"type": "text_delta", "text": "计划\n1. 先体检\n2. 再总结"},
                {"type": "usage", "input_tokens": 10, "output_tokens": 6},
            ],
            [
                {"type": "text_delta", "text": "我先给你说说思路。"},
                {"type": "usage", "input_tokens": 12, "output_tokens": 5},
            ],
            [
                {"type": "text_delta", "text": "还是先说计划，不着急执行。"},
                {"type": "usage", "input_tokens": 14, "output_tokens": 5},
            ],
        ]
    )

    outcome = harness.run_turn(
        [
            {"role": "user", "content": "我的持仓有什么"},
            {"role": "assistant", "content": "你手里现在有 4 张牌。"},
            {"role": "user", "content": "做一下体检"},
        ]
    )

    assert "连续 2 次没有调用必需工具" in outcome["result"]["text"]
    assert outcome["tool_calls"] == []
    assert len(outcome["provider_calls"]) == 3
