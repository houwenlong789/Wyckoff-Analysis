"""Calculate weekly stepped performance fees for US-stock recommendation service."""

from __future__ import annotations

import argparse
from math import inf
from typing import NamedTuple

US_STOCK_TIERS = (
    (0.00, 0.10, 0.01),
    (0.10, 0.30, 0.08),
    (0.30, 0.60, 0.12),
    (0.60, 1.00, 0.15),
    (1.00, 2.00, 0.18),
    (2.00, inf, 0.22),
)


class FeeResult(NamedTuple):
    actual_return: float
    profit: float
    fee: float
    effective_rate: float
    capped: bool
    next_high_watermark: float


def calculate_performance_fee(
    start_equity: float,
    end_equity: float,
    *,
    high_watermark: float | None = None,
    cash_in: float = 0.0,
    cash_out: float = 0.0,
    profit_fee_cap: float | None = 0.20,
) -> FeeResult:
    base = max(start_equity, high_watermark or start_equity)
    if base <= 0:
        raise ValueError("start_equity/high_watermark must be positive")

    adjusted_end = end_equity + cash_out - cash_in
    next_high_watermark = max(base, adjusted_end)
    profit = adjusted_end - base
    if profit <= 0:
        return FeeResult(profit / base, profit, 0.0, 0.0, False, next_high_watermark)

    actual_return = profit / base
    raw_fee = sum(
        base * (min(actual_return, upper) - lower) * rate
        for lower, upper, rate in US_STOCK_TIERS
        if actual_return > lower
    )
    cap = profit * profit_fee_cap if profit_fee_cap is not None else inf
    fee = min(raw_fee, cap)
    return FeeResult(actual_return, profit, fee, fee / profit, fee < raw_fee, next_high_watermark)


def evenly_spaced_returns(start: float, end: float, count: int) -> list[float]:
    if count < 2:
        raise ValueError("count must be at least 2")
    step = (end - start) / (count - 1)
    return [start + step * idx for idx in range(count)]


def format_percent(value: float, digits: int = 2) -> str:
    return f"{value * 100:.{digits}f}%"


def run_self_test() -> None:
    cases = [
        (0.10, 0.01),
        (0.30, 0.017 / 0.30),
        (1.00, 0.113 / 1.00),
        (2.00, 0.293 / 2.00),
        (20.00, 0.20),
    ]
    for actual_return, expected_rate in cases:
        result = calculate_performance_fee(1.0, 1.0 + actual_return)
        assert abs(result.effective_rate - expected_rate) < 1e-9
    assert calculate_performance_fee(1.0, 0.8).fee == 0.0
    assert calculate_performance_fee(1.0, 1.5, high_watermark=2.0).fee == 0.0
    assert calculate_performance_fee(1.0, 1.5).next_high_watermark == 1.5
    assert calculate_performance_fee(1.4, 1.45, high_watermark=1.5).next_high_watermark == 1.5


def print_cases(start: float, end: float, count: int, principal: float, cap: float | None) -> None:
    print("| case | weekly return | profit above HWM | fee | fee/profit | capped |")
    print("|---:|---:|---:|---:|---:|:---:|")
    for idx, actual_return in enumerate(evenly_spaced_returns(start, end, count), start=1):
        result = calculate_performance_fee(principal, principal * (1 + actual_return), profit_fee_cap=cap)
        capped = "yes" if result.capped else "no"
        print(
            f"| {idx} | {format_percent(result.actual_return)} | "
            f"{result.profit:,.2f} | {result.fee:,.2f} | "
            f"{format_percent(result.effective_rate)} | {capped} |"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print weekly US-stock stepped performance fee cases.")
    parser.add_argument(
        "--start-return", type=float, default=0.10, help="Start weekly return as decimal, default 0.10."
    )
    parser.add_argument("--end-return", type=float, default=20.00, help="End weekly return as decimal, default 20.00.")
    parser.add_argument("--count", type=int, default=50, help="Number of cases, default 50.")
    parser.add_argument("--principal", type=float, default=1_000_000.0, help="Principal amount, default 1,000,000.")
    parser.add_argument("--no-cap", action="store_true", help="Disable 20%% of profit cap.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_self_test()
    cap = None if args.no_cap else 0.20
    print_cases(args.start_return, args.end_return, args.count, args.principal, cap)


if __name__ == "__main__":
    main()
