"""LangGraph 显式图：prepare_context → agent → permission_gate → tools 循环。

设计要点：
- 临时上下文（system prompt / LTM / skills）每轮重建，不进 checkpoint messages。
- permission_gate 在 interrupt 前零副作用（resume 时节点从头重跑）；
  audit 与事件发射放在所有 interrupt 完成之后，并按 tool_call_id 去重。
- tools 为自定义执行节点（不用 ToolNode）：被拒调用补合成 ToolMessage，
  保持 AIMessage.tool_calls 配对；同时是 hooks / 事件 / audit 的落点。
- 绝不使用宽 try/except 包裹 interrupt()（其内部抛 GraphInterrupt）。
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import interrupt

from core.harness import AuditLog, HookContext, HookRegistry, PermissionPolicy
from core.state import AgentState
from utils.long_term_memory import format_relevant_memories_for_prompt
from utils.message_utils import compress_history, estimate_tokens, _messages_token_estimate
from utils.prompt_loader import get_system_prompt_with_tools
from utils.skill_registry import format_active_skills_for_prompt
from utils.task_context import set_task_id


def _safe_writer() -> Callable[[dict], None]:
    """获取 stream writer；非流式调用时回退 no-op。"""
    try:
        return get_stream_writer()
    except Exception:
        return lambda payload: None


def build_graph(
    *,
    model: Any,
    tools: list[BaseTool],
    policy: PermissionPolicy,
    hooks: HookRegistry,
    audit: AuditLog,
    max_context_tokens: int = 20000,
    keep_recent: int = 10,
) -> StateGraph:
    """构建（未编译的）Agent 图。编译与 checkpointer 由 AgentKernel 负责。"""

    tools_by_name = {tool.name: tool for tool in tools}
    bound_model = model.bind_tools(tools) if tools else model

    async def prepare_context(state: AgentState) -> dict:
        """重建临时上下文 + 按 prompt token 估算触发历史压缩。"""
        writer = _safe_writer()
        messages = state.get("messages", [])
        task_id = state.get("task_id", "")

        last_user_text = ""
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                last_user_text = str(msg.content)
                break

        system_prompt = get_system_prompt_with_tools(tools)
        context_parts = []
        ltm_context = format_relevant_memories_for_prompt(last_user_text, top_k=3)
        if ltm_context:
            context_parts.append(ltm_context)
        skill_context = format_active_skills_for_prompt(last_user_text)
        if skill_context:
            context_parts.append(skill_context)

        update: dict[str, Any] = {
            "system_prompt": system_prompt,
            "context": "\n\n".join(context_parts),
            "task_id": task_id,
        }

        # 压缩触发：优先用上一次模型调用的真实 input_tokens + 新输入估算
        estimated = _messages_token_estimate(messages)
        for msg in reversed(messages):
            usage_meta = getattr(msg, "usage_metadata", None)
            if isinstance(msg, AIMessage) and usage_meta and usage_meta.get("input_tokens"):
                estimated = int(usage_meta["input_tokens"]) + estimate_tokens(last_user_text)
                break

        if estimated > max_context_tokens and len(messages) > keep_recent:
            original_count = len(messages)
            new_messages, summary = compress_history(messages, keep_recent=keep_recent)
            if summary:
                update["messages"] = [RemoveMessage(id=REMOVE_ALL_MESSAGES), *new_messages]
                update["summary"] = summary
                writer(
                    {
                        "kind": "compression",
                        "original_count": original_count,
                        "kept_count": len(new_messages),
                        "summary": summary,
                    }
                )
        return update

    async def agent(state: AgentState) -> dict:
        """模型调用。token 流由 stream_mode='messages' 免费提供。"""
        writer = _safe_writer()
        messages = state.get("messages", [])
        system_prompt = state.get("system_prompt", "")
        context = state.get("context", "")

        system_text = system_prompt
        if context:
            system_text = f"{system_prompt}\n\n{context}" if system_prompt else context
        model_input: list[BaseMessage] = []
        if system_text:
            model_input.append(SystemMessage(content=system_text))
        model_input.extend(messages)

        resp = await bound_model.ainvoke(model_input)

        usage_meta = getattr(resp, "usage_metadata", None) or {}
        usage_update: dict[str, int] = {}
        if usage_meta:
            usage_update = {
                "prompt_tokens": int(usage_meta.get("input_tokens", 0) or 0),
                "completion_tokens": int(usage_meta.get("output_tokens", 0) or 0),
                "total_tokens": int(usage_meta.get("total_tokens", 0) or 0),
            }
            writer({"kind": "usage", "step": "agent", **usage_update})
        return {"messages": [resp], "usage": usage_update}

    def route_after_agent(state: AgentState) -> str:
        messages = state.get("messages", [])
        if messages and isinstance(messages[-1], AIMessage) and getattr(messages[-1], "tool_calls", None):
            return "permission_gate"
        return END

    async def permission_gate(state: AgentState, config: RunnableConfig) -> dict:
        """权限门：always 放行 / ask interrupt 等确认 / never 拦截。

        interrupt 前零副作用；audit 与 permission_resolved 事件在所有
        interrupt 完成后才发射（节点重跑安全 + 去重）。
        """
        messages = state.get("messages", [])
        last = messages[-1]
        tool_calls = getattr(last, "tool_calls", None) or []
        session_allowlist = list(state.get("session_allowlist", []))
        task_id = state.get("task_id", "")
        run_id = (config.get("configurable") or {}).get("run_id", "")

        approved: list[dict] = []
        rejected: list[dict] = []
        resolved_payloads: list[dict] = []
        audit_entries: list[dict] = []

        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("args") or {}
            tc_id = tc.get("id", "")
            decision = policy.evaluate(name, args, session_allowlist)
            tier = decision.tier
            action = "allow"
            remember = False

            if tier == "ask":
                # resume 重跑时此处直接返回已缓存的用户决策（按 interrupt 索引匹配）；
                # 首次执行到此抛 GraphInterrupt 挂起。此前不得有任何副作用。
                resume_value = interrupt(
                    {
                        "tool_call_id": tc_id,
                        "tool_name": name,
                        "args": args,
                        "tier": tier,
                        "reason": decision.reason,
                    }
                )
                if isinstance(resume_value, dict):
                    action = str(resume_value.get("action", "deny"))
                    remember = bool(resume_value.get("remember", False))
                else:
                    action = "deny"
                if action == "allow" and remember and name not in session_allowlist:
                    session_allowlist.append(name)
                resolved_payloads.append(
                    {
                        "kind": "permission_resolved",
                        "tool_call_id": tc_id,
                        "action": action,
                        "remember": remember,
                    }
                )

            # 纯计算归类（重跑安全）
            if tier == "never":
                rejected.append(
                    {"tool_call_id": tc_id, "tool_name": name, "reason": decision.reason, "action": "blocked"}
                )
                final_action = "blocked"
            elif action == "deny":
                rejected.append(
                    {"tool_call_id": tc_id, "tool_name": name, "reason": "用户拒绝执行", "action": "denied"}
                )
                final_action = "deny"
            else:
                approved.append({"tool_call_id": tc_id, "tool_name": name, "args": args})
                final_action = "allow"

            audit_entries.append(
                {
                    "kind": "decision",
                    "session_id": task_id,
                    "run_id": run_id,
                    "tool_call_id": tc_id,
                    "tool_name": name,
                    "tier": tier,
                    "action": final_action,
                    "reason": decision.reason,
                    "source": decision.source,
                }
            )

        for entry in audit_entries:
            if not audit.has_entry(entry["session_id"], entry["tool_call_id"], kind="decision"):
                audit.append(entry)

        writer = _safe_writer()
        for payload in resolved_payloads:
            writer(payload)

        return {
            "approved_calls": approved,
            "rejected": rejected,
            "session_allowlist": session_allowlist,
        }

    async def tools_node(state: AgentState, config: RunnableConfig) -> dict:
        """工具执行：被拒调用补合成 ToolMessage；批准调用走 hooks → ainvoke → audit。"""
        writer = _safe_writer()
        task_id = state.get("task_id", "")
        run_id = (config.get("configurable") or {}).get("run_id", "")
        approved = state.get("approved_calls", [])
        rejected = state.get("rejected", [])

        # 同步 @tool 经 run_in_executor 的 copy_context 可见此 ContextVar
        set_task_id(task_id)

        tool_messages: list[BaseMessage] = []

        for rej in rejected:
            content = json.dumps({"ok": False, "error": rej["reason"]}, ensure_ascii=False)
            tool_messages.append(
                ToolMessage(
                    content=content,
                    tool_call_id=rej["tool_call_id"],
                    name=rej["tool_name"],
                    status="error",
                )
            )
            writer(
                {
                    "kind": "tool_result",
                    "tool_call_id": rej["tool_call_id"],
                    "tool_name": rej["tool_name"],
                    "result": content,
                    "status": rej["action"],
                    "duration_ms": 0,
                }
            )
            audit.append(
                {
                    "kind": "result",
                    "session_id": task_id,
                    "run_id": run_id,
                    "tool_call_id": rej["tool_call_id"],
                    "tool_name": rej["tool_name"],
                    "status": rej["action"],
                    "result_summary": content[:500],
                }
            )

        for call in approved:
            tc_id = call["tool_call_id"]
            name = call["tool_name"]
            args = dict(call["args"])
            writer({"kind": "tool_call_started", "tool_call_id": tc_id, "tool_name": name, "args": args})

            ctx = HookContext(
                session_id=task_id, run_id=run_id, tool_name=name, tool_call_id=tc_id, args=args
            )
            hook_result = hooks.run_pre(ctx)
            if hook_result.action == "block":
                content = json.dumps(
                    {"ok": False, "error": f"hook 拦截: {hook_result.reason}"}, ensure_ascii=False
                )
                tool_messages.append(
                    ToolMessage(content=content, tool_call_id=tc_id, name=name, status="error")
                )
                writer(
                    {
                        "kind": "tool_result",
                        "tool_call_id": tc_id,
                        "tool_name": name,
                        "result": content,
                        "status": "blocked",
                        "duration_ms": 0,
                    }
                )
                audit.append(
                    {
                        "kind": "result",
                        "session_id": task_id,
                        "run_id": run_id,
                        "tool_call_id": tc_id,
                        "tool_name": name,
                        "status": "blocked",
                        "result_summary": content[:500],
                    }
                )
                continue
            args = ctx.args

            tool = tools_by_name.get(name)
            start = time.monotonic()
            status = "ok"
            if tool is None:
                content = json.dumps({"ok": False, "error": f"未知工具: {name}"}, ensure_ascii=False)
                status = "error"
            else:
                try:
                    result = await tool.ainvoke(args)
                    if isinstance(result, str):
                        content = result
                    elif isinstance(result, ToolMessage):
                        content = str(result.content)
                    else:
                        content = str(result)
                except Exception as exc:
                    content = json.dumps(
                        {"ok": False, "error": f"工具执行失败: {exc}"}, ensure_ascii=False
                    )
                    status = "error"
            duration_ms = int((time.monotonic() - start) * 1000)

            content = hooks.run_post(ctx, content)
            tool_messages.append(
                ToolMessage(
                    content=content,
                    tool_call_id=tc_id,
                    name=name,
                    status="error" if status == "error" else "success",
                )
            )
            writer(
                {
                    "kind": "tool_result",
                    "tool_call_id": tc_id,
                    "tool_name": name,
                    "result": content,
                    "status": status,
                    "duration_ms": duration_ms,
                }
            )
            audit.append(
                {
                    "kind": "result",
                    "session_id": task_id,
                    "run_id": run_id,
                    "tool_call_id": tc_id,
                    "tool_name": name,
                    "status": status,
                    "result_summary": content[:500],
                    "duration_ms": duration_ms,
                }
            )

        # ToolMessage 顺序与 AIMessage.tool_calls 对齐
        last = state.get("messages", [])[-1]
        order = {tc.get("id"): i for i, tc in enumerate(getattr(last, "tool_calls", None) or [])}
        tool_messages.sort(key=lambda m: order.get(m.tool_call_id, 999))
        return {"messages": tool_messages}

    graph = StateGraph(AgentState)
    graph.add_node("prepare_context", prepare_context)
    graph.add_node("agent", agent)
    graph.add_node("permission_gate", permission_gate)
    graph.add_node("tools", tools_node)
    graph.set_entry_point("prepare_context")
    graph.add_edge("prepare_context", "agent")
    graph.add_conditional_edges(
        "agent", route_after_agent, {"permission_gate": "permission_gate", END: END}
    )
    graph.add_edge("permission_gate", "tools")
    graph.add_edge("tools", "agent")
    return graph