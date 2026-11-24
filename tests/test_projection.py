"""STM JSON 投影：schema 兼容、往返恢复、会话列表、描述更新。"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from tests.fake_model import make_tool_call_message, probe_tool
from utils.message_utils import messages_from_session_data
from utils.session_manager import get_session_manager


async def test_projection_schema_and_roundtrip(make_kernel, tmp_path):
    responses = [
        make_tool_call_message("probe_tool", {"x": "hi"}, "call_1"),
        AIMessage(content="最终回答"),
    ]
    responses[1].usage_metadata = {"input_tokens": 50, "output_tokens": 8, "total_tokens": 58}
    kernel = await make_kernel(responses, [probe_tool])
    [ev async for ev in kernel.astream("s1", "初始问题")]

    sm = get_session_manager()
    data = sm.load_session("s1")
    assert data is not None
    assert data["task_id"] == "s1"
    assert data["description"].startswith("任务：")
    roles = [m["role"] for m in data["messages"]]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert data["messages"][1]["tool_calls"][0]["id"] == "call_1"
    assert data["messages"][2]["tool_call_id"] == "call_1"
    assert data["token_usage"]["total_tokens"] == 58
    assert data["token_usage"]["steps"]

    # 往返恢复
    restored = messages_from_session_data(data)
    assert len(restored) == 4

    # 会话列表可见
    sessions = sm.list_sessions(limit=5)
    assert any(s["task_id"] == "s1" for s in sessions)

    # 投影文件位于隔离根下
    assert (tmp_path / "memory" / "STM" / "s1.json").exists()


async def test_ltm_written_after_turn(make_kernel, tmp_path):
    kernel = await make_kernel([AIMessage(content="值得记住的回答")], [])
    [ev async for ev in kernel.astream("s1", "独特问题甲")]

    ltm_file = tmp_path / "memory" / "LTM" / "turn_memory.jsonl"
    assert ltm_file.exists()
    content = ltm_file.read_text(encoding="utf-8")
    assert "独特问题甲" in content
    assert "值得记住的回答" in content