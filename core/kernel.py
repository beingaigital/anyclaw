"""AgentKernel：headless 内核 facade，CLI 与 Web 共用。

职责：
- 编译 LangGraph 图 + 持有 AsyncSqliteSaver（checkpoint = 会话真相源）
- astream / resume → 统一 Event 流（内核是事件唯一写者，分配 seq/ts/run_id）
- run 段结束写 STM JSON 投影 + LTM（持久化不再散落 UI 层）
- 每会话单活跃 run（SessionBusyError → Web 409）
"""

from __future__ import annotations

import uuid
import warnings
from datetime import datetime
from typing import Any, AsyncIterator, Optional

import yaml
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from core import events
from core.events import Event, EventType
from core.graph import build_graph
from core.harness import AuditLog, HookRegistry, PermissionPolicy
from core.projection import ensure_projection, write_projection
from utils.long_term_memory import append_turn_memory
from utils.message_utils import messages_from_session_data
from utils.path import ensure_task_dirs, get_checkpoints_path, get_config_path
from utils.session_manager import get_session_manager
from utils.task_context import set_task_id
from utils.tool_registry import load_agent_tools


class SessionBusyError(RuntimeError):
    """会话已有活跃 run（Web 映射为 409）。"""


def _load_kernel_config() -> dict:
    path = get_config_path("kernel.yaml")
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


