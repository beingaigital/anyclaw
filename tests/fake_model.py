"""测试用脚本模型与探针工具：不依赖真实 API key 验证内核行为。"""

from __future__ import annotations

import json
import uuid
from typing import Any, Iterator, List, Optional

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolCallChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool

from utils.task_context import get_task_id


class ScriptedChatModel(BaseChatModel):
    """按脚本顺序返回 AIMessage（可含 tool_calls）；脚本耗尽后复用最后一条。

    _generate 驱动 _stream 聚合，使 LangGraph stream_mode="messages"
    能通过 on_llm_new_token 回调捕获 token 流。
    """

    responses: List[AIMessage]
    call_count: int = 0
    received: List[List[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted-chat-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _next_response(self) -> AIMessage:
        idx = min(self.call_count, len(self.responses) - 1)
        self.call_count += 1
        return self.responses[idx]

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        self.received.append(list(messages))
        resp = self._next_response()
        content = str(resp.content or "")
        # 唯一 id：避免跨实例/跨轮次 id 碰撞触发 add_messages 的同 id 替换语义
        msg_id = resp.id or f"scripted-{uuid.uuid4().hex[:8]}-{self.call_count}"

        for ch in content:
            chunk = AIMessageChunk(content=ch, id=msg_id)
            if run_manager:
                run_manager.on_llm_new_token(ch, chunk=chunk)
            yield ChatGenerationChunk(message=chunk)

        if resp.tool_calls:
            tccs = [
                ToolCallChunk(
                    name=tc["name"],
                    args=json.dumps(tc.get("args", {}), ensure_ascii=False),
                    id=tc.get("id", ""),
                    index=i,
                )
                for i, tc in enumerate(resp.tool_calls)
            ]
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=tccs, id=msg_id))

        usage = getattr(resp, "usage_metadata", None)
        if usage:
            yield ChatGenerationChunk(
                message=AIMessageChunk(content="", id=msg_id, usage_metadata=usage)
            )

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        aggregated: Optional[AIMessageChunk] = None
        for gen_chunk in self._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
            chunk = gen_chunk.message
            aggregated = chunk if aggregated is None else aggregated + chunk
        if aggregated is None:
            aggregated = AIMessageChunk(content="")
        return ChatResult(generations=[ChatGeneration(message=aggregated)])


def make_tool_call_message(name: str, args: dict, call_id: str, content: str = "") -> AIMessage:
    """构造带 tool_calls 的脚本 AIMessage。"""
    return AIMessage(
        content=content,
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


@tool
def probe_tool(x: str) -> str:
    """探针工具：回显输入与当前 task_id，用于验证上下文传播。"""
    return json.dumps({"ok": True, "x": x, "task_id": get_task_id()}, ensure_ascii=False)


@tool
def sync_context_probe() -> str:
    """同步探针：记录执行线程与 task_id（验证 ContextVar 跨 executor 传播）。"""
    import threading

    return json.dumps(
        {"ok": True, "task_id": get_task_id(), "thread": threading.get_ident()},
        ensure_ascii=False,
    )