"""STM JSON 投影：checkpoint 是会话真相源，memory/STM/*.json 是内核单写者投影。

投影保持既有 schema，使 cli/session_ui.py、clear_utils、LTM 等继续工作。
CLI/Web 不再直接写会话消息——所有持久化由内核在 run 段结束时统一完成。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from utils.session_manager import get_session_manager


def messages_to_session_dicts(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """BaseMessage 列表 → 既有 session messages schema。"""
    result: list[dict[str, Any]] = []
    now = datetime.now().isoformat()
    for msg in messages:
        if isinstance(msg, SystemMessage):
            continue
        if isinstance(msg, HumanMessage):
            result.append({"role": "user", "content": str(msg.content), "timestamp": now})
        elif isinstance(msg, AIMessage):
            tool_calls_data = []
            for tc in getattr(msg, "tool_calls", None) or []:
                if isinstance(tc, dict):
                    tool_calls_data.append(tc)
                else:
                    tool_calls_data.append(
                        {"name": getattr(tc, "name", ""), "args": getattr(tc, "args", {}), "id": getattr(tc, "id", "")}
                    )
            item: dict[str, Any] = {
                "role": "assistant",
                "content": str(msg.content or ""),
                "timestamp": now,
            }
            if tool_calls_data:
                item["tool_calls"] = tool_calls_data
            result.append(item)
        elif isinstance(msg, ToolMessage):
            result.append(
                {
                    "role": "tool",
                    "content": str(msg.content),
                    "tool_name": getattr(msg, "name", None) or "unknown",
                    "tool_call_id": msg.tool_call_id,
                    "timestamp": now,
                }
            )
    return result


def ensure_projection(session_id: str, initial_query: str) -> None:
    """确保指定 session_id 的投影存在（内核用调用方给定的 id，而非新建 uuid）。"""
    sm = get_session_manager()
    if sm.load_session(session_id) is not None:
        return
    now = datetime.now().isoformat()
    query = (initial_query or "").strip()
    data = {
        "task_id": session_id,
        "created_at": now,
        "updated_at": now,
        "description": f"初始对话：{query[:50]}" if query else "新会话",
        "initial_query": query,
        "messages": [],
        "token_usage": {
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "steps": [],
        },
    }
    sm.save_session(session_id, data)


def write_projection(
    session_id: str,
    messages: list[BaseMessage],
    *,
    usage_steps: list[dict[str, Any]] | None = None,
    usage_totals: dict[str, int] | None = None,
    final_query: str | None = None,
) -> None:
    """run 段结束时写投影：消息全量替换 + token 累计增量 + 首轮描述更新。"""
    sm = get_session_manager()
    ensure_projection(session_id, final_query or "")
    data = sm.load_session(session_id)
    if data is None:
        return

    data["messages"] = messages_to_session_dicts(messages)

    usage = data.get("token_usage") or {}
    steps = list(usage.get("steps") or [])
    totals = usage_totals or {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        usage[key] = int(usage.get(key, 0) or 0) + int(totals.get(key, 0) or 0)
    if usage_steps:
        steps.extend(usage_steps)
    usage["steps"] = steps
    data["token_usage"] = usage

    # 首个完成轮：把占位描述更新为任务描述（保留旧 CLI 的"关键修复"行为）
    description = str(data.get("description", ""))
    if final_query and description.startswith("初始对话"):
        sm.save_session(session_id, data, final_query=final_query)
    else:
        sm.save_session(session_id, data)