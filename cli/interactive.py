"""交互式运行：薄渲染器，消费 AgentKernel 统一事件流。

持久化全部在内核（checkpoint + STM 投影），本模块只做渲染与权限确认。
run_session_loop 签名不变，cli/main.py 的懒导入 seam 保留。
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from typing import Any, AsyncIterator, Optional

from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, Prompt

from cli.display import (
    console,
    print_separator,
    print_status,
    print_token_usage,
    print_tool_call,
    print_tool_result,
)
from core.events import Event, EventType
from utils.path import ensure_task_dirs
from utils.session_manager import get_session_manager


def run_session_loop(task_id: Optional[str] = None) -> None:
    """运行会话循环（同步入口，内部 asyncio.run）。"""
    asyncio.run(_run_session_loop_async(task_id))


def _safe_for_console(text: str) -> str:
    """将文本降级为当前终端编码可输出的字符串，避免 Windows/GBK 下 Rich 渲染崩溃。"""
    if not text:
        return ""
    text = text.replace("•", "-").replace("👇", "↓")
    encoding = getattr(getattr(console, "file", None), "encoding", None) or "utf-8"
    try:
        return text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    except Exception:
        return text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")


class _RenderCtx:
    """一次会话渲染的可变状态（Live 显示 / spinner）。"""

    def __init__(self, task_id: str, show_spinner: bool = False):
        self.task_id = task_id
        self.live: Optional[Live] = None
        self.acc = ""
        self.has_output = False
        self.progress = None
        if show_spinner:
            self.progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True,
            )
            self.progress.start()
            self.progress.add_task("思考中...", total=None)

    def stop_spinner(self) -> None:
        if self.progress and not self.has_output:
            self.progress.stop()
            self.has_output = True

    def stop_live(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None
            console.print()
            self.acc = ""

    def cleanup(self) -> None:
        self.stop_live()
        if self.progress:
            self.progress.stop()
            self.progress = None


def _panel(content: str) -> Panel:
    return Panel(
        Markdown(_safe_for_console(content)),
        border_style="orange1",
        padding=(1, 2),
        expand=False,
    )


async def _render_stream(
    kernel: Any,
    gen: AsyncIterator[Event],
    ctx: _RenderCtx,
) -> None:
    """渲染一个事件流；permission_request → Confirm → 递归渲染 resume 流。"""
    task_id_display = ctx.task_id[:8] if len(ctx.task_id) > 8 else ctx.task_id
    try:
        async for ev in gen:
            t = ev.type
            p = ev.payload

            if t == EventType.TOKEN_DELTA:
                ctx.stop_spinner()
                ctx.acc += p.get("delta", "")
                if ctx.live is None:
                    console.print(f"[bold orange1]({task_id_display}) assistant:[/bold orange1]")
                    console.print()
                    ctx.live = Live(_panel(ctx.acc), console=console, refresh_per_second=8)
                    ctx.live.start()
                else:
                    ctx.live.update(_panel(ctx.acc))

            elif t == EventType.MESSAGE_COMPLETE:
                ctx.stop_live()

            elif t == EventType.TOOL_CALL_STARTED:
                ctx.stop_spinner()
                ctx.stop_live()
                print_tool_call(p.get("tool_name", "unknown"), p.get("args", {}))

            elif t == EventType.TOOL_RESULT:
                print_tool_result(p.get("tool_name", "unknown"), p.get("result", ""))

            elif t == EventType.PERMISSION_REQUEST:
                ctx.stop_spinner()
                ctx.stop_live()
                args_text = json.dumps(p.get("args", {}), ensure_ascii=False, indent=2)
                console.print(
                    Panel(
                        f"[bold]{p.get('tool_name', '')}[/bold] 请求执行\n{args_text}\n\n[dim]原因: {p.get('reason', '')}[/dim]",
                        title="权限确认",
                        border_style="yellow",
                    )
                )
                allow = Confirm.ask("允许执行?", default=False)
                remember = False
                if allow:
                    remember = Confirm.ask("本会话内始终允许该工具?", default=False)
                decision = {"action": "allow" if allow else "deny", "remember": remember}
                await _render_stream(
                    kernel,
                    kernel.resume(ctx.task_id, decision, p.get("interrupt_id", "")),
                    ctx,
                )

            elif t == EventType.PERMISSION_RESOLVED:
                action = p.get("action", "")
                print_status(f"权限决策: {'允许' if action == 'allow' else '拒绝'}", "info")

            elif t == EventType.COMPRESSION:
                console.print()
                console.print("[yellow]对话历史已压缩[/yellow]")
                summary = p.get("summary", "")
                if summary:
                    summary_display = summary[:200] + "..." if len(summary) > 200 else summary
                    console.print(f"[dim]{summary_display}[/dim]")
                console.print()

            elif t == EventType.USAGE:
                print_token_usage(
                    p.get("step", "agent"),
                    p.get("prompt_tokens", 0),
                    p.get("completion_tokens", 0),
                    p.get("total_tokens", 0),
                )

            elif t == EventType.ERROR:
                ctx.stop_spinner()
                ctx.stop_live()
                print_status(f"执行出错: {p.get('message', '')}", "error")

            elif t == EventType.RUN_FINISHED:
                ctx.stop_spinner()
                ctx.stop_live()
                status = p.get("status", "")
                if status == "completed":
                    totals = p.get("usage_totals", {})
                    if totals.get("total_tokens", 0) > 0:
                        print_separator()
                        print_token_usage(
                            "总计",
                            totals.get("prompt_tokens", 0),
                            totals.get("completion_tokens", 0),
                            totals.get("total_tokens", 0),
                        )
                    print_separator()
                    print_status("处理完成", "success")
                elif status == "error":
                    print_status("执行失败", "error")
                # awaiting_permission：resume 流已在递归中渲染，这里跳过

    except KeyboardInterrupt:
        ctx.cleanup()
        print_status("用户中断了执行（会话状态已保留，可恢复）", "warning")
        await gen.aclose()
    except Exception as exc:
        ctx.cleanup()
        print_status(f"执行出错: {exc}", "error")
        traceback.print_exc()
        await gen.aclose()


async def _run_session_loop_async(task_id: Optional[str]) -> None:
    from core.kernel import AgentKernel  # 懒导入：启动时不硬依赖模型配置

    kernel = await AgentKernel.create()
    session_manager = get_session_manager()
    try:
        if task_id is None:
            task_id = session_manager.create_session("新会话")
            ensure_task_dirs(task_id)
            print_status(f"会话已创建，任务 ID: {task_id}", "info")

            task_id_display = task_id[:8]
            console.print("[green]────────────────────────────────────────────────────────────[/green]")
            initial_query = Prompt.ask(f"[bold cyan]({task_id_display}) user[/bold cyan]")
            if not initial_query:
                console.print("[yellow]未输入查询，退出。[/yellow]")
                return

            session_data = session_manager.load_session(task_id)
            if session_data:
                session_data["initial_query"] = initial_query
                session_manager.save_session(task_id, session_data, final_query=initial_query)

            ctx = _RenderCtx(task_id, show_spinner=True)
            await _render_stream(kernel, kernel.astream(task_id, initial_query), ctx)
            ctx.cleanup()
        else:
            session_data = session_manager.load_session(task_id)
            if not session_data:
                console.print(f"[red]无法加载会话: {task_id}[/red]")
                return
            ensure_task_dirs(task_id)
            print_status(f"已恢复会话: {task_id}", "info")
            print_status(f"描述: {session_data.get('description', 'N/A')}", "info")

        while True:
            task_id_display = task_id[:8] if len(task_id) > 8 else task_id
            console.print("[green]────────────────────────────────────────────────────────────[/green]")
            user_input = Prompt.ask(f"[bold cyan]({task_id_display}) user[/bold cyan]")
            if not user_input:
                continue

            if user_input.strip().startswith("/"):
                if user_input.strip() == "/exit":
                    print_status(f"任务目录: sandbox/{task_id}/", "info")
                    console.print(f"\n[cyan]会话已保存，已退出到主页面[/cyan]\n")
                    return
                console.print(f"[yellow]在会话中，只能使用 /exit 退出会话[/yellow]")
                continue

            ctx = _RenderCtx(task_id, show_spinner=True)
            await _render_stream(kernel, kernel.astream(task_id, user_input), ctx)
            ctx.cleanup()

    except (KeyboardInterrupt, EOFError):
        console.print(f"\n\n[cyan]会话已保存，感谢使用 [bold magenta]AnyClaw[/bold magenta]！[/cyan]\n")
        sys.exit(0)
    finally:
        await kernel.close()