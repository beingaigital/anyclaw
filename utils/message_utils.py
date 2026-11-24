"""消息工具：消息格式转换、会话数据恢复、tool 配对校验与历史压缩。"""

from __future__ import annotations

import json
import warnings
from typing import Any, List, Tuple
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage, BaseMessage, ToolCall, SystemMessage
from model.factory import get_text_generation_model


def messages_from_session_data(session_data: dict) -> List[BaseMessage]:
    """
    从会话数据中恢复消息列表

    兼容 LangChain 1.x 的消息与工具调用格式：
    - AIMessage.tool_calls 使用 ToolCall 对象列表
    - ToolMessage 只依赖 tool_call_id，不需要 name 参数
    """
    messages: List[BaseMessage] = []

    for i, msg_data in enumerate(session_data.get("messages", [])):
        role = msg_data.get("role", "")
        content = msg_data.get("content", "") or ""

        if role == "user":
            msg = HumanMessage(content=content)
            messages.append(msg)

        elif role == "assistant":
            raw_tool_calls = msg_data.get("tool_calls", [])
            if raw_tool_calls is None:
                raw_tool_calls = []
            elif not isinstance(raw_tool_calls, list):
                warnings.warn(f"tool_calls 不是列表格式: {type(raw_tool_calls)}, 设为空列表")
                raw_tool_calls = []

            formatted_tool_calls: List[ToolCall] = []

            if raw_tool_calls:
                for tc_idx, tc in enumerate(raw_tool_calls):
                    if isinstance(tc, dict):
                        tc_name = tc.get("name") or tc.get("function", {}).get("name") or "unknown"
                        tc_args = tc.get("args") or tc.get("function", {}).get("arguments") or {}
                        if isinstance(tc_args, str):
                            try:
                                tc_args = json.loads(tc_args)
                            except (json.JSONDecodeError, TypeError):
                                tc_args = {}
                        tc_id = tc.get("id") or tc.get("tool_call_id") or tc.get("call_id") or ""

                        if not tc_id:
                            tc_id = f"call_{i}_{tc_idx}_{hash(str(tc))}"
                            warnings.warn(f"tool_call 缺少 id，生成临时 id: {tc_id}")

                        extra_kwargs: dict[str, Any] = {}
                        if "type" in tc:
                            extra_kwargs["type"] = tc["type"]

                        tool_call = ToolCall(
                            name=tc_name,
                            args=tc_args if isinstance(tc_args, dict) else {},
                            id=tc_id,
                            **extra_kwargs,
                        )
                        formatted_tool_calls.append(tool_call)
                    elif isinstance(tc, str):
                        warnings.warn(f"tool_call 是字符串格式（可能是旧数据），跳过: {tc}")
                    else:
                        formatted_tool_calls.append(tc)

            if formatted_tool_calls is None:
                formatted_tool_calls = []
            elif not isinstance(formatted_tool_calls, list):
                warnings.warn(f"formatted_tool_calls 不是列表格式: {type(formatted_tool_calls)}, 设为空列表")
                formatted_tool_calls = []

            try:
                if formatted_tool_calls:
                    ai_msg = AIMessage(content=content, tool_calls=formatted_tool_calls)
                else:
                    ai_msg = AIMessage(content=content)
            except Exception:
                ai_msg = AIMessage(content=content)

            messages.append(ai_msg)

        elif role == "tool":
            tool_call_id = msg_data.get("tool_call_id", "") or ""
            tool_name = msg_data.get("tool_name") or "unknown_tool"

            if not tool_call_id:
                tool_call_id = f"restored_{tool_name}_{i}"
                warnings.warn(f"ToolMessage 缺少 tool_call_id，使用生成的 id: {tool_call_id}")

            tool_msg = ToolMessage(
                content=content,
                tool_call_id=tool_call_id,
                name=tool_name
            )
            messages.append(tool_msg)

    return validate_tool_pairing(messages)


