"""任务上下文：TASK_ID 由内核在 astream 前设置（ContextVar），供各工具读取。

内核为 async-native：LangGraph 节点与工具运行在同一事件循环的任务树中，
ContextVar 自然传播；同步 @tool 经 run_in_executor 的 copy_context 也能看到。
旧版的线程 ID 全局兜底已随线程桥接一并移除。
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

# 当前任务 ID，由 AgentKernel.astream 设置，供 tools 写入 sandbox/任务ID
TASK_ID_CTX: ContextVar[Optional[str]] = ContextVar("task_id", default=None)


def get_task_id() -> Optional[str]:
    """获取当前任务ID，不存在则返回 None。"""
    return TASK_ID_CTX.get()


def set_task_id(task_id: Optional[str]) -> None:
    """设置当前任务ID。"""
    TASK_ID_CTX.set(task_id)