"""Harness 权限：ask interrupt/resume、never 拦截、审计、gate 幂等、会话 allowlist。"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage

from core.events import EventType
from core.harness import PermissionPolicy
from tests.fake_model import make_tool_call_message, probe_tool


def _ask_policy():
    return PermissionPolicy(default_tier="ask", tool_tiers={"probe_tool": "ask"})


async def test_ask_interrupts_then_allow(make_kernel):
    responses = [
        make_tool_call_message("probe_tool", {"x": "hi"}, "call_1"),
        AIMessage(content="已执行"),
    ]
    kernel = await make_kernel(responses, [probe_tool], policy=_ask_policy())

    evs = [ev async for ev in kernel.astream("s1", "跑探针")]
    assert evs[-1].payload["status"] == "awaiting_permission"
    reqs = [ev for ev in evs if ev.type == EventType.PERMISSION_REQUEST]
    assert len(reqs) == 1
    assert reqs[0].payload["tool_name"] == "probe_tool"
    interrupt_id = reqs[0].payload["interrupt_id"]
    assert interrupt_id

    # 工具尚未执行
    assert not [ev for ev in evs if ev.type == EventType.TOOL_RESULT]

    evs2 = [
        ev
        async for ev in kernel.resume("s1", {"action": "allow", "remember": False}, interrupt_id)
    ]
    assert evs2[-1].payload["status"] == "completed"
    results = [ev for ev in evs2 if ev.type == EventType.TOOL_RESULT]
    assert len(results) == 1
    assert results[0].payload["status"] == "ok"
    assert [ev for ev in evs2 if ev.type == EventType.PERMISSION_RESOLVED][0].payload["action"] == "allow"


async def test_ask_deny_gives_model_another_turn(make_kernel):
    responses = [
        make_tool_call_message("probe_tool", {"x": "hi"}, "call_1"),
        AIMessage(content="好的，不执行了"),
    ]
    kernel = await make_kernel(responses, [probe_tool], policy=_ask_policy())

    evs = [ev async for ev in kernel.astream("s1", "跑探针")]
    interrupt_id = [ev for ev in evs if ev.type == EventType.PERMISSION_REQUEST][0].payload["interrupt_id"]

    evs2 = [ev async for ev in kernel.resume("s1", {"action": "deny"}, interrupt_id)]
    assert evs2[-1].payload["status"] == "completed"
    assert evs2[-1].payload["final_text"] == "好的，不执行了"
    results = [ev for ev in evs2 if ev.type == EventType.TOOL_RESULT]
    assert results[0].payload["status"] == "denied"
    assert "用户拒绝" in results[0].payload["result"]


async def test_never_blocks_without_interrupt(make_kernel):
    policy = PermissionPolicy(default_tier="always", tool_tiers={"probe_tool": "never"})
    responses = [
        make_tool_call_message("probe_tool", {"x": "hi"}, "call_1"),
        AIMessage(content="被拦截了"),
    ]
    kernel = await make_kernel(responses, [probe_tool], policy=policy)

    evs = [ev async for ev in kernel.astream("s1", "跑探针")]
    assert evs[-1].payload["status"] == "completed"
    assert not [ev for ev in evs if ev.type == EventType.PERMISSION_REQUEST]
    results = [ev for ev in evs if ev.type == EventType.TOOL_RESULT]
    assert results[0].payload["status"] == "blocked"


async def test_audit_written_and_deduped(make_kernel, tmp_path):
    kernel = await make_kernel(
        [
            make_tool_call_message("probe_tool", {"x": "hi"}, "call_1"),
            AIMessage(content="好的"),
        ],
        [probe_tool],
        policy=_ask_policy(),
    )
    evs = [ev async for ev in kernel.astream("s1", "跑探针")]
    interrupt_id = [ev for ev in evs if ev.type == EventType.PERMISSION_REQUEST][0].payload["interrupt_id"]
    [ev async for ev in kernel.resume("s1", {"action": "deny"}, interrupt_id)]

    audit_files = list((tmp_path / "memory" / "audit").glob("*.jsonl"))
    assert audit_files
    entries = [
        json.loads(line)
        for f in audit_files
        for line in f.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    decisions = [
        e
        for e in entries
        if e.get("kind") == "decision" and e.get("tool_call_id") == "call_1"
    ]
    # gate 重跑（resume）不得产生重复决策记录
    assert len(decisions) == 1
    assert decisions[0]["action"] == "deny"
    assert decisions[0]["tier"] == "ask"
    # 结果记录存在
    assert [e for e in entries if e.get("kind") == "result" and e.get("status") == "denied"]


async def test_remember_allowlists_for_session(make_kernel):
    responses = [
        make_tool_call_message("probe_tool", {"x": "1"}, "call_1"),
        AIMessage(content="第一次完成"),
        make_tool_call_message("probe_tool", {"x": "2"}, "call_2"),
        AIMessage(content="第二次完成"),
    ]
    kernel = await make_kernel(responses, [probe_tool], policy=_ask_policy())

    evs = [ev async for ev in kernel.astream("s1", "第一次")]
    interrupt_id = [ev for ev in evs if ev.type == EventType.PERMISSION_REQUEST][0].payload["interrupt_id"]
    [ev async for ev in kernel.resume("s1", {"action": "allow", "remember": True}, interrupt_id)]

    # 第二次调用：会话 allowlist 生效，不再 interrupt
    evs2 = [ev async for ev in kernel.astream("s1", "第二次")]
    assert evs2[-1].payload["status"] == "completed"
    assert not [ev for ev in evs2 if ev.type == EventType.PERMISSION_REQUEST]
    results = [ev for ev in evs2 if ev.type == EventType.TOOL_RESULT]
    assert results[0].payload["status"] == "ok"


async def test_busy_session_rejected(make_kernel):
    from core.kernel import SessionBusyError

    kernel = await make_kernel([AIMessage(content="ok")], [])
    kernel._active["s1"] = True
    try:
        gen = kernel.astream("s1", "hi")
        try:
            await gen.__anext__()
            raise AssertionError("应当抛出 SessionBusyError")
        except SessionBusyError:
            pass
        finally:
            await gen.aclose()
    finally:
        kernel._active.pop("s1", None)