def validate_tool_pairing(messages: List[BaseMessage]) -> List[BaseMessage]:
    """tool 配对校验（会话恢复与历史压缩共用）：

    1. ToolMessage 必须紧跟带对应 tool_calls 的 AIMessage，否则丢弃；
    2. 移除没有对应 ToolMessage 的 tool_calls（OpenAI 要求每个 tool_call 都有应答）。
    """
    # 第一遍：校验 ToolMessage 的前驱与 id 匹配
    validated_messages: List[BaseMessage] = []

    for i, msg in enumerate(messages):
        if isinstance(msg, ToolMessage):
            if i == 0 or not validated_messages:
                warnings.warn("ToolMessage 在消息列表开头，跳过该消息")
                continue

            prev_msg = validated_messages[-1]
            if not isinstance(prev_msg, AIMessage) or not getattr(prev_msg, "tool_calls", None):
                continue

            prev_tool_call_ids = set()
            for tc in prev_msg.tool_calls:
                if isinstance(tc, dict):
                    tc_id = tc.get("id") or tc.get("tool_call_id") or tc.get("call_id")
                else:
                    tc_id = getattr(tc, "id", None)
                if tc_id:
                    prev_tool_call_ids.add(tc_id)

            if msg.tool_call_id not in prev_tool_call_ids:
                continue

        validated_messages.append(msg)

    # 第二遍：移除没有对应 ToolMessage 的 tool_calls
    final_messages: List[BaseMessage] = []

    for i, msg in enumerate(validated_messages):
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            next_tool_call_ids: set[str] = set()
            for next_msg in validated_messages[i + 1 :]:
                if isinstance(next_msg, ToolMessage):
                    next_tool_call_ids.add(next_msg.tool_call_id)
                elif isinstance(next_msg, (AIMessage, HumanMessage)):
                    break

            valid_tool_calls = []
            for tc in msg.tool_calls:
                if isinstance(tc, dict):
                    tc_id = tc.get("id") or tc.get("tool_call_id") or tc.get("call_id")
                else:
                    tc_id = getattr(tc, "id", None)
                if tc_id in next_tool_call_ids:
                    valid_tool_calls.append(tc)

            if len(valid_tool_calls) != len(msg.tool_calls):
                msg.tool_calls = valid_tool_calls

        final_messages.append(msg)

    return final_messages


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK 字符 ×1，其余字符 /4。用于压缩触发判断。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return cjk + (other // 4 if other else 0)


def _messages_token_estimate(messages: List[BaseMessage]) -> int:
    """估算消息列表的 prompt token 数。"""
    total = 0
    for msg in messages:
        content = getattr(msg, "content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += estimate_tokens(str(part.get("text", "")))
                else:
                    total += estimate_tokens(str(part))
        tool_calls = getattr(msg, "tool_calls", None) or []
        for tc in tool_calls:
            total += estimate_tokens(json.dumps(tc, ensure_ascii=False, default=str))
    return total


def compress_history(
    messages: List[BaseMessage],
    keep_recent: int = 10,
) -> Tuple[List[BaseMessage], str]:
    """将旧消息压缩为摘要，保留最近 keep_recent 条（tool 调用组永不拆分）。

    与旧版 compress_messages 的区别：
    - 不再依赖"累计 completion_tokens"这一与上下文大小无关的错误指标，
      触发判断由调用方（prepare_context 节点）按 prompt token 估算决定；
    - 压缩边界回退到 tool 组起点，保证 AIMessage(tool_calls)+ToolMessages 不被拆开。

    Returns:
        (新消息列表 [summary_msg, *recent], 摘要文本)。
        调用方负责用 RemoveMessage(REMOVE_ALL_MESSAGES) 包装后写回 state。
    """
    if len(messages) <= keep_recent:
        return messages, ""

    # 边界回退：若落在 ToolMessage 上，回退到其所属 AIMessage，保证组完整
    boundary = max(0, len(messages) - keep_recent)
    while boundary > 0 and isinstance(messages[boundary], ToolMessage):
        boundary -= 1

    old_messages = messages[:boundary]
    recent_messages = messages[boundary:]
    if not old_messages:
        return messages, ""

    try:
        summary_llm = get_text_generation_model()

        old_content_parts = []
        for msg in old_messages:
            content = str(getattr(msg, "content", "") or "")
            if not content:
                continue
            if isinstance(msg, HumanMessage):
                role = "User"
            elif isinstance(msg, AIMessage):
                role = "Assistant"
            else:
                role = "Tool"
            if len(content) > 500:
                content = content[:500] + "..."
            old_content_parts.append(f"{role}: {content}")

        if not old_content_parts:
            return validate_tool_pairing(recent_messages), ""

        old_content = "\n".join(old_content_parts)
        summary_prompt = (
            f"请将以下对话历史压缩为简洁的摘要（100-200字），"
            f"保留关键信息、上下文和重要的工具调用结果：{old_content}\n摘要："
        )
        summary_response = summary_llm.invoke([HumanMessage(content=summary_prompt)])
        summary_text = str(getattr(summary_response, "content", "") or "").strip()

        summary_msg = AIMessage(content=f"[历史对话摘要] {summary_text}")
        return validate_tool_pairing([summary_msg, *recent_messages]), summary_text
    except Exception as exc:
        warnings.warn(f"历史压缩失败，仅保留最近消息: {exc}")
        return (
            validate_tool_pairing(recent_messages),
            f"压缩失败，已移除 {len(old_messages)} 条旧消息",
        )