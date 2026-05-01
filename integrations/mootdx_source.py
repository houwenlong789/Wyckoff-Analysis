from __future__ import annotations

import os
import re
from datetime import datetime

import pandas as pd


def _debug_source_fail(source: str, err: Exception) -> None:
    debug = os.getenv("DATA_SOURCE_DEBUG", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if debug:
        print(f"[data_source] {source} failed: {type(err).__name__}: {err}")


def _compact_error(err: Exception, max_len: int = 120) -> str:
    msg = str(err or "").strip().replace("\n", " ")
    msg = re.sub(r"\s+", " ", msg)
    if len(msg) > max_len:
        msg = msg[: max_len - 3] + "..."
    if msg:
        return f"{type(err).__name__}: {msg}"
    return type(err).__name__


def _tag_source(df: pd.DataFrame, source: str) -> pd.DataFrame:
    df.attrs["source"] = source
    return df


def _pick_column(df: pd.DataFrame, candidates: tuple[str, ...], label: str) -> pd.Series:
    for col in candidates:
        if col in df.columns:
            return df[col]
    raise RuntimeError(f"mootdx missing column {label}")


def fetch_stock_mootdx(
    symbol: str, start: str, end: str, adjust: str
) -> pd.DataFrame:
    """
    MooTDX 日线主链路。
    输出列与主链路保持一致：日期, 开盘, 最高, 最低, 收盘, 成交量, 成交额, 涨跌幅, 换手率, 振幅
    """
    from mootdx.quotes import Quotes

    try:
        start_d = datetime.strptime(start, "%Y%m%d").date()
        end_d = datetime.strptime(end, "%Y%m%d").date()
    except Exception as e:
        raise RuntimeError(f"mootdx date parse failed: {start}..{end}") from e
    if end_d < start_d:
        raise RuntimeError(f"mootdx invalid range: {start}..{end}")

    client = Quotes.factory(market="std", multithread=True, heartbeat=True)
    if client is None:
        raise RuntimeError("mootdx client init failed")

    frames: list[pd.DataFrame] = []
    day_span = (end_d - start_d).days + 1
    target_count = max(day_span * 2 + 32, 128)
    chunk_size = 800
    adjust_norm = str(adjust or "").strip().lower()
    bars_kwargs: dict[str, str] = {}
    if adjust_norm in {"qfq", "hfq"}:
        bars_kwargs["adjust"] = adjust_norm

    try:
        for start_offset in range(0, target_count, chunk_size):
            offset = min(chunk_size, target_count - start_offset)
            part = client.bars(
                symbol=str(symbol).strip(),
                frequency=9,
                offset=offset,
                start=start_offset,
                **bars_kwargs,
            )
            if part is None or part.empty:
                break
            part = part.copy()
            frames.append(part)

            date_col = "date" if "date" in part.columns else "datetime"
            if date_col in part.columns:
                dates = pd.to_datetime(part[date_col], errors="coerce").dropna()
                if not dates.empty and dates.min().date() <= start_d:
                    break
    finally:
        try:
            client.close()
        except Exception:
            pass

    if not frames:
        raise RuntimeError("mootdx empty")

    raw = pd.concat(frames, ignore_index=True)
    date_col = "date" if "date" in raw.columns else "datetime"
    if date_col not in raw.columns:
        raise RuntimeError("mootdx missing column date")

    dt = pd.to_datetime(raw[date_col], errors="coerce")
    raw = raw.assign(_date=dt.dt.strftime("%Y-%m-%d"))
    raw = raw[raw["_date"].notna()].copy()
    if raw.empty:
        raise RuntimeError("mootdx empty date")
    raw = raw.drop_duplicates(subset=["_date"], keep="last").sort_values("_date")

    close = pd.to_numeric(_pick_column(raw, ("close", "收盘"), "close"), errors="coerce")
    open_v = pd.to_numeric(_pick_column(raw, ("open", "开盘"), "open"), errors="coerce")
    high = pd.to_numeric(_pick_column(raw, ("high", "最高"), "high"), errors="coerce")
    low = pd.to_numeric(_pick_column(raw, ("low", "最低"), "low"), errors="coerce")
    volume = pd.to_numeric(
        _pick_column(raw, ("volume", "vol", "成交量"), "volume"),
        errors="coerce",
    )
    if "amount" in raw.columns or "成交额" in raw.columns:
        amount = pd.to_numeric(
            _pick_column(raw, ("amount", "成交额"), "amount"),
            errors="coerce",
        )
    else:
        amount = pd.Series(pd.NA, index=raw.index)
    if "pct_chg" in raw.columns or "涨跌幅" in raw.columns:
        pct = pd.to_numeric(
            _pick_column(raw, ("pct_chg", "涨跌幅"), "pct_chg"),
            errors="coerce",
        )
    else:
        prev_ref = close.shift(1).where(close.shift(1) > 0)
        pct = (close / prev_ref - 1.0) * 100.0
    if "amplitude" in raw.columns or "振幅" in raw.columns:
        amp = pd.to_numeric(
            _pick_column(raw, ("amplitude", "振幅"), "amplitude"),
            errors="coerce",
        )
    else:
        prev_ref = close.shift(1).where(close.shift(1) > 0)
        amp = (high - low) / prev_ref * 100.0

    result = pd.DataFrame(
        {
            "日期": raw["_date"],
            "开盘": open_v,
            "最高": high,
            "最低": low,
            "收盘": close,
            "成交量": volume,
            "成交额": amount,
            "涨跌幅": pct,
            "换手率": pd.NA,
            "振幅": amp,
        }
    )
    start_iso = start_d.isoformat()
    end_iso = end_d.isoformat()
    result = result[(result["日期"] >= start_iso) & (result["日期"] <= end_iso)].copy()
    if result.empty:
        raise RuntimeError("mootdx empty in range")
    return result[
        [
            "日期",
            "开盘",
            "最高",
            "最低",
            "收盘",
            "成交量",
            "成交额",
            "涨跌幅",
            "换手率",
            "振幅",
        ]
    ].copy()


def try_fetch_stock_mootdx(
    symbol: str,
    start: str,
    end: str,
    adjust: str,
    failed_sources: list[str],
    failed_details: list[str],
) -> pd.DataFrame | None:
    disable_mootdx = os.getenv("DATA_SOURCE_DISABLE_MOOTDX", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if disable_mootdx:
        failed_sources.append("mootdx(disabled)")
        failed_details.append("mootdx=disabled_by_env")
        return None

    try:
        return _tag_source(
            fetch_stock_mootdx(symbol, start, end, adjust),
            "mootdx",
        )
    except ModuleNotFoundError as e:
        _debug_source_fail("mootdx", e)
        failed_sources.append(f"mootdx(未安装: {e.name})")
        failed_details.append(f"mootdx={_compact_error(e)}")
    except Exception as e:
        _debug_source_fail("mootdx", e)
        failed_sources.append("mootdx")
        failed_details.append(f"mootdx={_compact_error(e)}")
    return None
