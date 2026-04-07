# -*- coding: utf-8 -*-
"""
Agent Pipeline CLI 入口 — 替代 daily_job.py 的新执行入口。

用法:
  python -m scripts.run_pipeline --trigger cron
  python -m scripts.run_pipeline --trigger manual --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Ensure project root is on sys.path
if __name__ == "__main__" or not __package__:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TZ = ZoneInfo("Asia/Shanghai")


def _setup_logging(logs_path: str | None = None) -> None:
    """配置日志：同时输出到 console 和文件。"""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if logs_path:
        os.makedirs(os.path.dirname(logs_path) or ".", exist_ok=True)
        handlers.append(logging.FileHandler(logs_path, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )


def _validate_env() -> list[str]:
    """Secret 完整性预检，返回缺失项列表。"""
    missing = []
    if not os.getenv("FEISHU_WEBHOOK_URL", "").strip():
        missing.append("FEISHU_WEBHOOK_URL")

    provider = os.getenv("DEFAULT_LLM_PROVIDER", "gemini").strip().lower() or "gemini"
    skip_llm = os.getenv("STEP3_SKIP_LLM", "").strip().lower() in {"1", "true", "yes", "on"}
    skip_step4 = os.getenv("DAILY_JOB_SKIP_STEP4", "").strip().lower() in {"1", "true", "yes", "on"}

    if (not skip_llm or not skip_step4):
        api_key = (
            os.getenv(f"{provider.upper()}_API_KEY")
            or os.getenv("GEMINI_API_KEY")
            or ""
        ).strip()
        if not api_key:
            missing.append(f"{provider.upper()}_API_KEY or GEMINI_API_KEY")

    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Wyckoff Agent Pipeline")
    parser.add_argument(
        "--trigger",
        choices=["cron", "web", "manual"],
        default="manual",
        help="触发来源 (default: manual)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅校验配置，不执行 pipeline",
    )
    parser.add_argument(
        "--logs",
        default=None,
        help="日志文件路径",
    )
    args = parser.parse_args()

    logs_path = args.logs or os.path.join(
        os.getenv("LOGS_DIR", "logs"),
        f"pipeline_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.log",
    )
    _setup_logging(logs_path)

    logger = logging.getLogger("run_pipeline")
    logger.info("Wyckoff Agent Pipeline starting (trigger=%s)", args.trigger)

    # 配置校验
    missing = _validate_env()
    if missing:
        logger.error("Missing config: %s", ", ".join(missing))
        return 1

    if args.dry_run:
        logger.info("--dry-run: config validation passed, exiting")
        return 0

    # 构建 Orchestrator 并执行
    from agents.orchestrator import OrchestratorAgent

    orchestrator = OrchestratorAgent.from_env()
    run_id = f"{args.trigger}_{datetime.now(TZ):%Y%m%d_%H%M%S}"

    result = orchestrator.run(trigger={
        "run_id": run_id,
        "trigger": args.trigger,
    })

    if result.ok:
        logger.info("Pipeline completed successfully (run_id=%s)", run_id)
        return 0
    else:
        logger.error(
            "Pipeline finished with status=%s error=%s (run_id=%s)",
            result.status.value, result.error, run_id,
        )
        # PARTIAL 不算失败（Funnel 成功但研报失败的情况）
        return 0 if result.status.value == "partial" else 1


if __name__ == "__main__":
    sys.exit(main())
