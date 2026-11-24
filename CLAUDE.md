# AnyClaw — Agent 开发底座

LangGraph headless 内核 + Harness 护栏 + CLI/Web 双端。基于它 vibe coding 垂类 Agent。

## 架构速览

- `core/` — 内核。`AgentKernel.astream/resume` 产出统一 `Event` 流（`core/events.py`），CLI 与 Web 共用，**不要在 UI 层做持久化或猜测事件类型**。
  - `core/graph.py` — 显式图：`prepare_context → agent → permission_gate → tools` 循环。
  - `core/harness/` — 权限三级（always/ask/never）+ hooks + 审计。
  - checkpoint（`memory/checkpoints.sqlite`）= 会话真相源；`memory/STM/*.json` = 内核单写者投影。
- `cli/` — 薄渲染器（Rich）。`cli/main.py` 主菜单；`cli/interactive.py` 消费事件流。
- `server/` + `web/` — FastAPI SSE + React 控制台。`anyclaw web` 启动。
- `tools/` — `@tool` 工具，自动发现注册。**写文件必须走 `utils/path_guard.resolve_tool_path`（写仅限沙箱）**。
- `model/factory.py` — 多 provider 模型工厂（`config/model.yaml` 按场景配置）。
- `utils/` — 会话/记忆/skills/profile/MCP 注册。Profile 层 = 根目录六大 md。

## 关键约束（改动前必读）

1. `interrupt()` 不可被宽 `try/except` 包裹；`permission_gate` 在 interrupt 前**零副作用**（resume 时节点从头重跑），audit 按 tool_call_id 去重。
2. 临时上下文（system prompt/LTM/skills）每轮重建，**不进 checkpoint messages**。
3. 压缩用 prompt-token 估算触发，`AIMessage(tool_calls)+ToolMessage` 组永不拆分。
4. task_id 经 ContextVar 传播（内核在 astream 前设置）；不要加线程级全局兜底。
5. 工具返回 JSON 字符串（含 `ok` 字段）；事件类型由内核显式发出。
6. AsyncSqliteSaver 生命周期由内核独占。

## 工作规则

- 动手前先声明模式（planning/building/reviewing/debugging/testing）。
- 写新代码前先搜已有实现（`utils/` 大概率有轮子）。
- 多步改动先给验证计划；改内核必须跑 `pytest`（fake model，无需 API key）。
- 工具作者体验不变：写 `@tool` 丢进 `tools/`，`config/permissions.yaml` 配权限。

## 常用命令

```bash
source .venv/bin/activate
pytest                          # 内核测试
python evals/runner.py          # eval 场景（CI 模式）
anyclaw                         # CLI
anyclaw web --port 8000         # Web 控制台
cd web && npm run dev           # 前端开发（代理 /api → 8000）
```

## 配置

`config/`：model / tools / skills / profile / prompt / kernel / permissions / hooks / mcp（yaml）。
API Key 在 `.env`（`*_APIKEY` 约定，模板见 `.env.example`）。