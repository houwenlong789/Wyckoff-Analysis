# -*- coding: utf-8 -*-
"""
Supabase 推荐跟踪数据存取模块
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
from supabase import Client, create_client
from core.constants import TABLE_RECOMMENDATION_TRACKING

def _get_supabase_admin_client() -> Client:
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (
        (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip() 
        or (os.getenv("SUPABASE_KEY") or "").strip()
    )
    if not url or not key:
        raise ValueError("SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 未配置")
    return create_client(url, key)

def is_supabase_configured() -> bool:
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip() or (os.getenv("SUPABASE_KEY") or "").strip()
    return bool(url and key)


def _parse_recommend_date(raw_value: Any) -> date | None:
    if raw_value is None:
        return None
    s = str(raw_value).strip()
    if not s:
        return None
    try:
        if len(s) == 8 and s.isdigit():
            return datetime.strptime(s, "%Y%m%d").date()
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


def _parse_write_date(record: dict[str, Any]) -> date | None:
    """优先用写入日 created_at 的日期，没有则用 recommend_date，用于纠错时查该日收盘价。"""
    created = record.get("created_at")
    if created is not None and str(created).strip():
        try:
            s = str(created).strip()
            if "T" in s or " " in s:
                return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
            if len(s) == 8 and s.isdigit():
                return datetime.strptime(s, "%Y%m%d").date()
            return datetime.fromisoformat(s).date()
        except Exception:
            pass
    return _parse_recommend_date(record.get("recommend_date"))


def _resolve_initial_price_from_history(code_str: str, rec_date: date) -> float:
    """
    用推荐日附近历史日线回填加入价：
    1) 优先 rec_date 当天
    2) 若当天无数据，回看最近 7 天并取 <= rec_date 的最近交易日
    """
    try:
        from integrations.data_source import fetch_stock_hist

        rec_s = rec_date.strftime("%Y-%m-%d")
        hist = fetch_stock_hist(code_str, rec_s, rec_s, adjust="qfq")
        if hist is not None and not hist.empty:
            close_s = pd.to_numeric(hist.get("收盘"), errors="coerce").dropna()
            if not close_s.empty:
                px = float(close_s.iloc[-1])
                if px > 0:
                    return px

        start_s = (rec_date - timedelta(days=7)).strftime("%Y-%m-%d")
        hist2 = fetch_stock_hist(code_str, start_s, rec_s, adjust="qfq")
        if hist2 is None or hist2.empty:
            return 0.0
        df = hist2.copy()
        if "日期" not in df.columns or "收盘" not in df.columns:
            return 0.0
        df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
        df["收盘"] = pd.to_numeric(df["收盘"], errors="coerce")
        df = df.dropna(subset=["日期", "收盘"]).sort_values("日期")
        if df.empty:
            return 0.0
        df = df[df["日期"].dt.date <= rec_date]
        if df.empty:
            return 0.0
        px = float(df["收盘"].iloc[-1])
        return px if px > 0 else 0.0
    except Exception:
        return 0.0

def upsert_recommendations(recommend_date: int, symbols_info: list[dict[str, Any]]) -> bool:
    """
    将每日选出的股票存入推荐跟踪表
    recommend_date: YYYYMMDD (int)
    """
    if not is_supabase_configured() or not symbols_info:
        return False
    try:
        client = _get_supabase_admin_client()

        # 预读已有记录的 recommend_count（按 code 聚合），后续在此基础上自增
        existing_counts: dict[int, int] = {}
        try:
            resp = (
                client.table(TABLE_RECOMMENDATION_TRACKING)
                .select("code,recommend_count")
                .execute()
            )
            for row in resp.data or []:
                try:
                    code_int = int(row.get("code"))
                except Exception:
                    continue
                existing_counts[code_int] = int(row.get("recommend_count") or 1)
        except Exception:
            existing_counts = {}

        payload = []
        for s in symbols_info:
            raw_code = str(s.get("code", "")).strip()
            # 提取纯数字部分 (比如 "000001.SZ" -> "000001")
            code_str = "".join(filter(str.isdigit, raw_code))
            if not code_str:
                continue
            
            # price 优先使用 step2 传入的 initial_price，并做多字段兜底
            price = 0.0
            for key in ("initial_price", "current_price", "price", "latest_price", "close"):
                raw_price = s.get(key)
                if raw_price is None or raw_price == "":
                    continue
                try:
                    parsed = float(raw_price)
                except Exception:
                    continue
                if parsed > 0:
                    price = parsed
                    break

            score_val: float | None = None
            for score_key in ("funnel_score", "priority_score", "score"):
                raw_score = s.get(score_key)
                if raw_score is None or raw_score == "":
                    continue
                try:
                    score_val = float(raw_score)
                    break
                except Exception:
                    continue
            
            code_int = int(code_str)
            old_cnt = existing_counts.get(code_int, 0)
            new_cnt = max(old_cnt + 1, 1)

            payload.append({
                "code": code_int,  # 存为 INT，首位0会消失
                "name": str(s.get("name", "")).strip(),
                "recommend_reason": str(s.get("tag", "")).strip(),
                "recommend_date": recommend_date,
                "initial_price": price,
                "current_price": price, # 初始时当前价等于加入价
                "change_pct": 0.0,      # 初始涨跌幅为 0
                "recommend_count": new_cnt,
                "funnel_score": score_val,
                "is_ai_recommended": False,
                "updated_at": datetime.utcnow().isoformat()
            })
        
        if payload:
            # 使用 upsert，基于 code 唯一约束：
            # - recommendation_tracking 中每只股票(code)仅保留一条记录；
            # - recommend_date / initial_price / current_price 等字段以“最近一次推荐”为准覆盖；
            # - recommend_count 为该股票被推荐的累计次数。
            try:
                client.table(TABLE_RECOMMENDATION_TRACKING).upsert(
                    payload, on_conflict="code"
                ).execute()
            except Exception as e:
                msg = str(e).lower()
                if "is_ai_recommended" in msg or "funnel_score" in msg:
                    fallback_payload: list[dict[str, Any]] = []
                    for row in payload:
                        r = dict(row)
                        r.pop("is_ai_recommended", None)
                        r.pop("funnel_score", None)
                        fallback_payload.append(r)
                    client.table(TABLE_RECOMMENDATION_TRACKING).upsert(
                        fallback_payload, on_conflict="code,recommend_date"
                    ).execute()
                else:
                    raise
        return True
    except Exception as e:
        print(f"[supabase_recommendation] upsert_recommendations failed: {e}")
        return False


def mark_ai_recommendations(recommend_date: int, ai_codes: list[str]) -> bool:
    """
    将某个推荐日的记录标记为是否 AI 推荐（可操作池）。
    ai_codes 传入 6 位代码字符串列表。
    """
    if not is_supabase_configured():
        return False
    try:
        client = _get_supabase_admin_client()
        now_iso = datetime.utcnow().isoformat()
        # 先全量置 false，再对白名单置 true，避免前一次残留。
        client.table(TABLE_RECOMMENDATION_TRACKING).update(
            {"is_ai_recommended": False, "updated_at": now_iso}
        ).eq("recommend_date", recommend_date).execute()

        code_ints: list[int] = []
        for code in ai_codes or []:
            code_digits = "".join(ch for ch in str(code) if ch.isdigit())
            if not code_digits:
                continue
            try:
                code_ints.append(int(code_digits))
            except Exception:
                continue
        code_ints = sorted(set(code_ints))
        if code_ints:
            client.table(TABLE_RECOMMENDATION_TRACKING).update(
                {"is_ai_recommended": True, "updated_at": now_iso}
            ).eq("recommend_date", recommend_date).in_("code", code_ints).execute()
        return True
    except Exception as e:
        msg = str(e)
        if "is_ai_recommended" in msg:
            print(
                "[supabase_recommendation] mark_ai_recommendations skipped: "
                "missing column is_ai_recommended (please run SQL migration)"
            )
            return False
        print(f"[supabase_recommendation] mark_ai_recommendations failed: {e}")
        return False

def sync_all_tracking_prices(
    price_map: dict[str, float] | None = None,
) -> int:
    """
    遍历表中所有股票，用最新价刷新 current_price 与 change_pct。
    price_map: 可选，code_str -> 最新收盘价。非空时直接使用；未传或为空时拉取全市场快照后更新。
    返回成功更新的数量。
    """
    if not is_supabase_configured():
        print("[supabase_recommendation] sync_all_tracking_prices: Supabase 未配置，跳过")
        return 0

    try:
        client = _get_supabase_admin_client()

        # 获取需要跟踪的股票代码（去重）
        resp = client.table(TABLE_RECOMMENDATION_TRACKING).select("code").execute()
        if not resp.data:
            print("[supabase_recommendation] sync_all_tracking_prices: 推荐表无记录，跳过")
            return 0

        unique_codes = sorted(list(set(int(r["code"]) for r in resp.data)))

        # 优先使用 price_map，否则拉取行情
        use_provided_map = bool(price_map)
        if not use_provided_map:
            from integrations.data_source import fetch_stock_spot_snapshot
            first_code = f"{unique_codes[0]:06d}"
            fetch_stock_spot_snapshot(first_code, force_refresh=True)

        updated_count = 0
        for code_int in unique_codes:
            code_str = f"{code_int:06d}"
            if use_provided_map:
                new_current_price = price_map.get(code_str)
                if new_current_price is None or new_current_price <= 0:
                    continue
                new_current_price = float(new_current_price)
            else:
                snap = fetch_stock_spot_snapshot(code_str, force_refresh=False)
                if not snap or snap.get("close") is None:
                    continue
                new_current_price = float(snap["close"])
            
            # 该股票可能有多条推荐记录（不同日期），逐条更新价格与涨跌幅
            rec_resp = client.table(TABLE_RECOMMENDATION_TRACKING).select("*").eq("code", code_int).execute()
            for record in rec_resp.data:
                initial_price = float(record.get("initial_price") or 0.0)
                rec_date = _parse_recommend_date(record.get("recommend_date"))
                update_payload = {
                    "current_price": new_current_price,
                    "updated_at": datetime.utcnow().isoformat(),
                }
                if initial_price > 0:
                    change_pct = (new_current_price - initial_price) / initial_price * 100.0
                    update_payload["change_pct"] = round(change_pct, 2)
                else:
                    backfill_price = (
                        _resolve_initial_price_from_history(code_str, rec_date)
                        if rec_date
                        else 0.0
                    )
                    if backfill_price <= 0:
                        backfill_price = new_current_price
                    update_payload["initial_price"] = backfill_price
                    update_payload["change_pct"] = (
                        round(
                            (new_current_price - backfill_price) / backfill_price * 100.0,
                            2,
                        )
                        if backfill_price > 0
                        else 0.0
                    )
                client.table(TABLE_RECOMMENDATION_TRACKING).update(update_payload).eq("id", record["id"]).execute()
                updated_count += 1

        if unique_codes and updated_count == 0:
            print(
                "[supabase_recommendation] sync_all_tracking_prices: 推荐表有 {} 只股票但 0 条更新，"
                "可能全市场行情拉取失败或非交易时段".format(len(unique_codes))
            )
        return updated_count
    except Exception as e:
        print(f"[supabase_recommendation] sync_all_tracking_prices failed: {e}")
        return 0


def correct_tracking_initial_prices() -> int:
    """
    纠错流程：遍历推荐表每条记录，用「写入日」当天收盘价（前复权）回填 initial_price，
    并用当前 current_price 重算 change_pct。写入日优先取 created_at，没有则用 recommend_date。
    每日执行可让历史数据逐步修正。
    返回被更新的记录数。
    """
    if not is_supabase_configured():
        print("[supabase_recommendation] correct_tracking_initial_prices: Supabase 未配置，跳过")
        return 0
    try:
        client = _get_supabase_admin_client()
        resp = client.table(TABLE_RECOMMENDATION_TRACKING).select("*").execute()
        if not resp.data:
            return 0
        cache: dict[tuple[str, date], float] = {}
        updated = 0
        for record in resp.data:
            write_date = _parse_write_date(record)
            if not write_date:
                continue
            code_int = record.get("code")
            if code_int is None:
                continue
            code_str = f"{int(code_int):06d}"
            current_price = float(record.get("current_price") or 0.0)
            if current_price <= 0:
                continue
            key = (code_str, write_date)
            if key not in cache:
                cache[key] = _resolve_initial_price_from_history(code_str, write_date)
            initial_from_hist = cache[key]
            if initial_from_hist <= 0:
                continue
            change_pct = round((current_price - initial_from_hist) / initial_from_hist * 100.0, 2)
            client.table(TABLE_RECOMMENDATION_TRACKING).update({
                "initial_price": initial_from_hist,
                "change_pct": change_pct,
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("id", record["id"]).execute()
            updated += 1
        return updated
    except Exception as e:
        print(f"[supabase_recommendation] correct_tracking_initial_prices failed: {e}")
        return 0


def load_recommendation_tracking(limit: int = 1000) -> list[dict[str, Any]]:
    """加载推荐跟踪数据"""
    try:
        # 这里可以使用普通 client，也可以用 admin
        from integrations.supabase_client import get_supabase_client
        client = get_supabase_client()
        resp = (
            client.table(TABLE_RECOMMENDATION_TRACKING)
            .select("*")
            .order("recommend_date", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception as e:
        print(f"[supabase_recommendation] load_recommendation_tracking failed: {e}")
        return []
