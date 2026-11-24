"""统一事件 schema：CLI 渲染器与 Web SSE 消费同一套事件，零分叉。

内核是事件的唯一写者：图节点通过 stream writer 发出原始 payload，
由 AgentKernel 统一分配 seq/ts/run_id 并封装为 Event。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """事件类型。"""

    RUN_STARTED = "run_started"
    TOKEN_DELTA = "token_delta"
    MESSAGE_COMPLETE = "message_complete"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_RESULT = "tool_result"
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_RESOLVED = "permission_resolved"
    COMPRESSION = "compression"
    USAGE = "usage"
    RUN_FINISHED = "run_finished"
    ERROR = "error"


class Event(BaseModel):
    """统一事件信封。SSE 序列化直接用 model_dump_json()。"""

    type: EventType
    session_id: str
    run_id: str
    seq: int = 0
    ts: datetime = Field(default_factory=datetime.now)
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# payload 构造器：约定各事件的 payload 形状
# ---------------------------------------------------------------------------


def token_delta(message_id: str, delta: str) -> dict[str, Any]:
    """模型流式增量（仅 agent 节点的输出）。"""
    return {"message_id": message_id, "delta": delta}


def message_complete(message_id: str, content: str, tool_calls: list | None = None) -> dict[str, Any]:
    """一条完整 AIMessage。"""
    return {"message_id": message_id, "content": content, "tool_calls": tool_calls or []}


def tool_call_started(tool_call_id: str, tool_name: str, args: dict) -> dict[str, Any]:
    """工具开始执行（已通过权限与 pre-hook）。"""
    return {"tool_call_id": tool_call_id, "tool_name": tool_name, "args": args}


def tool_result(
    tool_call_id: str,
    tool_name: str,
    result: str,
    status: str = "ok",
    duration_ms: int = 0,
) -> dict[str, Any]:
    """工具执行结果。status: ok | error | denied | blocked。"""
    return {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "result": result,
        "status": status,
        "duration_ms": duration_ms,
    }


def permission_request(
    interrupt_id: str,
    tool_call_id: str,
    tool_name: str,
    args: dict,
    tier: str,
    reason: str,
) -> dict[str, Any]:
    """ask 级工具等待用户确认（interrupt 挂起）。"""
    return {
        "interrupt_id": interrupt_id,
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "args": args,
        "tier": tier,
        "reason": reason,
    }


def permission_resolved(tool_call_id: str, action: str, remember: bool = False) -> dict[str, Any]:
    """用户确认结果。action: allow | deny。"""
    return {"tool_call_id": tool_call_id, "action": action, "remember": remember}


def compression(original_count: int, kept_count: int, summary: str) -> dict[str, Any]:
    """历史压缩通知。"""
    return {"original_count": original_count, "kept_count": kept_count, "summary": summary}


def usage(step: str, input_tokens: int, output_tokens: int, total_tokens: int) -> dict[str, Any]:
    """单次模型调用的 token 消耗。"""
    return {
        "step": step,
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def run_finished(
    status: str,
    usage_totals: dict | None = None,
    final_text: str = "",
) -> dict[str, Any]:
    """run 段结束。status: completed | awaiting_permission | error。"""
    return {"status": status, "usage_totals": usage_totals or {}, "final_text": final_text}


def error(message: str, kind: str = "unknown") -> dict[str, Any]:
    """错误事件。kind: recursion_limit | model_error | tool_error | unknown。"""
    return {"message": message, "kind": kind}