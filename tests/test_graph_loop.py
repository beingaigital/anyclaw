"""图循环与事件流：终止性、事件顺序、seq 单调、token 流、usage。"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from core.events import EventType
from tests.fake_model import make_tool_call_message, probe_tool


async def test_loop_terminates_and_event_order(make_kernel):
    responses = [
        make_tool_call_message("probe_tool", {"x": "hi"}, "call_1"),
        AIMessage(content="完成：hi"),
    ]
    kernel = await make_kernel(responses, [probe_tool])
    evs = [ev async for ev in kernel.astream("s1", "跑一下探针")]
    types = [ev.type for ev in evs]

    assert types[0] == EventType.RUN_STARTED
    assert types[-1] == EventType.RUN_FINISHED
    assert evs[-1].payload["status"] == "completed"
    assert evs[-1].payload["final_text"] == "完成：hi"

    assert EventType.TOOL_CALL_STARTED in types
    assert EventType.TOOL_RESULT in types
    assert types.index(EventType.TOOL_CALL_STARTED) < types.index(EventType.TOOL_RESULT)
    # 工具结果之后还有模型收尾回复
    assert types.index(EventType.TOOL_RESULT) < len(types) - 1

    # seq 单调递增
    assert [ev.seq for ev in evs] == list(range(len(evs)))
    # 所有事件同一 run_id/session_id
    assert len({ev.run_id for ev in evs}) == 1
    assert all(ev.session_id == "s1" for ev in evs)


async def test_token_deltas_and_message_complete(make_kernel):
    responses = [AIMessage(content="你好世界")]
    kernel = await make_kernel(responses, [])
    evs = [ev async for ev in kernel.astream("s2", "打招呼")]

    deltas = [ev for ev in evs if ev.type == EventType.TOKEN_DELTA]
    assert "".join(ev.payload["delta"] for ev in deltas) == "你好世界"

    completes = [ev for ev in evs if ev.type == EventType.MESSAGE_COMPLETE]
    assert len(completes) == 1
    assert completes[0].payload["content"] == "你好世界"


async def test_usage_accumulates(make_kernel):
    responses = [
        make_tool_call_message("probe_tool", {"x": "a"}, "call_1"),
        AIMessage(content="ok"),
    ]
    # 给脚本响应附 usage_metadata
    responses[0].usage_metadata = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    responses[1].usage_metadata = {"input_tokens": 200, "output_tokens": 5, "total_tokens": 205}
    kernel = await make_kernel(responses, [probe_tool])
    evs = [ev async for ev in kernel.astream("s3", "用工具")]

    usage_evs = [ev for ev in evs if ev.type == EventType.USAGE]
    assert len(usage_evs) == 2
    totals = evs[-1].payload["usage_totals"]
    assert totals["prompt_tokens"] == 300
    assert totals["completion_tokens"] == 15
    assert totals["total_tokens"] == 315


async def test_tool_error_self_corrects(make_kernel):
    responses = [
        make_tool_call_message("no_such_tool", {}, "call_1"),
        AIMessage(content="工具不存在，我直接回答"),
    ]
    kernel = await make_kernel(responses, [probe_tool])
    evs = [ev async for ev in kernel.astream("s4", "调用不存在的工具")]

    results = [ev for ev in evs if ev.type == EventType.TOOL_RESULT]
    assert len(results) == 1
    assert results[0].payload["status"] == "error"
    assert evs[-1].payload["status"] == "completed"