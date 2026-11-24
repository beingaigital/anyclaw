<div align="center">

![AnyClaw Logo](docs/logo图/logo图.png)

# AnyClaw

**Agent 开发基座：LangGraph headless 内核 × Harness 护栏 × CLI/Web 双端**

*你写工具与人格，框架负责循环、安全、持久化和两个前端。*

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.2+-green.svg)](https://github.com/langchain-ai/langgraph)
[![LangChain](https://img.shields.io/badge/LangChain-1.5+-green.svg)](https://www.langchain.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.124+-teal.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18-61dafb.svg)](https://react.dev/)
[![Tests](https://img.shields.io/badge/tests-42_passed-brightgreen.svg)](#-测试与-eval)
[![Version](https://img.shields.io/badge/version-0.2.0-blue)](https://github.com/beingaigital/anyclaw)

</div>

---

## 📖 AnyClaw 是什么

**AnyClaw 不是一个成品 Agent，而是一个 Agent 开发基座。**

它把做一个可靠智能体最难的公共部分全部预制好了——推理循环、工具调度、权限护栏、人工确认、会话持久化、记忆、流式输出、CLI 与 Web 两个前端——你只需要写三样东西：

| 你写的 | 放在哪 | 作用 |
|--------|--------|------|
| **工具** | `tools/*.py`（`@tool` 装饰器） | 你的 Agent 能做什么（领域能力） |
| **人格** | 根目录六大 md + `prompt/system_prompt.txt` | 你的 Agent 是谁、怎么思考、什么风格 |
| **配置** | `config/*.yaml` | 模型、权限、技能、MCP、压缩参数 |

写完丢进去，`anyclaw` 终端可用、`anyclaw web` 浏览器可用，**同一套内核、同一套会话、同一套安全策略**。这就是"vibe coding 搭自己的垂类 Agent"的含义：不碰框架底层，只生长领域部分。

### 开箱即得

- 🔄 **LangGraph 显式图内核**：推理-工具循环、checkpoint 持久化、进程重启可恢复会话
- 🛡️ **Harness 护栏**：Always/Ask/Never 三级权限 + 参数规则 + hooks + 审计，工具级强制而非 prompt 级请求
- 🙋 **人工确认（HITL）**：高风险工具 `interrupt()` 挂起，CLI 弹窗 / Web 模态框确认后 `resume` 继续
- 🖥️ **CLI + Web 双端**：Rich 终端 / React 控制台（流式对话、工具卡片、权限弹窗、状态面板）
- 🧠 **记忆体系**：短期（checkpoint）/ 长期（相关度检索）/ Profile 人格层 / Skills 技能自动激活 / 历史压缩
- 🔌 **MCP 接入**：外部工具生态即插即用，与本地工具同一权限管控
- 📦 **任务隔离**：每会话独立沙箱，文件写操作默认仅限沙箱内
- ✅ **可验证**：42 个内核测试（fake model，无需 API key）+ 场景化 eval + 第二 AI 评审

---

## 🏗️ 架构

### 分层总览

```
                 ┌────────────────────────────────────────────────────────┐
                 │                    core/ (headless 内核)                │
  cli/ ──────────┤  AgentKernel.astream / resume                          │
  (Rich 渲染器)   │    └─ LangGraph StateGraph + AsyncSqliteSaver          │
                 │         prepare_context → agent → route                │
  server/ ───────┤              ↑              ├─ 无 tool_calls → END     │
  (FastAPI+SSE)  │              │              └─ permission_gate         │
       │         │              │                   ├─ never → 拦截       │
       ▼         │              │                   ├─ ask → interrupt()  │
  web/ (React)   │              └── tools ←─────────┴─ always → 放行      │
                 │  harness: permissions / hooks / audit                  │
                 │  checkpoint = 真相源 → STM JSON 投影（单写者）           │
                 └────────────────────────────────────────────────────────┘
   tools/ (@tool 自动发现) + MCP tools (langchain-mcp-adapters)
   model/factory.py · utils/(记忆/技能/profile) · config/*.yaml
```

**一个内核、一套事件 schema、两个前端。** CLI 和 Web 都是 `AgentKernel` 事件流的薄渲染器——没有第二套 agent 逻辑，也没有第二套持久化。

### 内核：LangGraph 显式图

不用黑盒 `create_agent`，而是显式状态图——每个环节可见、可拦截、可恢复：

```
START → prepare_context → agent → [有 tool_calls?]
                                  ├─ 否 → END
                                  └─ 是 → permission_gate → tools → agent（循环）
```

| 节点 | 职责 |
|------|------|
| `prepare_context` | 每轮重建 system prompt（Profile + 工具目录 + 技能目录）；注入 LTM 检索与激活技能；按 **prompt-token 估算**触发历史压缩（tool 调用组永不拆分） |
| `agent` | 模型调用；token 流由 LangGraph `messages` 流模式免费提供 |
| `permission_gate` | 逐工具调用评估权限；ask 级 → `interrupt()` 挂起等用户确认；**interrupt 前零副作用**（resume 时节点从头重跑，审计按 tool_call_id 去重） |
| `tools` | pre-hook → `tool.ainvoke` → post-hook → 审计；被拒调用补合成 ToolMessage 保持消息配对；异常 → error ToolMessage，模型自纠 |

选择 LangGraph 显式图的收益：**checkpoint 原生持久化**（跨进程恢复）、**interrupt 原生 HITL**（不需要自己造挂起/恢复机制）、**状态可观测**（随时 `aget_state` 查看会话与 pending 确认）。

### 统一事件流

内核是事件的**唯一写者**。图节点发出原始 payload，`AgentKernel` 统一封装为 `Event`（type / session_id / run_id / seq / ts / payload），CLI 与 SSE 消费同一 schema：

| 事件 | 含义 |
|------|------|
| `run_started` | 一轮运行开始 |
| `token_delta` | 模型流式增量（仅 agent 节点，过滤工具内部 LLM） |
| `message_complete` | 一条完整 AIMessage（含 tool_calls） |
| `tool_call_started` | 工具开始执行（已通过权限与 pre-hook） |
| `tool_result` | 工具结果（status: ok / error / denied / blocked） |
| `permission_request` | ask 级工具挂起，等待用户确认 |
| `permission_resolved` | 用户决策（allow / deny / remember） |
| `compression` | 历史压缩通知 |
| `usage` | 单次模型调用 token 消耗 |
| `run_finished` | 运行段结束（completed / awaiting_permission / error） |
| `error` | 错误（recursion_limit / model_error / …） |

UI 层**不再猜测**"这段 JSON 是不是工具结果"——事件类型由内核显式声明。想写第三个前端（桌面端、Slack bot…）？消费这套事件流即可。

### 持久化模型

| 存储 | 位置 | 角色 |
|------|------|------|
| **checkpoint** | `memory/checkpoints.sqlite` | **会话真相源**：消息、interrupt 状态、会话 allowlist。进程重启后 `/memory` 或 Web 侧栏直接恢复 |
| **STM 投影** | `memory/STM/{id}.json` | 内核单写者投影，保持人类可读 schema，供会话列表/检查 |
| **LTM** | `memory/LTM/turn_memory.jsonl` | 每轮完成追加，下一轮按相关度检索注入 |
| **审计** | `memory/audit/{日期}.jsonl` | 每个工具决策 + 结果摘要 + 耗时，可追溯 |

### Harness：工具级强制护栏

`SOUL.md` 里写"高风险需确认"只是请求，模型可能绕过。Harness 是**代码级强制**：

```
评估顺序：参数模式规则 → 会话 allowlist → 工具级 tier → MCP server 级 → 默认 tier
```

- **always**：直接放行（只读工具、计算器…）
- **ask**：`interrupt()` 挂起 → CLI Confirm / Web 模态框 → `resume` 继续；可选"本会话始终允许"（仅会话级，不回写配置）
- **never**：直接拦截 + 审计留痕（危险命令正则、越界路径…）

外加 **pre/post hooks**（可 block/改参/改结果，pre 异常 fail-closed）与**全量审计**。

---

## ✨ 核心特点

**内核**
- 显式图 + async-native：无线程桥接、无同步/异步胶水，ContextVar 原生传播任务上下文
- 每会话单活跃 run（并发安全），多会话共享一个编译图（状态按 thread_id 隔离）
- 压缩指标正确（prompt-token 估算，而非累计 completion），压缩保 tool 配对

**安全**
- 三级权限 + 参数正则规则 + 路径护栏（写仅限沙箱，防 `..`/符号链接逃逸）
- 人工确认走 LangGraph 原生 interrupt，checkpoint 落盘——**刷新页面/重启进程后确认弹窗可恢复**
- 工具异常不崩循环：error ToolMessage 回喂模型自纠

**双端**
- CLI：Rich 流式 Markdown、工具调用面板、token 统计、会话管理
- Web：会话侧栏、流式对话、可折叠工具卡片、权限模态框、工具/模型/Profile/Skills 状态面板（热重载）、token 用量条
- 两端会话互通：Web 建的会话 CLI `/memory` 可恢复，反之亦然

**扩展**
- 工具自动发现：`@tool` 丢进 `tools/` 即注册，`config/tools.yaml` 控制启停
- MCP：stdio / SSE / streamable_http 三种 transport，server 挂掉不影响启动
- 模型工厂：OpenAI / Gemini / Qwen / DeepSeek / Kimi，按场景配不同模型（main / text_generation / element_extraction / code_generation）

**可验证**
- fake model 脚本化测试：权限拦截、interrupt/resume、checkpoint 恢复、压缩、审计去重全部可断言，无需 API key
- eval 场景 + 第二 AI 评审（`--judge`），CI 可跑

---

## 🧱 作为开发基座：搭建你的垂类 Agent

### 扩展点地图

**先查这张表——绝大多数定制不需要碰内核：**

| 想定制 | 改哪里 | 动内核吗 |
|--------|--------|----------|
| Agent 身份/语气/价值观 | `IDENTITY.md` / `SOUL.md` / `USER.md` | 否 |
| 决策与路由策略 | `AGENT.md` | 否 |
| 基础行为指令 | `prompt/system_prompt.txt` | 否 |
| 领域能力 | `tools/*.py`（`@tool`） | 否 |
| 工具安全等级 | `config/permissions.yaml` | 否 |
| 工具前后拦截/改写 | `hooks/*.py` | 否 |
| 领域方法论 | `skills/*.md` + `config/skills.yaml` | 否 |
| 外部工具生态 | `config/mcp.yaml` | 否 |
| 模型与场景 | `config/model.yaml` | 否 |
| 上下文窗口/压缩/步数上限 | `config/kernel.yaml` | 否 |
| 记忆策略 | `MEMORY.md`（+ 可选改 `utils/long_term_memory.py`） | 否 |
| 全新前端（桌面/IM bot） | 消费 `/api` SSE 事件流 | 否 |

### 五步搭建：以"舆情分析助手"为例

#### Step 1 · 定义人格与行为

编辑根目录 md（下一轮对话即生效，无需重启）：

```markdown
# IDENTITY.md
你是舆情分析助手，擅长从文本中提炼立场、要点与风险。
回答简洁、结构化，关键结论必须引用工具结果。

# SOUL.md
- 不编造工具执行结果
- 涉及外发/删除操作必须经用户确认
```

需要更细的行为指令就改 `prompt/system_prompt.txt`；需要可复用的方法论就写成 skill（Step 4）。

#### Step 2 · 写领域工具

在 `tools/` 下新建文件，`@tool` 装饰器 + JSON 字符串返回。**自动发现注册，无需改任何框架代码：**

```python
"""tools/opinion.py — 舆情摘要工具"""
import json
from langchain_core.tools import tool
from model.factory import get_text_generation_model
from utils.task_context import get_task_id
from utils.path import ensure_task_dirs

@tool
def summarize_opinion(text: str) -> str:
    """
    描述：对舆情文本生成结构化摘要（立场、要点、风险）。
    使用时机：用户提供舆情文本并要求分析时调用。
    输入：text（必填）——待分析的舆情文本。
    输出：JSON 字符串，含 ok、summary、result_file_path 字段。
    """
    try:
        llm = get_text_generation_model()   # 工具内可按场景选模型
        resp = llm.invoke(f"分析以下舆情文本，输出摘要/立场/风险：\n{text}")
        result = {"ok": True, "summary": str(resp.content)}
        task_id = get_task_id()
        if task_id:                          # 产出落盘到任务沙箱
            out = ensure_task_dirs(task_id) / "opinion_summary.json"
            out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            result["result_file_path"] = str(out)
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
```

**工具契约（约定优于配置）：**
- 返回 JSON 字符串，含 `ok` 字段；错误也返回 JSON（`{"ok": false, "error": ...}`），不抛异常
- 文件产出写 `sandbox/{task_id}`（用 `get_task_id()` + `ensure_task_dirs()`）；文件读写工具走 `utils/path_guard` 护栏
- docstring 写清"描述/使用时机/输入/输出"——这是模型选择工具的依据
- 工具内调 LLM 用 `model/factory.py` 的场景模型（text_generation / element_extraction / code_generation）

#### Step 3 · 配权限

`config/permissions.yaml`：

```yaml
default_tier: ask
tools:
  summarize_opinion: always    # 纯分析，放行
  write_file: ask              # 写文件需确认
  run_shell: ask
rules:
  - tool: run_shell
    pattern: 'rm\s+-rf|sudo\s|mkfs|shutdown|reboot'
    action: never
    reason: 危险命令
```

#### Step 4 · 加领域技能（可选）

`skills/opinion-analysis.md` 写方法论，`config/skills.yaml` 配关键词自动激活：

```yaml
skills:
  opinion_analysis:
    file: skills/opinion-analysis.md
    priority: 90
    keywords: [舆情, 立场, 风险, 摘要]
```

用户 query 命中关键词 → 技能内容自动注入当轮上下文。

#### Step 5 · 选模型，运行

`config/model.yaml` 配好 provider/model/api_key_env（见[快速开始](#-快速开始)），然后：

```bash
anyclaw          # 终端里就是你的舆情分析助手
anyclaw web      # 浏览器里也是，同一套会话与权限
```

**全程没有碰 `core/` 一行代码。** 换 Logo 和欢迎语（`cli/display.py`、`web/index.html`）即可交付为你自己的产品。

### 进阶扩展

**Hooks——工具级拦截/改写**（`hooks/guard.py`，自动发现）：

```python
from core.harness import HookContext, HookResult

def pre_tool_use(ctx: HookContext) -> HookResult:
    if ctx.tool_name == "run_shell" and "curl" in str(ctx.args.get("command", "")):
        return HookResult(action="block", reason="禁止外联命令")
    return HookResult(action="allow")

def post_tool_use(ctx: HookContext, result: str) -> str:
    return result   # 可脱敏/改写结果
```

**MCP——挂外部工具生态**（`config/mcp.yaml`，需 `[mcp]` extra）：

```yaml
enabled: true
tool_name_prefix: true        # 工具名加 server 前缀防重名
servers:
  weather:
    transport: streamable_http
    url: http://localhost:8000/mcp
  math:
    transport: stdio
    command: python
    args: ["./mcp_servers/math_server.py"]
```

MCP 工具与本地工具走**同一**权限门/hooks/审计；`mcp.default_tier: ask` 兜底，可按 server 覆盖。单个 server 挂掉只记状态跳过，不影响启动。

**自定义前端**：`POST /api/sessions/{id}/chat` 返回 SSE 事件流（schema 见[统一事件流](#统一事件流)），`POST /api/sessions/{id}/permission` 提交确认。照着 `web/src/api.ts` 的 `streamSSE` 接即可。

### 验证你的 Agent

```bash
pytest                              # 内核回归（fake model，无需 key）
python evals/runner.py              # 场景 eval（CI 模式）
python evals/runner.py --real --judge   # 真实模型 + 第二 AI 评审
```

给你的垂类 Agent 写一个 eval 场景（`evals/scenarios/*.yaml`）：脚本化模型轮次 + 权限策略 + 可检查断言（事件序列/工具结果/审计/文件落盘），参考现有三个场景。

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+（仅 Web 前端开发/构建需要）

### 安装

```bash
git clone https://github.com/beingaigital/anyclaw.git
cd anyclaw

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e ".[dev,web,mcp,gemini]"
# 最小 CLI：pip install -e .
# 国内镜像：pip install -i https://mirrors.aliyun.com/pypi/simple/ -e ".[dev,web,mcp,gemini]"
```

### 配置 API Key

```bash
cp .env.example .env
# 编辑 .env（变量名约定 *_APIKEY）：
#   DEEPSEEK_APIKEY=...   （默认 main 模型）
#   QWEN_APIKEY=...       （text_generation 等场景）
#   OPENAI_APIKEY / GEMINI_APIKEY / KIMI_APIKEY 按需
```

### 配置模型

`config/model.yaml`（按场景配不同模型）：

```yaml
main:                  # Agent 底座：推理 + 工具决策
  provider: deepseek   # openai / gemini / qwen / deepseek / kimi
  model: deepseek-chat
  api_key_env: DEEPSEEK_APIKEY

text_generation:       # 文本生成 / 压缩摘要 / eval 评审
  provider: qwen
  model: qwen-plus
  api_key_env: QWEN_APIKEY

element_extraction:    # 结构化抽取
  provider: qwen
  model: qwen-plus
  api_key_env: QWEN_APIKEY

code_generation:       # 代码生成
  provider: deepseek
  model: deepseek-chat
  api_key_env: DEEPSEEK_APIKEY
```

### 运行

```bash
anyclaw                  # CLI 终端
anyclaw web --port 8000  # Web 控制台 → http://127.0.0.1:8000
```

**CLI 命令**：`/new` 新会话 · `/memory` 恢复会话 · `/models` `/tools` `/profile` `/skills`（支持 `reload`）· `/skills match <query>` · `/clear` · `/exit`

**Web 前端开发**（热更新）：

```bash
cd web && npm install && npm run dev   # http://localhost:5173，代理 /api → 8000
npm run build                          # 生产构建 → web/dist，FastAPI 单端口服务
```

---

## ⚙️ 配置参考

| 文件 | 作用 | 关键项 |
|------|------|--------|
| `config/model.yaml` | 模型工厂 | 按场景的 provider / model / api_key_env / kwargs |
| `config/tools.yaml` | 工具启停 | auto_discover / enabled_tools / disabled_tools |
| `config/permissions.yaml` | 权限策略 | default_tier / tools / rules / mcp / path_scope |
| `config/hooks.yaml` | Hook 注册 | auto_discover / hooks（dotted path 列表） |
| `config/kernel.yaml` | 内核参数 | max_context_tokens / keep_recent_messages / recursion_limit |
| `config/skills.yaml` | 技能层 | 关键词 / 优先级 / 激活预算 / always_on |
| `config/profile.yaml` | Profile 层 | section 顺序 / 文件映射 / 截断预算 |
| `config/prompt.yaml` | Prompt 来源 | system_prompt 指向 prompt/ 下文本 |
| `config/mcp.yaml` | MCP 接入 | enabled / servers（stdio/sse/streamable_http）/ 超时 |

---

## 📁 项目结构

```
anyclaw/
├── core/               # headless 内核（定制垂类 Agent 通常不需要碰）
│   ├── events.py       # 统一事件 schema
│   ├── state.py        # AgentState
│   ├── graph.py        # 显式图：context→agent→permission_gate→tools
│   ├── kernel.py       # AgentKernel facade
│   ├── projection.py   # STM JSON 投影（单写者）
│   └── harness/        # permissions / hooks / audit
├── cli/                # CLI 薄渲染器 + 主菜单
├── server/             # FastAPI（SSE / 会话 / 状态路由）
├── web/                # React + Vite 控制台
├── tools/              # ← 你的领域工具放这里（@tool 自动发现）
├── hooks/              # ← 你的 pre/post hook 放这里（自动发现）
├── skills/             # ← 你的领域方法论放这里
├── model/              # 模型工厂（多 provider）
├── utils/              # 会话/记忆/技能/profile/路径护栏/MCP 注册
├── config/             # 全部 yaml 配置
├── prompt/             # system prompt 文本
├── evals/              # 场景化 eval + 第二 AI 评审
├── tests/              # pytest 内核测试（fake model）
├── IDENTITY.md SOUL.md AGENT.md USER.md MEMORY.md TOOLS.md  # ← 你的人格层
├── memory/             # 运行时：checkpoint / STM / LTM / 审计
└── sandbox/            # 运行时：任务沙箱
```

---

## 🧪 测试与 Eval

```bash
pytest                                  # 42 个内核测试，fake model，无需 API key
python evals/runner.py                  # 3 个场景（CI 模式）
python evals/runner.py --real --judge   # 真实模型 + 第二 AI 评审 → evals/report.md
```

测试覆盖：图循环与事件顺序 · ask/never 权限与 interrupt/resume · 审计去重 · checkpoint 跨进程恢复 · 旧 JSON 会话种子恢复 · 压缩配对 · 投影 schema · ContextVar 跨 executor 传播 · 路径护栏（`..`/符号链接逃逸）· 工具语义 · MCP 加载容错与过权限门 · Web SSE 与权限流 · 会话 CRUD。

---

## 📝 许可证

本项目采用 **NON-COMMERCIAL LEARNING LICENSE 1.1**（非商业学习许可证）。

Copyright (c) 2024 relakkes@gmail.com

本软件及其相关文档文件（以下简称"软件"）在以下条件下授权使用：

#### 授权范围

版权所有者授予任何接受本许可证的自然人或法人实体（以下简称"用户"）免费、非独占、不可转让的权利，以非商业学习为目的使用、复制、修改和合并本软件。

#### 使用条件

1. 用户必须在软件及其副本的所有合理显著位置包含上述版权声明和本许可证声明
2. 软件仅限于学习和研究目的，不得用于大规模爬取或干扰平台运营的活动
3. 未经版权所有者书面同意，软件不得用于任何商业目的或对第三方造成不当影响

#### 免责声明

1. 软件按"现状"提供，不提供任何明示或暗示的保证，包括但不限于适销性、特定用途适用性和非侵权性的保证
2. 在任何情况下，版权所有者均不对因使用本软件或无法使用本软件而产生的任何直接、间接、偶然、特殊、示范性或后果性损害承担责任

#### 完整许可证

完整的许可证文本请查看 [LICENSE.txt](LICENSE.txt) 文件。

**注意**：如需商业使用，请联系版权所有者获取商业许可。

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

---

<div align="center">

**Made with ❤️ by AnyClaw Team**

</div>