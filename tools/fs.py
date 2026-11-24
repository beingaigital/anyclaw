"""文件工具：读 / 写 / 编辑 / 列目录，带沙箱路径护栏。"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from utils.path import ensure_task_dirs
from utils.path_guard import PathGuardError, resolve_tool_path
from utils.task_context import get_task_id


def _ok(**fields: Any) -> str:
    return json.dumps({"ok": True, **fields}, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


@tool
def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
    """
    描述：读取文本文件内容（按行分页）。
    使用时机：需要查看文件内容时调用。
    输入：
    - path（必填）：文件路径，相对路径基于当前任务沙箱目录。
    - offset（可选）：起始行号，从 0 开始，默认 0。
    - limit（可选）：最多读取行数，默认 2000。
    输出：JSON 字符串，含 ok、content、path、total_lines、offset、truncated 字段。
    """
    try:
        resolved = resolve_tool_path(path, get_task_id(), write=False)
        if not resolved.is_file():
            return _err(f"文件不存在: {path}")
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        offset = max(0, int(offset))
        limit = max(1, int(limit))
        selected = lines[offset : offset + limit]
        return _ok(
            path=str(resolved),
            content="\n".join(selected),
            total_lines=len(lines),
            offset=offset,
            truncated=offset + limit < len(lines),
        )
    except PathGuardError as exc:
        return _err(str(exc))
    except Exception as exc:
        return _err(f"读取失败: {exc}")


@tool
def write_file(path: str, content: str) -> str:
    """
    描述：写入文本文件（覆盖已有内容），自动创建父目录。写操作仅限任务沙箱内。
    使用时机：需要创建或覆盖文件时调用。
    输入：
    - path（必填）：文件路径，相对路径基于当前任务沙箱目录。
    - content（必填）：要写入的完整文本内容。
    输出：JSON 字符串，含 ok、path、bytes_written 字段。
    """
    try:
        task_id = get_task_id()
        resolved = resolve_tool_path(path, task_id, write=True)
        if task_id:
            ensure_task_dirs(task_id)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return _ok(path=str(resolved), bytes_written=len(content.encode("utf-8")))
    except PathGuardError as exc:
        return _err(str(exc))
    except Exception as exc:
        return _err(f"写入失败: {exc}")


@tool
def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """
    描述：精确匹配替换文件中的文本片段。old_string 必须唯一匹配（除非 replace_all）。
    使用时机：需要修改已有文件的局部内容时调用。
    输入：
    - path（必填）：文件路径，相对路径基于当前任务沙箱目录。
    - old_string（必填）：要被替换的原文本（精确匹配）。
    - new_string（必填）：替换后的新文本。
    - replace_all（可选）：是否替换所有匹配，默认 False。
    输出：JSON 字符串，含 ok、path、replacements 字段。
    """
    try:
        resolved = resolve_tool_path(path, get_task_id(), write=True)
        if not resolved.is_file():
            return _err(f"文件不存在: {path}")
        text = resolved.read_text(encoding="utf-8", errors="replace")
        count = text.count(old_string)
        if count == 0:
            return _err("old_string 未在文件中匹配到任何内容")
        if count > 1 and not replace_all:
            return _err(f"old_string 匹配到 {count} 处，需唯一匹配或设置 replace_all=true")
        new_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        resolved.write_text(new_text, encoding="utf-8")
        return _ok(path=str(resolved), replacements=count if replace_all else 1)
    except PathGuardError as exc:
        return _err(str(exc))
    except Exception as exc:
        return _err(f"编辑失败: {exc}")


@tool
def list_dir(path: str = ".") -> str:
    """
    描述：列出目录内容（名称、类型、大小）。
    使用时机：需要查看目录结构时调用。
    输入：
    - path（可选）：目录路径，相对路径基于当前任务沙箱目录，默认 "."。
    输出：JSON 字符串，含 ok、path、entries 字段（entries 为 {name, type, size} 列表）。
    """
    try:
        resolved = resolve_tool_path(path, get_task_id(), write=False)
        if not resolved.is_dir():
            return _err(f"目录不存在: {path}")
        entries = []
        for item in sorted(resolved.iterdir()):
            entries.append(
                {
                    "name": item.name,
                    "type": "dir" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                }
            )
        return _ok(path=str(resolved), entries=entries)
    except PathGuardError as exc:
        return _err(str(exc))
    except Exception as exc:
        return _err(f"列目录失败: {exc}")