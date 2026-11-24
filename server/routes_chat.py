"""对话路由：POST chat → SSE 事件流；POST permission → resume 事件流。

与 CLI 消费同一 Event schema，零分叉。客户端断开 → 取消 run（checkpoint 保留部分状态）。
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core import events
from core.events import Event, EventType
from core.kernel import SessionBusyError

router = APIRouter(prefix="/api", tags=["chat"])

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class ChatBody(BaseModel):
    message: str


class PermissionBody(BaseModel):
    action: str  # allow | deny
    remember: bool = False
    interrupt_id: str


def _frame(ev: Event) -> str:
    return f"event: {ev.type.value}\ndata: {ev.model_dump_json()}\n\n"


async def _sse_pump(
    gen_factory, request: Request, session_id: str
) -> AsyncIterator[str]:
    """把内核事件流泵成 SSE，15s 心跳；断开时取消上游 run。"""
    queue: asyncio.Queue = asyncio.Queue()

    async def pump():
        try:
            async for ev in gen_factory():
                await queue.put(ev)
        except SessionBusyError as exc:
            await queue.put(
                Event(
                    type=EventType.ERROR,
                    session_id=session_id,
                    run_id="",
                    payload=events.error(str(exc), kind="busy"),
                )
            )
        except Exception as exc:
            await queue.put(
                Event(
                    type=EventType.ERROR,
                    session_id=session_id,
                    run_id="",
                    payload=events.error(str(exc), kind="server_error"),
                )
            )
        finally:
            await queue.put(None)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if ev is None:
                break
            if await request.is_disconnected():
                break
            yield _frame(ev)
    finally:
        task.cancel()


@router.post("/sessions/{session_id}/chat")
async def chat(session_id: str, body: ChatBody, request: Request):
    kernel = request.app.state.kernel
    if kernel.is_busy(session_id):
        raise HTTPException(status_code=409, detail="会话已有活跃 run")

    return StreamingResponse(
        _sse_pump(lambda: kernel.astream(session_id, body.message), request, session_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/sessions/{session_id}/permission")
async def permission(session_id: str, body: PermissionBody, request: Request):
    kernel = request.app.state.kernel
    if kernel.is_busy(session_id):
        raise HTTPException(status_code=409, detail="会话已有活跃 run")
    if body.action not in ("allow", "deny"):
        raise HTTPException(status_code=400, detail="action 必须是 allow 或 deny")

    decision = {"action": body.action, "remember": body.remember}
    return StreamingResponse(
        _sse_pump(
            lambda: kernel.resume(session_id, decision, body.interrupt_id),
            request,
            session_id,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )