# -*- coding: utf-8 -*-
"""
数据库维护任务 — 多表过期数据清理。
每日定时运行，按各表 TTL 策略删除历史记录以节约数据库空间。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

if __name__ == "__main__" or not __package__:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.constants import (
    TABLE_DAILY_NAV,
    TABLE_MARKET_SIGNAL_DAILY,
    TABLE_RECOMMENDATION_TRACKING,
    TABLE_SIGNAL_PENDING,
    TABLE_STOCK_HIST_CACHE,
    TABLE_TRADE_ORDERS,
)
from integrations.supabase_base import create_admin_client

# 保留期检查阈值（超过这些值会触发告警）
DELETE_COUNT_THRESHOLD = 100   # 单次删除行数阈值
DELETE_RATIO_THRESHOLD = 0.5   # 单次删除比例阈值

# (table, date_column, ttl_days)
# 注意: signal_pending / market_signal_daily / daily_nav 表可能尚未创建，
# cleanup_table 中会优雅处理 PGRST205 / "not found" 等错误。
CLEANUP_RULES: list[tuple[str, str, int]] = [
    (TABLE_STOCK_HIST_CACHE, "date", 320),
    (TABLE_TRADE_ORDERS, "trade_date", 15),
    (TABLE_RECOMMENDATION_TRACKING, "recommend_date", 40),
    (TABLE_SIGNAL_PENDING, "signal_date", 15),
    (TABLE_MARKET_SIGNAL_DAILY, "trade_date", 30),
    (TABLE_DAILY_NAV, "trade_date", 15),
]

# 以下表的日期列类型为 bigint(YYYYMMDD格式)，需要特殊处理
BIGINT_DATE_COLUMNS = {
    TABLE_RECOMMENDATION_TRACKING: "recommend_date",
}


def _cutoff_iso(ttl_days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=ttl_days)).date().isoformat()


def _cutoff_yyyymmdd(ttl_days: int) -> int:
    """将 cutoff 转为 YYYYMMDD 格式整数，用于 recommend_date 列（YYYYMMDD格式）。"""
    return int((datetime.now(timezone.utc).date() - timedelta(days=ttl_days)).strftime("%Y%m%d"))


def _send_deletion_warning(table: str, rows_to_delete: int, total_rows: int, ttl_days: int) -> None:
    """当删除行数超过阈值时发送告警通知。"""
    delete_ratio = rows_to_delete / total_rows if total_rows > 0 else 0
    threshold_count = int(os.getenv("DB_MAINTENANCE_COUNT_THRESHOLD", str(DELETE_COUNT_THRESHOLD)))
    threshold_ratio = float(os.getenv("DB_MAINTENANCE_RATIO_THRESHOLD", str(DELETE_RATIO_THRESHOLD)))
    exceeded = rows_to_delete > threshold_count or delete_ratio > threshold_ratio
    msg = (
        f"⚠️ **[db_maintenance] 数据删除告警**\n\n"
        f"- 表名: `{table}`\n"
        f"- 保留期: {ttl_days} 天\n"
        f"- 本次将删除: **{rows_to_delete}** 行\n"
        f"- 当前总行数: {total_rows} 行\n"
        f"- 删除比例: {delete_ratio:.1%}\n"
        f"- 阈值: ≤{threshold_count} 行 且 ≤{threshold_ratio:.0%}\n"
    )
    if exceeded:
        msg += "\n🚨 **超过阈值，已暂停自动删除！**\n请检查是否正常。"
        print(f"[db_maintenance] WARNING: {table} delete threshold exceeded: {rows_to_delete}/{total_rows}")
    else:
        msg += "\n（如为异常，请立即检查！）"
    try:
        from utils.notify import send_all_webhooks
        feishu = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        wecom = os.getenv("WECOM_WEBHOOK_URL", "").strip()
        dingtalk = os.getenv("DINGTALK_WEBHOOK_URL", "").strip()
        tg_token = os.getenv("TG_BOT_TOKEN", "").strip()
        tg_chat = os.getenv("TG_CHAT_ID", "").strip()
        if feishu or wecom or dingtalk or (tg_token and tg_chat):
            send_all_webhooks(feishu, wecom, dingtalk, "数据删除告警", msg,
                             tg_bot_token=tg_token, tg_chat_id=tg_chat)
    except Exception as e:
        print(f"[db_maintenance] failed to send warning: {e}")


def _is_table_not_found_error(error: Exception) -> bool:
    """检查是否为 Supabase PGRST205 表不存在错误。"""
    err_str = str(error)
    return "PGRST205" in err_str or "not found" in err_str.lower()


def _cleanup_stock_hist_cache_batched(
    client, cutoff: str, *, batch_days: int = 10, max_batches: int = 50
) -> tuple[str, int | None]:
    """大表分批删除: 从 oldest data 开始，按 batch_days 逐步向 cutoff 推进。

    每次只删除一个窄日期范围（如 10 天），避免 statement_timeout。
    """
    from datetime import date as date_type

    if isinstance(cutoff, str):
        cutoff_date = datetime.strptime(cutoff, "%Y-%m-%d").date()
    else:
        cutoff_date = cutoff

    # 先找到最老数据的日期
    resp = (
        client.table(TABLE_STOCK_HIST_CACHE)
        .select("date")
        .order("date", desc=False)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return "ok", 0

    oldest_str = str(resp.data[0].get("date", ""))
    if not oldest_str:
        return "ok", 0

    oldest_date = datetime.strptime(oldest_str, "%Y-%m-%d").date()
    current = oldest_date
    deleted_batches = 0

    while current < cutoff_date and deleted_batches < max_batches:
        batch_end = current + timedelta(days=batch_days)
        if batch_end > cutoff_date:
            batch_end = cutoff_date

        batch_start_str = current.isoformat()
        batch_end_str = batch_end.isoformat()

        # 检查是否有数据在这个范围内
        has_data = (
            client.table(TABLE_STOCK_HIST_CACHE)
            .select("date")
            .gte("date", batch_start_str)
            .lt("date", batch_end_str)
            .limit(1)
            .execute()
        )
        if has_data.data:
            # 删除这个日期范围内的所有数据
            client.table(TABLE_STOCK_HIST_CACHE).delete().gte(
                "date", batch_start_str
            ).lt("date", batch_end_str).execute()

        current = batch_end
        deleted_batches += 1

    return "ok", deleted_batches


def cleanup_table(
    client, table: str, date_col: str, ttl_days: int, *, dry_run: bool = False
) -> tuple[str, int | None]:
    # bigint 列 (recommend_date): 使用 YYYYMMDD 格式整数比较
    if table in BIGINT_DATE_COLUMNS and BIGINT_DATE_COLUMNS[table] == date_col:
        cutoff = _cutoff_yyyymmdd(ttl_days)
    else:
        cutoff = _cutoff_iso(ttl_days)

    try:
        if dry_run:
            resp = (
                client.table(table)
                .select("*", count="exact")
                .lt(date_col, cutoff)
                .limit(0)
                .execute()
            )
            return "dry_run", resp.count or 0

        # stock_hist_cache 是大表(百万级)，需要按 symbol 分批删除防止超时
        if table == TABLE_STOCK_HIST_CACHE:
            return _cleanup_stock_hist_cache_batched(client, cutoff)

        # 删除前安全检查（非 stock_hist_cache 的小表）
        total_resp = client.table(table).select("*", count="exact").limit(0).execute()
        total_rows = total_resp.count or 0
        to_delete_resp = (
            client.table(table)
            .select("*", count="exact")
            .lt(date_col, cutoff)
            .limit(0)
            .execute()
        )
        rows_to_delete = to_delete_resp.count or 0
        if rows_to_delete > 0:
            _send_deletion_warning(table, rows_to_delete, total_rows, ttl_days)
            # 阈值：超过阈值则暂停
            delete_ratio = rows_to_delete / total_rows if total_rows > 0 else 0
            threshold_count = int(os.getenv("DB_MAINTENANCE_COUNT_THRESHOLD", str(DELETE_COUNT_THRESHOLD)))
            threshold_ratio = float(os.getenv("DB_MAINTENANCE_RATIO_THRESHOLD", str(DELETE_RATIO_THRESHOLD)))
            if rows_to_delete > threshold_count or delete_ratio > threshold_ratio:
                return f"skipped: threshold exceeded ({rows_to_delete} rows, {delete_ratio:.1%})", rows_to_delete

        # 其他小表: 直接 PostgREST DELETE（高效单请求）
        client.table(table).delete().lt(date_col, cutoff).execute()
        return "ok", rows_to_delete
    except Exception as e:
        # 表尚未创建时视为非致命错误
        if _is_table_not_found_error(e):
            return "skipped: table not found", None
        return f"error: {e}", None


def cleanup_unadjusted_cache(client) -> tuple[bool, str]:
    """删除 stock_hist_cache 中 adjust='none' 的存量缓存。"""
    try:
        client.table(TABLE_STOCK_HIST_CACHE).delete().eq("adjust", "none").execute()
        return True, "cleaned adjust=none rows"
    except Exception as first_err:
        try:
            batch_size = max(int(os.getenv("STOCK_CACHE_CLEANUP_SYMBOL_BATCH", "300")), 1)
            max_rounds = max(int(os.getenv("STOCK_CACHE_CLEANUP_MAX_ROUNDS", "200")), 1)
            deleted_symbols = 0
            for _ in range(max_rounds):
                probe = (
                    client.table(TABLE_STOCK_HIST_CACHE)
                    .select("symbol")
                    .eq("adjust", "none")
                    .limit(batch_size)
                    .execute()
                )
                symbols = sorted(
                    {
                        str(r.get("symbol", "")).strip()
                        for r in (probe.data or [])
                        if str(r.get("symbol", "")).strip()
                    }
                )
                if not symbols:
                    return True, f"cleaned adjust=none (batched, symbols={deleted_symbols})"
                for sym in symbols:
                    client.table(TABLE_STOCK_HIST_CACHE).delete().eq("adjust", "none").eq(
                        "symbol", sym
                    ).execute()
                    deleted_symbols += 1
            return False, f"partial cleanup, deleted_symbols={deleted_symbols}, first_err={first_err}"
        except Exception as batch_err:
            return False, f"batch cleanup also failed: {batch_err} (original: {first_err})"


def main() -> int:
    parser = argparse.ArgumentParser(description="数据库维护 — 多表过期数据清理")
    parser.add_argument("--dry-run", action="store_true", help="只查询待清理行数，不实际删除")
    args = parser.parse_args()

    client = create_admin_client()
    all_ok = True

    for table, date_col, ttl_days in CLEANUP_RULES:
        status, count = cleanup_table(client, table, date_col, ttl_days, dry_run=args.dry_run)
        suffix = f" ({count} rows)" if count is not None else ""
        print(f"[db_maintenance] {table}: {status}, ttl={ttl_days}d{suffix}")
        if status.startswith("error"):
            all_ok = False

    ok, msg = cleanup_unadjusted_cache(client)
    print(f"[db_maintenance] stock_hist_cache adjust=none: ok={ok}, {msg}")
    if not ok:
        all_ok = False

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
