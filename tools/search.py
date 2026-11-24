"""搜索工具：glob 文件匹配与 grep 内容搜索（纯 Python，无外部依赖）。"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.tools import tool

from utils.path_guard import PathGuardError, resolve_tool_path
from utils.task_context import get_task_id

_MAX_FILE_SIZE = 1024 * 1024  # grep 跳过超过 1MB 的文件


def _ok(**fields: Any) -> str:
    return json.dumps({"ok": True, **fields}, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


@tool
def glob_files(pattern: str, path: str = ".") -> str:
    """
    描述：按 glob 模式递归匹配文件路径（如 "**/*.py"）。
    使用时机：需要按文件名模式查找文件时调用。
    输入：
    - pattern（必填）：glob 模式，如 "*.md" 或 "**/*.py"。
    - path（可选）：搜索根目录，相对路径基于当前任务沙箱目录，默认 "."。
    输出：JSON 字符串，含 ok、files（相对路径列表，最多 500 条）、truncated 字段。
    """
    try:
        base = resolve_tool_path(path, get_task_id(), write=False)
        if not base.is_dir():
            return _err(f"目录不存在: {path}")
        files = []
        for p in sorted(base.rglob(pattern)):
            if p.is_file():
                files.append(str(p.relative_to(base)))
                if len(files) >= 500:
                    break
        return _ok(files=files, truncated=len(files) >= 500)
    except PathGuardError as exc:
        return _err(str(exc))
    except Exception as exc:
        return _err(f"glob 失败: {exc}")


@tool
def grep(pattern: str, path: str = ".", file_glob: str = "*", max_results: int = 50) -> str:
    """
    描述：在文件内容中按正则搜索，返回匹配行（跳过二进制与超过 1MB 的文件）。
    使用时机：需要按内容查找代码或文本时调用。
    输入：
    - pattern（必填）：Python 正则表达式。
    - path（可选）：搜索根目录，相对路径基于当前任务沙箱目录，默认 "."。
    - file_glob（可选）：文件名过滤 glob，默认 "*"。
    - max_results（可选）：最多返回匹配行数，默认 50。
    输出：JSON 字符串，含 ok、matches（{file, line, text} 列表）、truncated 字段。
    """
    try:
        base = resolve_tool_path(path, get_task_id(), write=False)
        if not base.is_dir():
            return _err(f"目录不存在: {path}")
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return _err(f"正则无效: {exc}")

        max_results = max(1, int(max_results))
        matches = []
        for p in sorted(base.rglob(file_glob)):
            if not p.is_file() or p.stat().st_size > _MAX_FILE_SIZE:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append(
                        {"file": str(p.relative_to(base)), "line": lineno, "text": line[:300]}
                    )
                    if len(matches) >= max_results:
                        return _ok(matches=matches, truncated=True)
        return _ok(matches=matches, truncated=False)
    except PathGuardError as exc:
        return _err(str(exc))
    except Exception as exc:
        return _err(f"grep 失败: {exc}")