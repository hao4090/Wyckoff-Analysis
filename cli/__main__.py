# -*- coding: utf-8 -*-
"""
威科夫终端读盘室 — 入口。

用法:
    wyckoff                # 启动 TUI
    wyckoff update         # 升级到最新版
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from dotenv import load_dotenv

# 加载 .env（项目根目录）
load_dotenv()

# 抑制 Streamlit 在非 Streamlit 环境下的全部日志
os.environ["STREAMLIT_LOG_LEVEL"] = "error"
import logging as _logging


def _silence_streamlit():
    for name in list(_logging.Logger.manager.loggerDict):
        if name.startswith("streamlit"):
            lg = _logging.getLogger(name)
            lg.handlers.clear()
            lg.setLevel(_logging.CRITICAL)
            lg.propagate = False


try:
    import streamlit  # noqa: F401
except Exception:
    pass
_silence_streamlit()

# CLI 环境：只显示 CRITICAL，不泄漏 traceback 给用户
import warnings as _warnings
_warnings.filterwarnings("ignore", category=DeprecationWarning)
_warnings.filterwarnings("ignore", category=ResourceWarning)
_logging.basicConfig(level=_logging.CRITICAL)


# ---------------------------------------------------------------------------
# Provider 工厂
# ---------------------------------------------------------------------------

def _create_provider(provider_name: str, api_key: str, model: str = "", base_url: str = ""):
    from cli.providers import PROVIDERS
    import inspect

    cls = PROVIDERS.get(provider_name)
    if cls is None:
        install_hints = {
            "gemini": "pip install google-genai",
            "claude": "pip install anthropic",
            "openai": "pip install openai",
        }
        hint = install_hints.get(provider_name, "")
        return None, f"Provider '{provider_name}' 不可用，请先安装依赖：{hint}"

    kwargs = {"api_key": api_key}
    if model:
        kwargs["model"] = model
    if base_url:
        kwargs["base_url"] = base_url

    sig = inspect.signature(cls.__init__)
    kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}

    return cls(**kwargs), None


def _do_update():
    import shutil
    print("正在升级 youngcan-wyckoff-analysis ...")
    pkg = "youngcan-wyckoff-analysis"
    uv = shutil.which("uv")
    if uv:
        cmd = [uv, "pip", "install", "--python", sys.executable, "--upgrade", pkg]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", pkg]
    try:
        subprocess.check_call(cmd)
        print("\n升级完成！请重新运行 wyckoff。")
    except subprocess.CalledProcessError as e:
        print(f"\n升级失败: {e}")
    sys.exit(0)


def _get_version() -> str:
    try:
        from importlib.metadata import version
        return version("youngcan-wyckoff-analysis")
    except Exception:
        return "dev"


def main():
    parser = argparse.ArgumentParser(
        prog="wyckoff",
        description="威科夫终端读盘室 — Wyckoff 量价分析 Agent",
    )
    parser.add_argument("-v", "--version", action="version", version=f"wyckoff {_get_version()}")
    parser.add_argument(
        "command", nargs="?", default=None,
        help="子命令: update（升级到最新版）",
    )
    args = parser.parse_args()

    if args.command == "update":
        _do_update()
    elif args.command is not None:
        print(f"未知命令: {args.command}")
        print("可用命令: wyckoff update")
        sys.exit(1)

    # --- 初始化：Auth + Tools + Provider ---
    from cli.tools import ToolRegistry
    tools = ToolRegistry()

    session_expired = False
    try:
        from cli.auth import restore_session, _load_session
        had_session = _load_session() is not None
        session = restore_session()
        if session:
            tools.state.update({
                "user_id": session["user_id"],
                "email": session["email"],
                "access_token": session.get("access_token", ""),
                "refresh_token": session.get("refresh_token", ""),
            })
            from core.stock_cache import set_cli_tokens
            set_cli_tokens(session.get("access_token", ""), session.get("refresh_token", ""))
        elif had_session:
            session_expired = True
    except Exception:
        pass

    from core.prompts import CHAT_AGENT_SYSTEM_PROMPT
    system_prompt = CHAT_AGENT_SYSTEM_PROMPT

    state = {
        "provider": None,
        "provider_name": "",
        "model": "",
        "api_key": "",
        "base_url": "",
    }

    try:
        from cli.auth import load_model_configs, load_default_model_id
        configs = load_model_configs()
        default_id = load_default_model_id()
        if configs and default_id:
            # 为所有配置设置环境变量（工具可能读取）
            env_map = {"gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
            for cfg in configs:
                ek = env_map.get(cfg.get("provider_name", ""))
                if ek and cfg.get("api_key"):
                    os.environ.setdefault(ek, cfg["api_key"])
            default_cfg = next((c for c in configs if c["id"] == default_id), configs[0])
            if len(configs) == 1:
                provider, err = _create_provider(
                    default_cfg["provider_name"], default_cfg["api_key"],
                    default_cfg.get("model", ""), default_cfg.get("base_url", ""),
                )
                if not err:
                    state.update(default_cfg)
                    state["provider"] = provider
            else:
                from cli.providers.fallback import FallbackProvider
                state.update(default_cfg)
                state["provider"] = FallbackProvider(configs, default_id)
    except Exception:
        pass

    # --- 启动 TUI ---
    from cli.tui import WyckoffTUI
    app = WyckoffTUI(
        provider=state["provider"],
        tools=tools,
        state=state,
        system_prompt=system_prompt,
        session_expired=session_expired,
    )
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        # 退出时静默：关闭第三方连接 + OS 级重定向抑制 daemon 线程垃圾输出
        try:
            import baostock as bs
            bs.logout()
        except Exception:
            pass
        # os.dup2 在 OS 文件描述符层面重定向，不受 Python GC 影响
        try:
            _devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(_devnull, 1)  # stdout fd
            os.dup2(_devnull, 2)  # stderr fd
            os.close(_devnull)
        except Exception:
            pass


if __name__ == "__main__":
    main()