class AgentKernel:
    """headless 内核。一个实例服务多个会话（状态按 thread_id 隔离）。"""

    def __init__(
        self,
        *,
        graph: Any,
        saver: Any,
        saver_cm: Any,
        model: Any,
        tools: list,
        policy: PermissionPolicy,
        hooks: HookRegistry,
        audit: AuditLog,
        max_context_tokens: int,
        keep_recent: int,
        recursion_limit: int,
        mcp_status: Optional[dict[str, str]] = None,
    ):
        self._graph = graph
        self._saver = saver
        self._saver_cm = saver_cm
        self._model = model
        self._tools = tools
        self._policy = policy
        self._hooks = hooks
        self._audit = audit
        self._max_context_tokens = max_context_tokens
        self._keep_recent = keep_recent
        self._recursion_limit = recursion_limit
        self.mcp_status = mcp_status or {}
        self._active: dict[str, bool] = {}

    @classmethod
    async def create(
        cls,
        *,
        model: Any = None,
        tools: Optional[list] = None,
        policy: Optional[PermissionPolicy] = None,
        hooks: Optional[HookRegistry] = None,
        checkpointer: Any = None,
    ) -> "AgentKernel":
        """创建内核。model/tools/policy/hooks/checkpointer 均可注入（测试用 fake）。"""
        cfg = _load_kernel_config()
        max_context_tokens = int(cfg.get("max_context_tokens", 20000) or 20000)
        keep_recent = int(cfg.get("keep_recent_messages", 10) or 10)
        recursion_limit = int(cfg.get("recursion_limit", 50) or 50)

        if model is None:
            from model.factory import get_react_model

            model = get_react_model()
        policy = policy or PermissionPolicy.load()
        hooks = hooks or HookRegistry.load()
        audit = AuditLog()

        mcp_status: dict[str, str] = {}
        if tools is None:
            tools = load_agent_tools()
            # MCP 工具（可选 extra，import guarded；server 挂掉不影响启动）
            from utils.mcp_registry import load_mcp_tools_async

            mcp_result = await load_mcp_tools_async()
            mcp_status = mcp_result.server_status
            existing_names = {t.name for t in tools}
            for t in mcp_result.tools:
                if t.name not in existing_names:
                    tools.append(t)
                    existing_names.add(t.name)
                    policy.mcp_tool_names.add(t.name)

        saver_cm = None
        if checkpointer is not None:
            saver = checkpointer
        else:
            saver_cm = AsyncSqliteSaver.from_conn_string(str(get_checkpoints_path()))
            saver = await saver_cm.__aenter__()
            await saver.setup()

        graph = build_graph(
            model=model,
            tools=tools,
            policy=policy,
            hooks=hooks,
            audit=audit,
            max_context_tokens=max_context_tokens,
            keep_recent=keep_recent,
        ).compile(checkpointer=saver)

        return cls(
            graph=graph,
            saver=saver,
            saver_cm=saver_cm,
            model=model,
            tools=tools,
            policy=policy,
            hooks=hooks,
            audit=audit,
            max_context_tokens=max_context_tokens,
            keep_recent=keep_recent,
            recursion_limit=recursion_limit,
            mcp_status=mcp_status,
        )

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def is_busy(self, session_id: str) -> bool:
        return bool(self._active.get(session_id))

    async def astream(self, session_id: str, user_input: str) -> AsyncIterator[Event]:
        """运行一轮用户输入，yield 统一事件流。"""
        if self._active.get(session_id):
            raise SessionBusyError(f"会话 {session_id} 已有活跃 run")
        self._active[session_id] = True

        ensure_projection(session_id, user_input)
        ensure_task_dirs(session_id)

        # checkpoint 为空且 STM JSON 有历史 → 旧会话种子恢复
        config = {"configurable": {"thread_id": session_id}}
        snapshot = await self._graph.aget_state(config)
        existing = (snapshot.values or {}).get("messages", [])
        if existing:
            input_data: Any = {"messages": [HumanMessage(content=user_input)], "task_id": session_id}
        else:
            session_data = get_session_manager().load_session(session_id)
            history = messages_from_session_data(session_data) if session_data else []
            input_data = {
                "messages": [*history, HumanMessage(content=user_input)],
                "task_id": session_id,
            }

        async for ev in self._run(session_id, input_data, user_query=user_input):
            yield ev

    async def resume(
        self, session_id: str, decision: dict, interrupt_id: str
    ) -> AsyncIterator[Event]:
        """恢复被 interrupt 挂起的 run（用户权限确认）。"""
        if self._active.get(session_id):
            raise SessionBusyError(f"会话 {session_id} 已有活跃 run")
        self._active[session_id] = True
        set_task_id(session_id)
        input_data = Command(resume={interrupt_id: decision})
        async for ev in self._run(session_id, input_data, user_query=None):
            yield ev

    async def get_history(self, session_id: str) -> list[BaseMessage]:
        snapshot = await self._graph.aget_state({"configurable": {"thread_id": session_id}})
        return (snapshot.values or {}).get("messages", [])

    async def get_pending_interrupt(self, session_id: str) -> Optional[dict]:
        """返回 pending 的权限确认（Web 刷新页面恢复弹窗用）。"""
        snapshot = await self._graph.aget_state({"configurable": {"thread_id": session_id}})
        for task in snapshot.tasks:
            for intr in getattr(task, "interrupts", ()) or ():
                value = intr.value if isinstance(intr.value, dict) else {}
                return {"interrupt_id": str(intr.id), **value}
        return None

    async def reload_tools(self, tools: Optional[list] = None) -> None:
        """重新加载工具（含 MCP）并重编译图（同一 checkpointer，会话不丢）。"""
        if tools is None:
            tools = load_agent_tools(force_reload=True)
            from utils.mcp_registry import load_mcp_tools_async

            mcp_result = await load_mcp_tools_async(force_reload=True)
            self.mcp_status = mcp_result.server_status
            self._policy.mcp_tool_names.clear()
            existing_names = {t.name for t in tools}
            for t in mcp_result.tools:
                if t.name not in existing_names:
                    tools.append(t)
                    existing_names.add(t.name)
                    self._policy.mcp_tool_names.add(t.name)
        self._tools = tools
        self._graph = build_graph(
            model=self._model,
            tools=tools,
            policy=self._policy,
            hooks=self._hooks,
            audit=self._audit,
            max_context_tokens=self._max_context_tokens,
            keep_recent=self._keep_recent,
        ).compile(checkpointer=self._saver)

    async def close(self) -> None:
        if self._saver_cm is not None:
            await self._saver_cm.__aexit__(None, None, None)
            self._saver_cm = None

    # ------------------------------------------------------------------
    # 内部：事件流消费
    # ------------------------------------------------------------------

    async def _run(
        self, session_id: str, input_data: Any, *, user_query: Optional[str]
    ) -> AsyncIterator[Event]:
        run_id = uuid.uuid4().hex[:12]
        seq = 0
        usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        usage_steps: list[dict] = []
        final_text = ""
        status = "completed"
        err_event: Optional[Event] = None

        def emit(type_: EventType, payload: dict) -> Event:
            nonlocal seq
            ev = Event(type=type_, session_id=session_id, run_id=run_id, seq=seq, payload=payload)
            seq += 1
            return ev

        config = {
            "configurable": {"thread_id": session_id, "run_id": run_id},
            "recursion_limit": self._recursion_limit,
        }
        set_task_id(session_id)

        yield emit(EventType.RUN_STARTED, {"user_input": user_query or ""})

        try:
            async for mode, data in self._graph.astream(
                input_data, config, stream_mode=["messages", "updates", "custom"]
            ):
                if mode == "messages":
                    chunk, meta = data
                    # 只取 agent 节点输出：过滤压缩摘要 LLM 与工具内部 LLM
                    if (meta or {}).get("langgraph_node") != "agent":
                        continue
                    content = getattr(chunk, "content", "")
                    if isinstance(content, str) and content:
                        yield emit(
                            EventType.TOKEN_DELTA,
                            events.token_delta(str(getattr(chunk, "id", "") or ""), content),
                        )
                elif mode == "custom":
                    if not isinstance(data, dict):
                        continue
                    kind = data.get("kind")
                    payload = {k: v for k, v in data.items() if k != "kind"}
                    type_map = {
                        "compression": EventType.COMPRESSION,
                        "usage": EventType.USAGE,
                        "tool_call_started": EventType.TOOL_CALL_STARTED,
                        "tool_result": EventType.TOOL_RESULT,
                        "permission_resolved": EventType.PERMISSION_RESOLVED,
                    }
                    if kind in type_map:
                        if kind == "usage":
                            for key in usage_totals:
                                usage_totals[key] += int(payload.get(key, 0) or 0)
                            usage_steps.append({**payload, "timestamp": datetime.now().isoformat()})
                        yield emit(type_map[kind], payload)
                elif mode == "updates":
                    if not isinstance(data, dict):
                        continue
                    if "__interrupt__" in data:
                        intr_list = data["__interrupt__"]
                        if intr_list:
                            intr = intr_list[0]
                            value = intr.value if isinstance(intr.value, dict) else {}
                            yield emit(
                                EventType.PERMISSION_REQUEST,
                                events.permission_request(
                                    interrupt_id=str(intr.id),
                                    tool_call_id=value.get("tool_call_id", ""),
                                    tool_name=value.get("tool_name", ""),
                                    args=value.get("args", {}),
                                    tier=value.get("tier", "ask"),
                                    reason=value.get("reason", ""),
                                ),
                            )
                        status = "awaiting_permission"
                    elif "agent" in data:
                        node_update = data["agent"] or {}
                        for msg in node_update.get("messages", []):
                            if isinstance(msg, AIMessage):
                                content = str(msg.content or "")
                                tool_calls = [
                                    dict(tc) for tc in (getattr(msg, "tool_calls", None) or [])
                                ]
                                yield emit(
                                    EventType.MESSAGE_COMPLETE,
                                    events.message_complete(str(msg.id or ""), content, tool_calls),
                                )
                                if content and not tool_calls:
                                    final_text = content
        except GraphRecursionError as exc:
            status = "error"
            err_event = emit(
                EventType.ERROR,
                events.error(f"达到步数上限({self._recursion_limit}): {exc}", kind="recursion_limit"),
            )
        except Exception as exc:
            status = "error"
            err_event = emit(EventType.ERROR, events.error(str(exc), kind="model_error"))
        finally:
            self._active.pop(session_id, None)

        # 投影 + LTM（不在 finally 里 yield，避免 GeneratorExit 冲突）
        try:
            snapshot = await self._graph.aget_state(config)
            messages = (snapshot.values or {}).get("messages", [])
            query_for_ltm = user_query
            if not query_for_ltm:
                for msg in reversed(messages):
                    if isinstance(msg, HumanMessage):
                        query_for_ltm = str(msg.content)
                        break
            write_projection(
                session_id,
                messages,
                usage_steps=usage_steps,
                usage_totals=usage_totals,
                final_query=query_for_ltm if status == "completed" else None,
            )
            if status == "completed" and final_text.strip() and query_for_ltm:
                append_turn_memory(task_id=session_id, query=query_for_ltm, response=final_text)
        except Exception as exc:
            warnings.warn(f"投影写入失败: {exc}")

        if err_event is not None:
            yield err_event
        yield emit(EventType.RUN_FINISHED, events.run_finished(status, usage_totals, final_text))