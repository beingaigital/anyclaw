# AnyClaw 架构与事件流

## 分层

| 层 | 目录 | 职责 |
|----|------|------|
| 内核 | `core/` | LangGraph 显式图 + checkpoint + 统一事件流 + 投影/LTM 写入 |
| Harness | `core/harness/` | 权限三级（always/ask/never）+ 参数规则 + hooks + 审计 |
| 前端 | `cli/`、`server/` + `web/` | 渲染事件流；CLI 用 Rich，Web 用 SSE + React |
| 能力 | `tools/`、`utils/mcp_registry.py` | @tool 自动发现 + MCP 外部工具 |
| 资产 | `model/`、`utils/`、`config/`、profile md、skills | 模型工厂 / 记忆 / 技能 / 配置 |

## 图结构

```
START → prepare_context → agent → [有 tool_calls?]
                                  ├─ 否 → END
                                  └─ 是 → permission_gate → tools → agent（循环）
```

- **prepare_context**：重建 system prompt（profile + 工具目录 + skills catalog）与临时上下文（LTM + 激活技能）；按 prompt token 估算触发历史压缩（`RemoveMessage(REMOVE_ALL_MESSAGES)` + 摘要 + 最近窗口，tool 组不拆）。临时上下文不进 checkpoint messages。
- **agent**：`model.ainvoke`；token 流由 `stream_mode="messages"` 提供（只取 `langgraph_node=="agent"`，过滤压缩/工具内部 LLM）。
- **permission_gate**：逐 tool_call 评估权限。ask → `interrupt(payload)` 挂起；**interrupt 前零副作用**（resume 时节点从头重跑），audit 与事件发射在所有 interrupt 完成后进行并按 tool_call_id 去重。
- **tools**：自定义执行节点（非 ToolNode）。被拒调用补合成 ToolMessage 保持配对；批准调用走 pre-hook → `tool.ainvoke` → post-hook → 审计。异常 → error ToolMessage，模型自纠。

## 事件流（CLI 与 Web 共用）

```
caller                    AgentKernel                     StateGraph
  │  astream(sid, text)        │                              │
  │───────────────────────────>│ run_started (seq=0)          │
  │                            │── astream(messages/updates/  │
  │                            │   custom) ──────────────────>│ prepare_context
  │                            │<── custom: compression ──────│ (可能压缩)
  │                            │<── messages: (chunk, meta) ──│ agent
  │                            │    → token_delta             │
  │                            │<── custom: usage ────────────│
  │                            │<── updates: {agent: msgs} ───│ → message_complete
  │                            │<── updates: {__interrupt__} ─│ permission_gate (ask)
  │  permission_request        │                              │
  │  run_finished(awaiting)    │ [流结束，checkpoint 落盘]      │
  │  Confirm / 弹窗             │                              │
  │  resume(sid, decision, id) │                              │
  │───────────────────────────>│ Command(resume={id: decision})│ gate 重跑，interrupt 返回决策
  │                            │<── custom: permission_resolved│
  │                            │<── custom: tool_call_started ─│ tools
  │                            │<── custom: tool_result ──────│
  │                            │        ... 循环回 agent ...    │
  │                            │<── updates: {agent: msgs} ───│ 无 tool_calls → END
  │                            │ 写 STM 投影 + LTM             │
  │  run_finished(completed)   │                              │
```

## 持久化

- **checkpoint（memory/checkpoints.sqlite）= 会话真相源**：消息、interrupt 状态、会话 allowlist。跨进程重启可恢复。
- **STM JSON（memory/STM/{id}.json）= 投影**：内核单写者，run 段结束时全量写。保持旧 schema，session_ui / clear_utils / LTM 兼容。
- **LTM（memory/LTM/turn_memory.jsonl）**：完成轮追加，下一轮按相关度检索注入。
- **审计（memory/audit/{日期}.jsonl）**：每个工具决策 + 结果摘要。

## 关键约束

1. `interrupt()` 不可被宽 try/except 包裹（内部抛 GraphInterrupt）。
2. permission_gate 重跑安全：interrupt 前纯计算，副作用后置 + 去重。
3. 同步 @tool 经 `run_in_executor` 的 `copy_context` 看到 ContextVar（task_id）；内核在 astream 前设置。
4. AsyncSqliteSaver 生命周期由内核独占（FastAPI lifespan 不重复 enter）。
5. Web 默认绑 127.0.0.1 无认证（单用户本地）；暴露局域网前需自行加认证。