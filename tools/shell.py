"""Shell 工具：在任务沙箱内执行命令。默认 ask 级权限 + denylist 双保险。"""

from __future__ import annotations

import json
import subprocess
import time

from langchain_core.tools import tool

from utils.path import ensure_task_dirs, get_task_dir
from utils.task_context import get_task_id

_OUTPUT_CAP = 10000


@tool
def run_shell(command: str, timeout: int = 60) -> str:
    """
    描述：在当前任务沙箱目录内执行 shell 命令，返回退出码与输出（各截断至 10000 字符）。
    使用时机：需要运行命令（安装依赖、执行脚本、系统查询等）时调用。高风险命令会被权限策略拦截。
    输入：
    - command（必填）：要执行的 shell 命令。
    - timeout（可选）：超时秒数，默认 60。
    输出：JSON 字符串，含 ok、exit_code、stdout、stderr、truncated、duration_ms 字段。
    """
    task_id = get_task_id()
    if not task_id:
        return json.dumps(
            {"ok": False, "error": "未找到任务ID，拒绝执行 shell（无沙箱上下文）"},
            ensure_ascii=False,
        )
    try:
        cwd = ensure_task_dirs(task_id)
        timeout = max(1, int(timeout))
        start = time.monotonic()
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        truncated = len(stdout) > _OUTPUT_CAP or len(stderr) > _OUTPUT_CAP
        return json.dumps(
            {
                "ok": proc.returncode == 0,
                "exit_code": proc.returncode,
                "stdout": stdout[:_OUTPUT_CAP],
                "stderr": stderr[:_OUTPUT_CAP],
                "truncated": truncated,
                "duration_ms": duration_ms,
            },
            ensure_ascii=False,
        )
    except subprocess.TimeoutExpired:
        return json.dumps(
            {"ok": False, "error": f"命令超时（{timeout}s）", "exit_code": -1},
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"执行失败: {exc}"}, ensure_ascii=False)