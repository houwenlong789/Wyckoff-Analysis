"""Daily price-action footprint features for signal review."""

from __future__ import annotations

from typing import Any

import pandas as pd


def _numeric_frame(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = (df.sort_values("date") if "date" in df.columns else df).copy()
    required = ("open", "high", "low", "close", "volume")
    if any(col not in out.columns for col in required):
        return pd.DataFrame()
    for col in required:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["open"] = out["open"].fillna(out["close"])
    return out.dropna(subset=["high", "low", "close", "volume"]).reset_index(drop=True)


def _safe_ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den <= 0:
        return None
    return float(num) / float(den)


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def _bounded(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _tail_mean(series: pd.Series, window: int, *, exclude_last: int = 0) -> float | None:
    if len(series) <= exclude_last:
        return None
    data = series.iloc[:-exclude_last] if exclude_last else series
    if len(data) < window:
        return None
    value = float(data.tail(window).mean())
    return value if value > 0 else None


def _last_ma(close: pd.Series, window: int) -> float | None:
    if len(close) < window:
        return None
    value = float(close.rolling(window).mean().iloc[-1])
    return value if pd.notna(value) and value > 0 else None


def _base_metrics(df: pd.DataFrame) -> dict[str, float | bool | None]:
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    last = df.iloc[-1]
    prev_close = float(close.iloc[-2]) if len(close) >= 2 else float(last["close"])
    day_range = max(float(last["high"]) - float(last["low"]), 1e-9)
    prior_high_20 = float(high.iloc[:-1].tail(20).max()) if len(high) > 1 else None
    prior_high_60 = float(high.iloc[:-1].tail(60).max()) if len(high) > 1 else None
    support_60 = float(low.iloc[:-1].tail(60).min()) if len(low) > 1 else None
    vol_ratio_20 = _safe_ratio(float(last["volume"]), _tail_mean(volume, 20, exclude_last=1))
    recent3_vol = _tail_mean(volume, 3)
    prior20_vol = _tail_mean(volume.iloc[:-3], 20) if len(volume) > 3 else None
    return {
        "day_pct": _safe_ratio(float(last["close"]) - prev_close, prev_close) * 100 if prev_close > 0 else None,
        "range_pct": _safe_ratio(day_range, prev_close) * 100 if prev_close > 0 else None,
        "body_pct": _safe_ratio(float(last["close"]) - float(last["open"]), float(last["open"])) * 100
        if float(last["open"]) > 0
        else None,
        "close_pos": _safe_ratio(float(last["close"]) - float(last["low"]), day_range),
        "upper_wick_pct": _safe_ratio(float(last["high"]) - max(float(last["open"]), float(last["close"])), day_range)
        * 100,
        "lower_wick_pct": _safe_ratio(min(float(last["open"]), float(last["close"])) - float(last["low"]), day_range)
        * 100,
        "volume_ratio_20": vol_ratio_20,
        "recent3_volume_ratio_20": _safe_ratio(recent3_vol, prior20_vol),
        "prior_high_20": prior_high_20,
        "prior_high_60": prior_high_60,
        "support_60": support_60,
        "breakout_20": prior_high_20 is not None and float(last["close"]) >= prior_high_20 * 0.99,
        "breakout_60": prior_high_60 is not None and float(last["close"]) >= prior_high_60 * 0.99,
        "failed_breakout_20": prior_high_20 is not None
        and float(last["high"]) >= prior_high_20 * 1.005
        and float(last["close"]) < prior_high_20 * 0.995,
        "support_reclaim_60": support_60 is not None
        and float(last["low"]) <= support_60 * 0.995
        and float(last["close"]) >= support_60 * 1.005,
        "ma20": _last_ma(close, 20),
        "ma50": _last_ma(close, 50),
        "ma200": _last_ma(close, 200),
    }


def _score_absorption(m: dict[str, Any]) -> float:
    score = 0.0
    vol = float(m.get("volume_ratio_20") or 0.0)
    close_pos = float(m.get("close_pos") or 0.0)
    day_pct = float(m.get("day_pct") or 0.0)
    lower_wick = float(m.get("lower_wick_pct") or 0.0)
    score += min(vol / 1.5, 1.0) * 28.0
    score += 30.0 if close_pos >= 0.68 else 18.0 if close_pos >= 0.52 else 0.0
    score += 20.0 if day_pct >= -0.5 else 10.0 if day_pct >= -2.0 else 0.0
    score += min(lower_wick / 35.0, 1.0) * 12.0
    if m.get("support_reclaim_60"):
        score += 18.0
    return _bounded(score)


def _score_dry_up(m: dict[str, Any]) -> float:
    ratio = m.get("recent3_volume_ratio_20")
    if ratio is None:
        return 0.0
    close_pos = float(m.get("close_pos") or 0.0)
    range_pct = float(m.get("range_pct") or 0.0)
    score = max(0.0, (1.0 - float(ratio)) * 90.0)
    if close_pos >= 0.55:
        score += 12.0
    if range_pct <= 3.0:
        score += 10.0
    return _bounded(score)


def _score_breakout(m: dict[str, Any]) -> float:
    if not (m.get("breakout_20") or m.get("breakout_60")):
        return 0.0
    vol = float(m.get("volume_ratio_20") or 0.0)
    close_pos = float(m.get("close_pos") or 0.0)
    upper_wick = float(m.get("upper_wick_pct") or 0.0)
    body_pct = float(m.get("body_pct") or 0.0)
    score = min(vol / 2.0, 1.0) * 35.0
    score += 30.0 if close_pos >= 0.75 else 18.0 if close_pos >= 0.6 else 0.0
    score += 18.0 if body_pct >= 3.0 else 8.0 if body_pct > 0 else 0.0
    score += max(0.0, 17.0 - min(upper_wick, 50.0) * 0.34)
    return _bounded(score)


def _score_supply(m: dict[str, Any]) -> float:
    vol = float(m.get("volume_ratio_20") or 0.0)
    close_pos = float(m.get("close_pos") or 0.0)
    upper_wick = float(m.get("upper_wick_pct") or 0.0)
    day_pct = float(m.get("day_pct") or 0.0)
    score = min(vol / 2.0, 1.0) * 35.0
    score += 28.0 if close_pos <= 0.35 else 14.0 if close_pos <= 0.5 else 0.0
    score += min(upper_wick / 45.0, 1.0) * 22.0
    if day_pct < 0:
        score += 10.0
    if m.get("failed_breakout_20"):
        score += 18.0
    return _bounded(score)


def _score_reclaim(m: dict[str, Any]) -> float:
    if not m.get("support_reclaim_60"):
        return 0.0
    close_pos = float(m.get("close_pos") or 0.0)
    lower_wick = float(m.get("lower_wick_pct") or 0.0)
    vol = float(m.get("volume_ratio_20") or 0.0)
    score = 42.0 + min(lower_wick / 35.0, 1.0) * 24.0
    score += 18.0 if close_pos >= 0.6 else 8.0 if close_pos >= 0.45 else 0.0
    score += min(vol / 1.5, 1.0) * 16.0
    return _bounded(score)


def _tags(m: dict[str, Any], scores: dict[str, float]) -> tuple[list[str], list[str], str]:
    positive = []
    negative = []
    if scores["absorption_score"] >= 70:
        positive.append("absorption")
    if scores["dry_up_score"] >= 65:
        positive.append("dry_up")
    if scores["breakout_quality_score"] >= 70:
        positive.append("quality_breakout")
    if scores["reclaim_score"] >= 70:
        positive.append("support_reclaim")
    if scores["supply_pressure_score"] >= 70:
        negative.append("supply_pressure")
    if scores["failed_breakout_score"] >= 60:
        negative.append("failed_breakout")
    if float(m.get("close_pos") or 0.0) < 0.35:
        negative.append("weak_close")
    bias = "supply" if negative and not positive else "demand" if positive else "neutral"
    return positive, negative, bias


def compute_price_action_footprint(df: pd.DataFrame | None, signal_type: str) -> dict[str, Any]:
    frame = _numeric_frame(df)
    if len(frame) < 20:
        return {}
    metrics = _base_metrics(frame)
    failed_score = 0.0
    if metrics.get("failed_breakout_20"):
        failed_score = 45.0 + min(float(metrics.get("volume_ratio_20") or 0.0) / 2.0, 1.0) * 35.0
    scores = {
        "absorption_score": _score_absorption(metrics),
        "dry_up_score": _score_dry_up(metrics),
        "breakout_quality_score": _score_breakout(metrics),
        "supply_pressure_score": _score_supply(metrics),
        "failed_breakout_score": _bounded(failed_score),
        "reclaim_score": _score_reclaim(metrics),
    }
    tags, negative_tags, bias = _tags(metrics, scores)
    return {
        "version": "price_action_footprint_v1",
        "signal_type": str(signal_type or "").strip().lower(),
        "bias": bias,
        "tags": tags,
        "negative_tags": negative_tags,
        **{key: _round(value) if isinstance(value, float) else value for key, value in metrics.items()},
        **scores,
    }


def build_price_action_footprint_map(
    triggers: dict[str, list[tuple[str, float]]],
    df_map: dict[str, pd.DataFrame],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for signal_type, hits in (triggers or {}).items():
        sig = str(signal_type or "").strip().lower()
        for code, _score in hits or []:
            code_s = str(code or "").strip()
            if not code_s or not sig:
                continue
            key = f"{sig}:{code_s}"
            if key in out:
                continue
            footprint = compute_price_action_footprint(df_map.get(code_s), sig)
            if footprint:
                out[key] = footprint
                out.setdefault(code_s, footprint)
    return out
