"""MCP 工具注册：config/mcp.yaml → langchain-mcp-adapters。

- 逐 server 加载 + 超时 + 独立异常捕获：server 挂掉只记 status 跳过，绝不破坏启动
- adapter 未安装（[mcp] extra）→ 空结果 + warning
- MCP 工具并入内核工具列表，走同一 permission_gate / hooks / audit
"""

from __future__ import annotations

import asyncio
import warnings
from dataclasses import dataclass, field
from typing import Any

import yaml

from utils.path import get_config_path


@dataclass
class McpLoadResult:
    tools: list = field(default_factory=list)
    server_status: dict[str, str] = field(default_factory=dict)


def _normalize_transport(value: Any) -> str:
    transport = str(value or "").strip().lower().replace("-", "_")
    if transport in ("http", "streamable_http"):
        return "streamable_http"
    return transport


async def load_mcp_tools_async(force_reload: bool = False) -> McpLoadResult:
    """加载 MCP 工具。配置缺失/禁用/adapter 未安装 → 空结果。"""
    path = get_config_path("mcp.yaml")
    if not path.exists():
        return McpLoadResult()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:
        warnings.warn(f"mcp.yaml 读取失败，跳过 MCP: {exc}")
        return McpLoadResult()
    if not isinstance(raw, dict) or not bool(raw.get("enabled", True)):
        return McpLoadResult()

    servers_cfg = raw.get("servers")
    if not isinstance(servers_cfg, dict) or not servers_cfg:
        return McpLoadResult()

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        warnings.warn("未安装 langchain-mcp-adapters，跳过 MCP（pip install -e '.[mcp]'）")
        return McpLoadResult()

    connections: dict[str, dict] = {}
    status: dict[str, str] = {}
    for name, cfg in servers_cfg.items():
        name = str(name).strip()
        if not name or not isinstance(cfg, dict):
            continue
        transport = _normalize_transport(cfg.get("transport"))
        if transport == "stdio":
            command = str(cfg.get("command", "")).strip()
            if not command:
                status[name] = "error: stdio 缺少 command"
                continue
            conn: dict[str, Any] = {
                "transport": "stdio",
                "command": command,
                "args": [str(a) for a in (cfg.get("args") or [])],
            }
            env = cfg.get("env")
            if isinstance(env, dict) and env:
                conn["env"] = {str(k): str(v) for k, v in env.items()}
            connections[name] = conn
        elif transport in ("sse", "streamable_http"):
            url = str(cfg.get("url", "")).strip()
            if not url:
                status[name] = f"error: {transport} 缺少 url"
                continue
            conn = {"transport": transport, "url": url}
            headers = cfg.get("headers")
            if isinstance(headers, dict) and headers:
                conn["headers"] = {str(k): str(v) for k, v in headers.items()}
            connections[name] = conn
        else:
            status[name] = f"error: 未知 transport {transport!r}"

    if not connections:
        return McpLoadResult([], status)

    try:
        client = MultiServerMCPClient(
            connections,
            tool_name_prefix=bool(raw.get("tool_name_prefix", True)),
            handle_tool_errors=True,
        )
    except Exception as exc:
        warnings.warn(f"MCP 客户端构建失败，跳过: {exc}")
        return McpLoadResult([], status)

    timeout = float(raw.get("connect_timeout_seconds", 15) or 15)
    tools: list = []
    for name in connections:
        try:
            server_tools = await asyncio.wait_for(client.get_tools(server_name=name), timeout)
            tools.extend(server_tools)
            status[name] = "ok"
        except Exception as exc:
            status[name] = f"error: {exc}"

    return McpLoadResult(tools, status)