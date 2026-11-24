"""会话管理路由：列表 / 创建 / 详情（含 pending 权限确认）/ 消息 / 删除。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from utils.path import ensure_task_dirs, get_stm_dir
from utils.session_manager import get_session_manager

router = APIRouter(prefix="/api", tags=["sessions"])


class CreateSessionBody(BaseModel):
    initial_query: str = "新会话"


def _summary(data: dict) -> dict:
    return {
        "task_id": data.get("task_id"),
        "description": data.get("description", ""),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "message_count": len(data.get("messages", [])),
    }


@router.get("/sessions")
def list_sessions(limit: int = 20):
    sm = get_session_manager()
    return {"sessions": [_summary(s) for s in sm.list_sessions(limit=limit)]}


@router.post("/sessions")
def create_session(body: CreateSessionBody):
    sm = get_session_manager()
    task_id = sm.create_session(body.initial_query)
    ensure_task_dirs(task_id)
    return {"task_id": task_id}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    sm = get_session_manager()
    data = sm.load_session(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    kernel = request.app.state.kernel
    pending = await kernel.get_pending_interrupt(session_id)
    return {**_summary(data), "pending_interrupt": pending}


@router.get("/sessions/{session_id}/messages")
def get_messages(session_id: str):
    sm = get_session_manager()
    data = sm.load_session(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"messages": data.get("messages", []), "token_usage": data.get("token_usage", {})}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    path = get_stm_dir() / f"{session_id}.json"
    if path.exists():
        path.unlink()
    kernel = request.app.state.kernel
    try:
        await kernel._saver.adelete_thread(session_id)
    except Exception:
        pass
    return {"ok": True}