#!/usr/bin/env python3
"""持仓分钟级诊断：规则 + LLM，结论推送 Telegram。"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from integrations.llm_client import call_llm, provider_fallbacks, provider_route_chain, resolve_provider_name
from integrations.supabase_portfolio import load_portfolio_state
from integrations.tickflow_client import TickFlowClient
from scripts.tail_buy_intraday_job import (
    _analyze_holdings_actions,
    _build_holdings_markdown,
)
from utils.feishu import send_feishu_notification
from utils.notify import send_to_telegram

TZ = ZoneInfo("Asia/Shanghai")

HOLDING_ACTIONS = ("ADD", "HOLD", "TRIM", "EXIT")

SYSTEM_PROMPT = (
    "你是A股持仓诊断助手。根据持仓分钟级特征和规则一判结果，"
    "给出最终操作结论。你只能在 ADD/HOLD/TRIM/EXIT 中选择一个，必须返回 JSON。\n"
    "ADD=加仓, HOLD=不动, TRIM=减仓, EXIT=清仓。\n"
    "禁止输出投资建议免责声明，禁止输出 markdown。"
)


@dataclass
class HoldingLLMResult:
    code: str
    name: str
    rule_action: str
    llm_action: str = ""
    llm_reason: str = ""
    llm_confidence: float | None = None
    error: str = ""


def _build_holding_llm_prompt(advice: Any, free_cash: float, total_equity: float) -> str:
    f = advice.features or {}
    cash_pct = (free_cash / total_equity * 100) if total_equity > 0 else 0
    return (
        f"股票: {advice.code} {advice.name}\n"
        f"持仓: {advice.shares}股, 成本={advice.cost:.2f}, 现价={advice.current_price:.2f}, 浮盈={advice.pnl_pct:+.1f}%\n"
        f"账户: 可用现金={free_cash:.0f} ({cash_pct:.1f}%), 总权益={total_equity:.0f}\n"
        f"规则一判: action={advice.action}, rule_score={advice.rule_score:.1f}\n"
        f"规则理由: {'；'.join(advice.reasons[:3])}\n"
        f"分时特征:\n"
        f"- close_pos={_sf(f.get('close_pos')):.3f}\n"
        f"- dist_vwap_pct={_sf(f.get('dist_vwap_pct')):.3f}\n"
        f"- last30_ret_pct={_sf(f.get('last30_ret_pct')):.3f}\n"
        f"- day_ret_pct={_sf(f.get('day_ret_pct')):.3f}\n"
        f"- tail30_volume_share={_sf(f.get('tail30_volume_share')):.3f}\n"
        f"- drop_from_high_pct={_sf(f.get('drop_from_high_pct')):.3f}\n"
        '\n请输出严格 JSON：{"action":"ADD|HOLD|TRIM|EXIT","reason":"<=80字","confidence":0.0}'
    )


def _sf(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _parse_holding_llm(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    parsed = None
    try:
        parsed = json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                pass
    if not isinstance(parsed, dict):
        return None
    action = str(parsed.get("action", "")).strip().upper()
    if action not in HOLDING_ACTIONS:
        return None
    reason = str(parsed.get("reason", "")).strip()
    conf = parsed.get("confidence")
    try:
        conf = max(0.0, min(1.0, float(conf)))
    except Exception:
        conf = None
    return {"action": action, "reason": reason, "confidence": conf}


def _run_holdings_llm(
    holdings: list[Any],
    free_cash: float,
    total_equity: float,
    llm_routes: list[dict[str, str]],
    deadline_at: datetime,
) -> list[HoldingLLMResult]:
    results: list[HoldingLLMResult] = []
    if not llm_routes:
        for h in holdings:
            results.append(HoldingLLMResult(code=h.code, name=h.name, rule_action=h.action, error="no_llm_routes"))
        return results

    def _judge(h: Any) -> HoldingLLMResult:
        prompt = _build_holding_llm_prompt(h, free_cash, total_equity)
        for route in llm_routes:
            left = (deadline_at - datetime.now(TZ)).total_seconds()
            if left <= 5:
                return HoldingLLMResult(code=h.code, name=h.name, rule_action=h.action, error="deadline")
            try:
                text = call_llm(
                    provider=route["provider"],
                    model=route["model"],
                    api_key=route["api_key"],
                    system_prompt=SYSTEM_PROMPT,
                    user_message=prompt,
                    base_url=route.get("base_url") or None,
                    timeout=min(30, max(10, int(left - 3))),
                    max_output_tokens=256,
                    allow_truncated_text=True,
                )
                parsed = _parse_holding_llm(text)
                if parsed:
                    return HoldingLLMResult(
                        code=h.code,
                        name=h.name,
                        rule_action=h.action,
                        llm_action=parsed["action"],
                        llm_reason=parsed["reason"],
                        llm_confidence=parsed["confidence"],
                    )
            except Exception:
                continue
        return HoldingLLMResult(code=h.code, name=h.name, rule_action=h.action, error="all_routes_failed")

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(_judge, holdings))
    return results


def _build_report(
    llm_results: list[HoldingLLMResult],
    holdings: list[Any],
    free_cash: float,
    total_equity: float,
    rule_section: str,
    elapsed: float,
) -> str:
    cash_pct = (free_cash / total_equity * 100) if total_equity > 0 else 0
    lines = [
        f"📊 持仓诊断 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}",
        "",
        f"- 持仓数: {len(holdings)}",
        f"- 可用现金: {free_cash:.0f} ({cash_pct:.1f}%)",
        f"- 总权益: {total_equity:.0f}",
        f"- 耗时: {elapsed:.1f}s",
        "",
    ]

    action_map: dict[str, list[HoldingLLMResult]] = {"ADD": [], "HOLD": [], "TRIM": [], "EXIT": []}
    for r in llm_results:
        final = r.llm_action or r.rule_action
        action_map.setdefault(final, []).append(r)

    for action, label in [("ADD", "加仓"), ("HOLD", "不动"), ("TRIM", "减仓"), ("EXIT", "清仓")]:
        items = action_map.get(action, [])
        lines.append(f"## {action}（{label}）")
        if not items:
            lines.append("- 无")
        else:
            for r in items:
                reason = r.llm_reason or "(规则判断)"
                conf = f" conf={r.llm_confidence:.0%}" if r.llm_confidence is not None else ""
                rule_tag = f" [规则:{r.rule_action}]" if r.rule_action != (r.llm_action or r.rule_action) else ""
                lines.append(f"- {r.code} {r.name}{rule_tag}{conf} | {reason}")
        lines.append("")

    lines.append("---")
    lines.append(rule_section)
    return "\n".join(lines)


def _build_llm_routes() -> list[dict[str, str]]:
    provider = resolve_provider_name("HOLDING_DIAG_LLM_PROVIDER", "efficiency")
    return provider_route_chain(
        provider,
        provider_fallbacks("HOLDING_DIAG_LLM_FALLBACK_PROVIDERS"),
    )


def _run_llm_and_report(
    holdings: list[Any],
    rule_section: str,
    portfolio_id: str,
    deadline_at: datetime,
    t0: float,
) -> str:
    state = load_portfolio_state(portfolio_id)
    free_cash = float(state.get("free_cash", 0)) if isinstance(state, dict) else 0
    total_equity = float(state.get("total_equity") or 0) if isinstance(state, dict) else 0
    if total_equity <= 0:
        total_equity = free_cash + sum(h.current_price * h.shares for h in holdings)

    llm_routes = _build_llm_routes()
    print(f"[holding-diag] LLM routes: {[r['name'] for r in llm_routes]}")
    llm_results = _run_holdings_llm(holdings, free_cash, total_equity, llm_routes, deadline_at)
    llm_ok = sum(1 for r in llm_results if r.llm_action)
    print(f"[holding-diag] LLM: {llm_ok}/{len(llm_results)} success")

    elapsed = time.time() - t0
    return _build_report(llm_results, holdings, free_cash, total_equity, rule_section, elapsed)


def _send_feishu_report(report: str) -> None:
    webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not webhook:
        print("[holding-diag] FEISHU_WEBHOOK_URL 未配置，跳过飞书发送")
        return
    ok = send_feishu_notification(webhook, "持仓诊断", report)
    print(f"[holding-diag] Feishu: {'ok' if ok else 'failed'}")


def main() -> int:
    t0 = time.time()
    tickflow_api_key = os.getenv("TICKFLOW_API_KEY", "").strip()
    tg_bot_token = os.getenv("TG_BOT_TOKEN", "").strip()
    tg_chat_id = os.getenv("TG_CHAT_ID", "").strip()
    user_id = os.getenv("SUPABASE_USER_ID", "").strip()
    portfolio_id = os.getenv("TAIL_BUY_PORTFOLIO_ID", "").strip() or (
        f"USER_LIVE:{user_id}" if user_id else "USER_LIVE"
    )

    if not tickflow_api_key:
        print("[holding-diag] ERROR: TICKFLOW_API_KEY not set")
        return 1

    deadline_at = datetime.now(TZ) + timedelta(minutes=10)
    tf_client = TickFlowClient(api_key=tickflow_api_key)

    print(f"[holding-diag] portfolio={portfolio_id}")
    holdings, limit_hit, meta = _analyze_holdings_actions(
        tickflow_client=tf_client,
        portfolio_id=portfolio_id,
        signal_map={},
        style="conservative",
        intraday_batch_size=200,
        hard_stop_pct=6.0,
        deadline_at=deadline_at,
        logs_path=None,
    )
    if not holdings:
        print("[holding-diag] no holdings to diagnose")
        return 0

    rule_section = _build_holdings_markdown(holdings=holdings, portfolio_meta=meta, tickflow_limit_hit=limit_hit)
    report = _run_llm_and_report(holdings, rule_section, portfolio_id, deadline_at, t0)
    print(report)
    _send_feishu_report(report)

    if tg_bot_token and tg_chat_id:
        ok = send_to_telegram(f"📊 持仓诊断\n\n{report}", tg_bot_token=tg_bot_token, tg_chat_id=tg_chat_id)
        print(f"[holding-diag] Telegram: {'ok' if ok else 'failed'}")
    else:
        print("[holding-diag] Telegram not configured, skipping push")

    return 0


if __name__ == "__main__":
    sys.exit(main())
