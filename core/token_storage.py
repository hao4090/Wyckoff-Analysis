# -*- coding: utf-8 -*-
"""
Token 持久化 — Cookie（主） + localStorage（兼容）双通道。

写入时：同时写 Cookie 和 localStorage（登录时页面已加载，JS 无时序问题）。
读取时：优先用 st.context.cookies 同步读 Cookie（无 iframe 依赖），
        fallback 到 st_javascript 读 localStorage（兼容旧 token）。
清除时：同时清两者。
"""
from __future__ import annotations

import json
import urllib.parse

_STORAGE_KEY_ACCESS = "akshare_access_token"
_STORAGE_KEY_REFRESH = "akshare_refresh_token"

_COOKIE_KEY_ACCESS = "wyckoff_access_token"
_COOKIE_KEY_REFRESH = "wyckoff_refresh_token"
_COOKIE_MAX_AGE = 604800  # 7 天


# ---------------------------------------------------------------------------
# Restore (read)
# ---------------------------------------------------------------------------

def _restore_from_cookies() -> tuple[str | None, str | None]:
    """从 Cookie 同步读取 token（st.context.cookies，无 iframe 时序问题）。"""
    try:
        import streamlit as st
        cookies = st.context.cookies
        access = (cookies.get(_COOKIE_KEY_ACCESS) or "").strip()
        refresh = (cookies.get(_COOKIE_KEY_REFRESH) or "").strip()
        if access and refresh:
            return (access, refresh)
    except Exception:
        pass
    return (None, None)


def _restore_from_localstorage() -> tuple[str | None, str | None]:
    """从 localStorage 异步读取 token（旧通道 fallback）。"""
    try:
        from streamlit_javascript import st_javascript

        js = (
            "JSON.stringify({"
            f'access_token: localStorage.getItem("{_STORAGE_KEY_ACCESS}") || "", '
            f'refresh_token: localStorage.getItem("{_STORAGE_KEY_REFRESH}") || ""'
            "})"
        )
        result = st_javascript(js)
        if not result or not isinstance(result, str) or result == "0":
            return (None, None)
        data = json.loads(result)
        access = (data.get("access_token") or "").strip()
        refresh = (data.get("refresh_token") or "").strip()
        if access and refresh:
            return (access, refresh)
    except Exception:
        pass
    return (None, None)


def restore_tokens_from_storage() -> tuple[str | None, str | None]:
    """恢复 token：优先 Cookie（同步），fallback localStorage（异步）。"""
    access, refresh = _restore_from_cookies()
    if access and refresh:
        return (access, refresh)
    return _restore_from_localstorage()


# ---------------------------------------------------------------------------
# Persist (write)
# ---------------------------------------------------------------------------

def persist_tokens_to_storage(access_token: str, refresh_token: str) -> bool:
    """将 token 写入 Cookie + localStorage，成功返回 True。"""
    if not access_token or not refresh_token:
        return False
    try:
        from streamlit_javascript import st_javascript

        a = json.dumps(access_token)
        r = json.dumps(refresh_token)

        # URL-encode token values for safe cookie storage
        a_cookie = urllib.parse.quote(access_token, safe="")
        r_cookie = urllib.parse.quote(refresh_token, safe="")

        js = (
            # localStorage
            f'localStorage.setItem("{_STORAGE_KEY_ACCESS}", {a}); '
            f'localStorage.setItem("{_STORAGE_KEY_REFRESH}", {r}); '
            # Cookie
            f'document.cookie = "{_COOKIE_KEY_ACCESS}={a_cookie}'
            f";path=/;max-age={_COOKIE_MAX_AGE};SameSite=Lax\"; "
            f'document.cookie = "{_COOKIE_KEY_REFRESH}={r_cookie}'
            f";path=/;max-age={_COOKIE_MAX_AGE};SameSite=Lax\"; "
            'return "ok";'
        )
        st_javascript(js)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------

def clear_tokens_from_storage() -> bool:
    """清除 Cookie + localStorage 中的 token，成功返回 True。"""
    try:
        from streamlit_javascript import st_javascript

        js = (
            # localStorage
            f'localStorage.removeItem("{_STORAGE_KEY_ACCESS}"); '
            f'localStorage.removeItem("{_STORAGE_KEY_REFRESH}"); '
            # Cookie — max-age=0 立即过期
            f'document.cookie = "{_COOKIE_KEY_ACCESS}=;path=/;max-age=0;SameSite=Lax"; '
            f'document.cookie = "{_COOKIE_KEY_REFRESH}=;path=/;max-age=0;SameSite=Lax"; '
            'return "ok";'
        )
        st_javascript(js)
        return True
    except Exception:
        return False
