"""历史压缩：prompt-token 触发、RemoveMessage 生效、tool 组不拆。"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, ToolMessage

from core.events import EventType
from tests.fake_model import ScriptedChatModel
from utils.session_manager import get_session_manager


def _seed_long_history(task_id: str) -> None:
    sm = get_session_manager()
    long_text = "这是一段比较长的历史消息内容，用于触发上下文压缩机制。"
    for i in range(12):
        sm.add_message(task_id, "user", f"{long_text}（问题{i}）")
        sm.add_message(task_id, "assistant", f"{long_text}（回答{i}）")
    # 一组 tool 调用（配对完整）
    sm.add_message(
        task_id,
        "assistant",
        "",
        tool_calls=[{"name": "probe_tool", "args": {"x": "old"}, "id": "old_call_1"}],
    )
    sm.add_message(task_id, "tool", '{"ok": true}', tool_name="probe_tool", tool_call_id="old_call_1")


async def test_compression_triggers_and_keeps_pairing(make_kernel, tmp_path, monkeypatch):
    # 小阈值触发压缩
    (tmp_path / "config" / "kernel.yaml").write_text(
        "max_context_tokens: 50\nkeep_recent_messages: 4\nrecursion_limit: 50\n",
        encoding="utf-8",
    )
    # 摘要模型替换为 fake
    import utils.message_utils as mu

    monkeypatch.setattr(
        mu, "get_text_generation_model", lambda: ScriptedChatModel(responses=[AIMessage(content="这是摘要")])
    )

    sm = get_session_manager()
    task_id = sm.create_session("长会话")
    _seed_long_history(task_id)

    kernel = await make_kernel([AIMessage(content="压缩后的回答")], [])
    evs = [ev async for ev in kernel.astream(task_id, "新问题")]
    assert evs[-1].payload["status"] == "completed"

    comps = [ev for ev in evs if ev.type == EventType.COMPRESSION]
    assert len(comps) == 1
    assert comps[0].payload["summary"] == "这是摘要"
    assert comps[0].payload["original_count"] > comps[0].payload["kept_count"]

    history = await kernel.get_history(task_id)
    # 摘要 + 最近窗口 + 新 user + 新 assistant
    assert str(history[0].content).startswith("[历史对话摘要]")
    assert len(history) < 28
    # tool 组完整：不存在孤儿 ToolMessage
    for i, msg in enumerate(history):
        if isinstance(msg, ToolMessage):
            prev = history[i - 1]
            assert isinstance(prev, AIMessage) and prev.tool_calls
            assert msg.tool_call_id in {tc["id"] for tc in prev.tool_calls}


async def test_no_compression_under_threshold(make_kernel, tmp_path):
    (tmp_path / "config" / "kernel.yaml").write_text(
        "max_context_tokens: 100000\nkeep_recent_messages: 4\n", encoding="utf-8"
    )
    kernel = await make_kernel([AIMessage(content="短回答")], [])
    evs = [ev async for ev in kernel.astream("s1", "短问题")]
    assert not [ev for ev in evs if ev.type == EventType.COMPRESSION]