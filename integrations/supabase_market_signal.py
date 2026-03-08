# -*- coding: utf-8 -*-
"""
Supabase 最新交易日市场信号读写

用途：
1) 定时任务写入 A50 / VIX / 大盘水温
2) Web 端读取最新交易日市场信号并渲染全局提示栏
"""
from __future__ import annotations

from datetime import date, datetime
import os
from typing import Any

from supabase import Client, create_client

from core.constants import TABLE_MARKET_SIGNAL_DAILY

def _get_supabase_admin_client() -> Client:
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (
        (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        or (os.getenv("SUPABASE_KEY") or "").strip()
    )
    if not url or not key:
        raise ValueError("SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 未配置")
    return create_client(url, key)


def is_supabase_admin_configured() -> bool:
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (
        (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        or (os.getenv("SUPABASE_KEY") or "").strip()
    )
    return bool(url and key)


def _normalize_trade_date(raw: Any) -> str:
    if isinstance(raw, date):
        return raw.isoformat()
    text = str(raw or "").strip()
    if len(text) >= 10:
        return text[:10]
    return text


def _safe_float(raw: Any) -> float | None:
    try:
        if raw is None:
            return None
        text = str(raw).strip().replace(",", "")
        if not text:
            return None
        return float(text)
    except Exception:
        return None


def _format_signed_pct(raw: Any) -> str:
    value = _safe_float(raw)
    if value is None:
        return "--"
    return f"{value:+.2f}%"


def _format_plain(raw: Any, digits: int = 2) -> str:
    value = _safe_float(raw)
    if value is None:
        return "--"
    return f"{value:.{digits}f}"


def _benchmark_regime_desc(regime: str) -> str:
    mapping = {
        "RISK_ON": "偏强",
        "NEUTRAL": "中性",
        "RISK_OFF": "偏弱",
        "CRASH": "极弱",
        "BLACK_SWAN": "极端恶劣",
        "UNKNOWN": "待确认",
    }
    return mapping.get(str(regime or "").strip().upper(), "待确认")


def _premarket_regime_desc(regime: str) -> str:
    mapping = {
        "NORMAL": "平稳",
        "CAUTION": "情绪冲击",
        "RISK_OFF": "转冷",
        "BLACK_SWAN": "急剧恶化",
    }
    return mapping.get(str(regime or "").strip().upper(), "待确认")


def compose_market_banner(row: dict[str, Any] | None) -> dict[str, str]:
    data = dict(row or {})
    trade_date = _normalize_trade_date(data.get("trade_date"))
    benchmark_regime = str(data.get("benchmark_regime", "") or "").strip().upper()
    premarket_regime = str(data.get("premarket_regime", "") or "").strip().upper()
    a50_pct = _safe_float(data.get("a50_pct_chg"))
    vix_pct = _safe_float(data.get("vix_pct_chg"))

    if benchmark_regime in {"CRASH", "BLACK_SWAN"} or (
        premarket_regime == "BLACK_SWAN" and benchmark_regime not in {"RISK_ON"}
    ):
        tone = "恶劣"
    elif premarket_regime in {"BLACK_SWAN", "RISK_OFF"} or benchmark_regime == "RISK_OFF":
        tone = "保守"
    elif benchmark_regime == "RISK_ON" and (
        premarket_regime == "CAUTION"
        or (a50_pct is not None and a50_pct < 0)
        or (vix_pct is not None and vix_pct >= 8.0)
    ):
        tone = "谨慎乐观"
    elif benchmark_regime == "NEUTRAL" or premarket_regime == "CAUTION":
        tone = "谨慎"
    elif benchmark_regime == "RISK_ON":
        tone = "乐观"
    else:
        tone = "谨慎"

    if benchmark_regime == "RISK_ON" and premarket_regime in {"BLACK_SWAN", "RISK_OFF"}:
        title = "亲爱的投资者，最新交易日的大盘偏强，但盘前风险已显著抬升。"
    elif benchmark_regime == "RISK_ON" and premarket_regime == "CAUTION":
        title = "亲爱的投资者，最新交易日的大盘仍偏强，但盘前情绪出现扰动。"
    elif benchmark_regime in {"CRASH", "BLACK_SWAN"}:
        title = "亲爱的投资者，最新交易日市场环境恶劣，防守优先。"
    elif benchmark_regime == "RISK_ON" and premarket_regime == "NORMAL":
        title = "亲爱的投资者，最新交易日的大盘与盘前信号共振偏强。"
    elif benchmark_regime == "NEUTRAL" and premarket_regime in {"BLACK_SWAN", "RISK_OFF"}:
        title = "亲爱的投资者，最新交易日水温中性，但盘前风险需要优先防守。"
    elif benchmark_regime == "NEUTRAL" and premarket_regime == "CAUTION":
        title = "亲爱的投资者，最新交易日水温中性，盘前情绪有扰动。"
    else:
        title = "亲爱的投资者，最新交易日请顺势而为，保持节奏。"

    action_text = {
        "恶劣": "以上指标均为最新交易日盘后数据。当前以防守为先，先控制风险，盘中沉着应对，只在把握足够高时再考虑出手。",
        "保守": "以上指标均为最新交易日盘后数据。当前宜收敛仓位，盘中沉着应对，少做激进追价，优先寻找更清晰、更高胜率的打法。",
        "谨慎": "以上指标均为最新交易日盘后数据。当前信号仍有分歧，盘中先耐心观察，等方向更明确后再做高胜率出手。",
        "谨慎乐观": "以上指标均为最新交易日盘后数据。可以顺势跟随，但盘中仍要沉着应对，控制节奏和仓位，优先做更确定的高胜率机会。",
        "乐观": "以上指标均为最新交易日盘后数据。当前环境相对顺风，但盘中仍要沉着应对，优先参与有确认、有纪律的高胜率打法。",
    }

    return {
        "banner_tone": tone,
        "banner_title": title,
        "banner_message": action_text.get(tone, "保持纪律，尊重市场。"),
    }


def _deep_merge_source_jobs(base: Any, patch: Any) -> dict[str, Any]:
    left = dict(base or {}) if isinstance(base, dict) else {}
    right = dict(patch or {}) if isinstance(patch, dict) else {}
    merged = dict(left)
    for key, value in right.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def _normalize_row_for_upsert(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    if "trade_date" in out:
        out["trade_date"] = _normalize_trade_date(out.get("trade_date"))
    for key in [
        "main_index_close",
        "main_index_ma50",
        "main_index_ma200",
        "main_index_recent3_cum_pct",
        "main_index_today_pct",
        "smallcap_close",
        "smallcap_recent3_cum_pct",
        "a50_close",
        "a50_pct_chg",
        "vix_close",
        "vix_pct_chg",
    ]:
        if key in out:
            out[key] = _safe_float(out.get(key))
    for key in ["a50_value_date", "vix_value_date"]:
        if key in out and out.get(key):
            out[key] = _normalize_trade_date(out.get(key))
    if "premarket_reasons" in out and out.get("premarket_reasons") is None:
        out["premarket_reasons"] = []
    if "source_jobs" in out and not isinstance(out.get("source_jobs"), dict):
        out["source_jobs"] = {}
    return out


def _load_market_signal_by_trade_date(client: Client, trade_date: str) -> dict[str, Any] | None:
    resp = (
        client.table(TABLE_MARKET_SIGNAL_DAILY)
        .select("*")
        .eq("trade_date", trade_date)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    return dict(resp.data[0])


def upsert_market_signal_daily(trade_date: date | str, patch: dict[str, Any]) -> bool:
    if not is_supabase_admin_configured():
        return False
    try:
        client = _get_supabase_admin_client()
        trade_date_text = _normalize_trade_date(trade_date)
        existing = _load_market_signal_by_trade_date(client, trade_date_text) or {}
        merged = dict(existing)
        merged.update(_normalize_row_for_upsert(dict(patch or {})))
        merged["trade_date"] = trade_date_text
        merged["source_jobs"] = _deep_merge_source_jobs(
            existing.get("source_jobs"),
            patch.get("source_jobs") if isinstance(patch, dict) else None,
        )
        merged.update(compose_market_banner(merged))
        merged["updated_at"] = datetime.utcnow().isoformat()
        client.table(TABLE_MARKET_SIGNAL_DAILY).upsert(
            _normalize_row_for_upsert(merged),
            on_conflict="trade_date",
        ).execute()
        return True
    except Exception as e:
        print(f"[supabase_market_signal] upsert_market_signal_daily failed: {e}")
        return False


def load_latest_market_signal_daily(client: Client | None = None) -> dict[str, Any] | None:
    attempts: list[tuple[str, Client | None]] = []
    if client is not None:
        attempts.append(("provided", client))
    else:
        if is_supabase_admin_configured():
            try:
                attempts.append(("admin", _get_supabase_admin_client()))
            except Exception:
                pass
        try:
            from integrations.supabase_client import get_supabase_client

            attempts.append(("session", get_supabase_client()))
        except Exception:
            pass

    for source, sb in attempts:
        try:
            if sb is None:
                continue
            resp = (
                sb.table(TABLE_MARKET_SIGNAL_DAILY)
                .select("*")
                .order("trade_date", desc=True)
                .limit(1)
                .execute()
            )
            if resp.data:
                return dict(resp.data[0])
        except Exception:
            continue
    return None
