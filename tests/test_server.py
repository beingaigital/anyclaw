"""Web 服务端：SSE chat、权限 resume、pending 恢复、投影共享（CLI 可恢复）。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from server.app import create_app
from tests.fake_model import ScriptedChatModel, make_tool_call_message, probe_tool
from utils.session_manager import get_session_manager


def _patch_model(monkeypatch, responses, tools=None):
    import model.factory as mf
    import core.kernel as ck

    monkeypatch.setattr(mf, "get_react_model", lambda: ScriptedChatModel(responses=list(responses)))
    monkeypatch.setattr(ck, "load_agent_tools", lambda force_reload=False: list(tools or []))


def _collect_sse(client, url, body):
    frames = []
    with client.stream("POST", url, json=body) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line.startswith("data: "):
                frames.append(json.loads(line[6:]))
    return frames


def test_health_and_sessions_crud(tmp_path, monkeypatch):
    _patch_model(monkeypatch, [AIMessage(content="ok")])
    with TestClient(create_app()) as client:
        assert client.get("/api/health").json()["ok"]
        sid = client.post("/api/sessions", json={"initial_query": "测试"}).json()["task_id"]
        sessions = client.get("/api/sessions").json()["sessions"]
        assert any(s["task_id"] == sid for s in sessions)
        assert client.get(f"/api/sessions/{sid}").json()["task_id"] == sid
        assert client.get(f"/api/sessions/{sid}/messages").json()["messages"] == []
        assert client.delete(f"/api/sessions/{sid}").json()["ok"]
        assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_chat_sse_streams_and_projects(tmp_path, monkeypatch):
    _patch_model(monkeypatch, [AIMessage(content="你好世界")])
    with TestClient(create_app()) as client:
        sid = client.post("/api/sessions", json={}).json()["task_id"]
        frames = _collect_sse(client, f"/api/sessions/{sid}/chat", {"message": "打招呼"})
        types = [f["type"] for f in frames]
        assert types[0] == "run_started"
        assert types[-1] == "run_finished"
        assert frames[-1]["payload"]["status"] == "completed"
        assert frames[-1]["payload"]["final_text"] == "你好世界"
        assert "token_delta" in types

        # 投影已写：CLI 的 SessionManager 能看到同一会话
        data = get_session_manager().load_session(sid)
        assert data is not None
        assert [m["role"] for m in data["messages"]] == ["user", "assistant"]


def test_permission_flow_and_pending_restore(tmp_path, monkeypatch):
    (tmp_path / "config" / "permissions.yaml").write_text(
        "default_tier: ask\ntools:\n  probe_tool: ask\n", encoding="utf-8"
    )
    _patch_model(
        monkeypatch,
        [
            make_tool_call_message("probe_tool", {"x": "hi"}, "call_1"),
            AIMessage(content="已执行"),
        ],
        tools=[probe_tool],
    )
    with TestClient(create_app()) as client:
        sid = client.post("/api/sessions", json={}).json()["task_id"]
        frames = _collect_sse(client, f"/api/sessions/{sid}/chat", {"message": "跑探针"})
        assert frames[-1]["payload"]["status"] == "awaiting_permission"
        req = [f for f in frames if f["type"] == "permission_request"][0]
        interrupt_id = req["payload"]["interrupt_id"]

        # 刷新页面场景：GET session 恢复 pending
        detail = client.get(f"/api/sessions/{sid}").json()
        assert detail["pending_interrupt"]["interrupt_id"] == interrupt_id

        # 允许 → resume SSE
        frames2 = _collect_sse(
            client,
            f"/api/sessions/{sid}/permission",
            {"action": "allow", "remember": False, "interrupt_id": interrupt_id},
        )
        assert frames2[-1]["payload"]["status"] == "completed"
        results = [f for f in frames2 if f["type"] == "tool_result"]
        assert results[0]["payload"]["status"] == "ok"

        # pending 已清空
        assert client.get(f"/api/sessions/{sid}").json()["pending_interrupt"] is None


def test_status_endpoints(tmp_path, monkeypatch):
    _patch_model(monkeypatch, [AIMessage(content="ok")])
    with TestClient(create_app()) as client:
        assert "enabled" in client.get("/api/tools").json()
        assert "models" in client.get("/api/models").json()
        assert "sections" in client.get("/api/profile").json()
        assert "skills" in client.get("/api/skills").json()
        assert client.post("/api/tools/reload").status_code == 200