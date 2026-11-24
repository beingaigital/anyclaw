"""MCP：stdio 加载带前缀、坏 server 不影响启动、MCP 工具过 ask 权限门。"""

from __future__ import annotations

import sys
from pathlib import Path

from langchain_core.messages import AIMessage

from core.events import EventType
from core.harness import PermissionPolicy
from tests.fake_model import make_tool_call_message
from utils.mcp_registry import load_mcp_tools_async

_SERVER = str(Path(__file__).resolve().parent / "mcp_math_server.py")


def _write_mcp_yaml(project_env, body: str) -> None:
    (project_env / "config" / "mcp.yaml").write_text(body, encoding="utf-8")


async def test_stdio_loads_with_prefix(project_env):
    _write_mcp_yaml(
        project_env,
        f"""
enabled: true
tool_name_prefix: true
servers:
  math:
    transport: stdio
    command: "{sys.executable}"
    args: ["{_SERVER}"]
""",
    )
    result = await load_mcp_tools_async()
    assert result.server_status.get("math") == "ok"
    assert any(t.name == "math_add" for t in result.tools)


async def test_down_server_skipped(project_env):
    _write_mcp_yaml(
        project_env,
        """
enabled: true
connect_timeout_seconds: 2
servers:
  bad:
    transport: streamable_http
    url: http://127.0.0.1:1/mcp
""",
    )
    result = await load_mcp_tools_async()
    assert result.server_status.get("bad", "").startswith("error")
    assert result.tools == []


async def test_disabled_returns_empty(project_env):
    _write_mcp_yaml(project_env, "enabled: false\nservers: {}\n")
    result = await load_mcp_tools_async()
    assert result.tools == [] and result.server_status == {}


async def test_mcp_tool_through_ask_gate(make_kernel, project_env):
    _write_mcp_yaml(
        project_env,
        f"""
enabled: true
tool_name_prefix: true
servers:
  math:
    transport: stdio
    command: "{sys.executable}"
    args: ["{_SERVER}"]
""",
    )
    result = await load_mcp_tools_async()
    assert result.tools, "MCP math server 应加载成功"

    policy = PermissionPolicy(default_tier="ask")
    policy.mcp_tool_names = {t.name for t in result.tools}

    kernel = await make_kernel(
        [
            make_tool_call_message("math_add", {"a": 1, "b": 2}, "call_1"),
            AIMessage(content="结果是 3"),
        ],
        result.tools,
        policy=policy,
    )
    evs = [ev async for ev in kernel.astream("s1", "1+2 等于几")]
    assert evs[-1].payload["status"] == "awaiting_permission"
    reqs = [ev for ev in evs if ev.type == EventType.PERMISSION_REQUEST]
    assert reqs[0].payload["tool_name"] == "math_add"

    evs2 = [
        ev
        async for ev in kernel.resume("s1", {"action": "allow"}, reqs[0].payload["interrupt_id"])
    ]
    assert evs2[-1].payload["status"] == "completed"
    results = [ev for ev in evs2 if ev.type == EventType.TOOL_RESULT]
    assert results[0].payload["status"] == "ok"
    assert "3" in results[0].payload["result"]