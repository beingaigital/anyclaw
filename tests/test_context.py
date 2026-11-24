"""任务上下文：同步 @tool 在 async 内核下经 ContextVar 拿到 task_id（无线程桥接）。"""

from __future__ import annotations

import json
import threading

from langchain_core.messages import AIMessage

from core.events import EventType
from tests.fake_model import make_tool_call_message, probe_tool, sync_context_probe


async def test_sync_tool_sees_task_id(make_kernel):
    responses = [
        make_tool_call_message("probe_tool", {"x": "hi"}, "call_1"),
        AIMessage(content="ok"),
    ]
    kernel = await make_kernel(responses, [probe_tool])
    evs = [ev async for ev in kernel.astream("session-abc", "跑探针")]

    results = [ev for ev in evs if ev.type == EventType.TOOL_RESULT]
    payload = json.loads(results[0].payload["result"])
    assert payload["task_id"] == "session-abc"


async def test_context_propagates_across_executor(make_kernel):
    main_thread = threading.get_ident()
    responses = [
        make_tool_call_message("sync_context_probe", {}, "call_1"),
        AIMessage(content="ok"),
    ]
    kernel = await make_kernel(responses, [sync_context_probe])
    evs = [ev async for ev in kernel.astream("session-xyz", "跑同步探针")]

    results = [ev for ev in evs if ev.type == EventType.TOOL_RESULT]
    payload = json.loads(results[0].payload["result"])
    # 同步工具可能跑在 executor 线程，但 ContextVar 经 copy_context 传播
    assert payload["task_id"] == "session-xyz"