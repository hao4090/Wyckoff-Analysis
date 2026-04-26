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

# (table, date_column, ttl_days)
# 注意: signal_pending / market_signal_daily / daily_nav 表可能尚未创建，
# cleanup_table 中会优雅处理 PGRST205 (表不存在) 错误。
CLEANUP_RULES: list[tuple[str, str, int]] = [
    (TABLE_STOCK_HIST_CACHE, "date", 320),
    (TABLE_TRADE_ORDERS, "trade_date", 15),
    (TABLE_RECOMMENDATION_TRACKING, "recommend_date", 40),
    (TABLE_SIGNAL_PENDING, "signal_date", 15),
    (TABLE_MARKET_SIGNAL_DAILY, "trade_date", 30),
    (TABLE_DAILY_NAV, "trade_date", 15),
]

# 以下表的日期列类型为 bigint，需要将 cutoff 转为 Unix 时间戳(秒)
BIGINT_DATE_COLUMNS = {
    TABLE_RECOMMENDATION_TRACKING: "recommend_date",
}


def _cutoff_iso(ttl_days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=ttl_days)).date().isoformat()


def _cutoff_unix_timestamp(ttl_days: int) -> int:
    """将 cutoff 转为 Unix 时间戳(秒)，用于 bigint 类型的日期列。"""
    return int(
        (datetime.now(timezone.utc) - timedelta(days=ttl_days)).timestamp()
    )


def _is_table_not_found_error(error: Exception) -> bool:
    """检查是否为 Supabase PGRST205 表不存在错误。"""
    err_str = str(error)
    return "PGRST205" in err_str or "not found" in err_str.lower()


def cleanup_table(
    client, table: str, date_col: str, ttl_days: int, *, dry_run: bool = False
) -> tuple[str, int | None]:
    # bigint 列: 使用 Unix 时间戳
    if table in BIGINT_DATE_COLUMNS and BIGINT_DATE_COLUMNS[table] == date_col:
        cutoff = str(_cutoff_unix_timestamp(ttl_days))
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

        # 大表分批删除: 先尝试一次性删除, 超时时按 symbol 分批
        max_batch = 500
        deleted_total = 0
        for _ in range(max_batch):
            resp = (
                client.table(table)
                .select("id")
                .lt(date_col, cutoff)
                .limit(max_batch)
                .execute()
            )
            rows = resp.data or []
            if not rows:
                break

            ids = [r["id"] for r in rows]
            client.table(table).delete().in_("id", ids).execute()
            deleted_total += len(ids)

        return "ok", deleted_total
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
