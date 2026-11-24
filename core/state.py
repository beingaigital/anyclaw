"""AgentState：LangGraph 图状态定义。

设计要点：
- messages 用 add_messages reducer，checkpoint 持久化，是会话真相源。
- system_prompt / context 为每轮覆盖的临时上下文键，**不进入 messages**，
  避免 SystemMessage 在 checkpoint 里无限累积。
- approved_calls / rejected 是 permission_gate → tools 的普通传递键（last-value-wins）。
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


def merge_usage(left: dict | None, right: dict | None) -> dict:
    """usage reducer：累加 prompt/completion/total。"""
    left = left or {}
    right = right or {}
    merged = dict(left)
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        merged[key] = int(left.get(key, 0) or 0) + int(right.get(key, 0) or 0)
    return merged


class AgentState(TypedDict, total=False):
    """图状态。total=False：节点只返回增量键。"""

    messages: Annotated[list[BaseMessage], add_messages]
    task_id: str
    summary: str
    system_prompt: str
    context: str
    usage: Annotated[dict, merge_usage]
    session_allowlist: list[str]
    approved_calls: list[dict[str, Any]]
    rejected: list[dict[str, Any]]