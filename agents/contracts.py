# -*- coding: utf-8 -*-
"""
Agent 间数据契约 — 所有 Agent 的输入输出类型定义。

每个 dataclass 提供 from_legacy_dict() / to_legacy_dict() 实现
新旧格式互转，Phase 1 期间保持与现有 pipeline 的完全兼容。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Regime(str, Enum):
    """大盘水温 regime。"""
    RISK_ON = "RISK_ON"
    NEUTRAL = "NEUTRAL"
    RISK_OFF = "RISK_OFF"
    CRASH = "CRASH"
    PANIC_REPAIR = "PANIC_REPAIR"
    BLACK_SWAN = "BLACK_SWAN"

    @classmethod
    def from_str(cls, s: str) -> "Regime":
        s = (s or "").strip().upper()
        try:
            return cls(s)
        except ValueError:
            return cls.NEUTRAL


class PipelineStatus(str, Enum):
    """Pipeline / Agent 执行状态。"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


# ---------------------------------------------------------------------------
# MarketContext — MarketContextAgent 的输出
# ---------------------------------------------------------------------------

@dataclass
class MarketContext:
    """大盘环境上下文。"""
    date: str                                       # YYYY-MM-DD
    regime: Regime = Regime.NEUTRAL
    benchmark_metrics: dict = field(default_factory=dict)  # close, ma50, ma200, 3d ...
    sector_rotation: dict = field(default_factory=dict)
    breadth: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)           # 完整 benchmark_context 原始 dict

    @classmethod
    def from_legacy_dict(cls, d: dict) -> "MarketContext":
        """从现有 benchmark_context dict 构造。"""
        from utils.trading_clock import resolve_end_calendar_day
        date = d.get("date") or resolve_end_calendar_day().isoformat()
        return cls(
            date=str(date),
            regime=Regime.from_str(str(d.get("regime", "NEUTRAL"))),
            benchmark_metrics={
                k: d.get(k) for k in (
                    "close", "ma50", "ma200",
                    "recent3_cum_pct", "main_today_pct",
                    "main_code", "smallcap_code",
                    "smallcap_close", "smallcap_recent3_cum_pct",
                )
            },
            sector_rotation=d.get("sector_rotation") or {},
            breadth=d.get("breadth") or {},
            raw=dict(d),
        )

    def to_legacy_dict(self) -> dict:
        """还原为现有 benchmark_context dict 格式。"""
        out = dict(self.raw) if self.raw else {}
        out["regime"] = self.regime.value
        out["date"] = self.date
        out.update(self.benchmark_metrics)
        if self.sector_rotation:
            out["sector_rotation"] = self.sector_rotation
        if self.breadth:
            out["breadth"] = self.breadth
        return out


# ---------------------------------------------------------------------------
# StockCandidate — 单只候选股信息
# ---------------------------------------------------------------------------

_CANDIDATE_FIELDS = (
    "code", "name", "tag", "track", "stage", "score", "priority_score",
    "industry", "sector_state", "exit_signal",
)


@dataclass
class StockCandidate:
    """漏斗筛出的候选股。"""
    code: str = ""
    name: str = ""
    tag: str = ""
    track: str = ""                      # Trend | Accum | ""
    stage: str = ""                      # Markup | Accum_A | Accum_B | Accum_C | ""
    score: float = 0.0
    priority_score: float = 0.0
    industry: str = ""
    sector_state: str = ""
    exit_signal: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_legacy_dict(cls, d: dict) -> "StockCandidate":
        kwargs = {k: d.get(k, "") for k in _CANDIDATE_FIELDS}
        kwargs["score"] = float(kwargs.get("score") or 0)
        kwargs["priority_score"] = float(kwargs.get("priority_score") or 0)
        kwargs["raw"] = dict(d)
        return cls(**kwargs)

    def to_legacy_dict(self) -> dict:
        if self.raw:
            return dict(self.raw)
        return {k: getattr(self, k) for k in _CANDIDATE_FIELDS}


# ---------------------------------------------------------------------------
# ScreenResult — ScreenerAgent 的输出
# ---------------------------------------------------------------------------

@dataclass
class ScreenResult:
    """漏斗筛选结果。"""
    candidates: list[StockCandidate] = field(default_factory=list)
    total_scanned: int = 0
    funnel_stats: dict = field(default_factory=dict)

    @classmethod
    def from_legacy(
        cls,
        symbols_info: list[dict],
        total_scanned: int = 0,
        funnel_stats: dict | None = None,
    ) -> "ScreenResult":
        return cls(
            candidates=[StockCandidate.from_legacy_dict(d) for d in symbols_info],
            total_scanned=total_scanned,
            funnel_stats=funnel_stats or {},
        )

    def to_legacy_symbols_info(self) -> list[dict]:
        return [c.to_legacy_dict() for c in self.candidates]


# ---------------------------------------------------------------------------
# AnalysisReport — WyckoffAnalystAgent 的输出
# ---------------------------------------------------------------------------

@dataclass
class AnalysisReport:
    """AI 三阵营研报结果。"""
    report_text: str = ""                # 完整 Markdown
    springboard_codes: list[str] = field(default_factory=list)
    model_used: str = ""
    token_usage: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# StrategyDecision — StrategyAgent 的输出
# ---------------------------------------------------------------------------

@dataclass
class StrategyDecision:
    """OMS 决策结果。"""
    market_view: str = ""
    decisions: list[dict] = field(default_factory=list)
    model_used: str = ""
    reason: str = "ok"


# ---------------------------------------------------------------------------
# AgentResult — 所有 Agent 的统一输出包装
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    """Agent 执行结果的通用包装。"""
    agent_name: str = ""
    status: PipelineStatus = PipelineStatus.PENDING
    payload: Any = None
    error: str | None = None
    duration_ms: int = 0
    retries: int = 0

    @property
    def ok(self) -> bool:
        return self.status == PipelineStatus.COMPLETED

    def to_checkpoint_dict(self) -> dict:
        """序列化为可存入 Supabase JSON 的 dict。"""
        return {
            "agent_name": self.agent_name,
            "status": self.status.value,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "retries": self.retries,
        }
