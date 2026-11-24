"""Checkpoint 持久化：跨内核实例恢复 interrupt、旧 JSON 会话种子恢复。"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from core.events import EventType
from core.harness import PermissionPolicy
from tests.fake_model import make_tool_call_message, probe_tool
from utils.session_manager import get_session_manager


async def test_resume_across_kernel_instances(make_kernel, tmp_path):
    db_path = tmp_path / "shared.sqlite"
    policy = PermissionPolicy(default_tier="ask", tool_tiers={"probe_tool": "ask"})

    kernel1 = await make_kernel(
        [make_tool_call_message("probe_tool", {"x": "hi"}, "call_1")],
        [probe_tool],
        policy=policy,
        db_path=db_path,
    )
    evs = [ev async for ev in kernel1.astream("s1", "跑探针")]
    assert evs[-1].payload["status"] == "awaiting_permission"
    interrupt_id = [ev for ev in evs if ev.type == EventType.PERMISSION_REQUEST][0].payload["interrupt_id"]
    await kernel1.close()

    # 第二个内核实例（新连接、新模型脚本）恢复同一会话
    kernel2 = await make_kernel(
        [AIMessage(content="恢复后完成")],
        [probe_tool],
        policy=policy,
        db_path=db_path,
    )
    pending = await kernel2.get_pending_interrupt("s1")
    assert pending is not None
    assert pending["tool_name"] == "probe_tool"

    evs2 = [ev async for ev in kernel2.resume("s1", {"action": "allow"}, interrupt_id)]
    assert evs2[-1].payload["status"] == "completed"
    assert evs2[-1].payload["final_text"] == "恢复后完成"

    history = await kernel2.get_history("s1")
    # user + AIMessage(tool_calls) + ToolMessage + 收尾 AIMessage
    assert len(history) == 4
    await kernel2.close()


async def test_legacy_json_session_seeding(make_kernel):
    sm = get_session_manager()
    task_id = sm.create_session("旧会话")
    sm.add_message(task_id, "user", "旧问题")
    sm.add_message(task_id, "assistant", "旧回答")

    kernel = await make_kernel([AIMessage(content="新回答")], [])
    evs = [ev async for ev in kernel.astream(task_id, "新问题")]
    assert evs[-1].payload["status"] == "completed"

    # fake model 收到的输入应包含旧历史
    model = kernel._model
    received = model.received[-1]
    texts = [str(m.content) for m in received]
    assert "旧问题" in texts
    assert "旧回答" in texts
    assert "新问题" in texts

    history = await kernel.get_history(task_id)
    assert len(history) == 4  # 旧 user/assistant + 新 user/assistant