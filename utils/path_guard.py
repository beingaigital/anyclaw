"""路径护栏：工具文件访问范围强制。

- 写默认仅限 sandbox/{task_id}（额外根经 config/permissions.yaml path_scope.write_roots）
- 读默认项目根 + sandbox（额外根经 read_roots）
- 先 resolve() 再检查，防 .. 与符号链接逃逸
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from utils.path import get_config_path, get_project_root, get_sandbox_dir, get_task_dir


class PathGuardError(Exception):
    """路径超出允许范围。"""


def _extra_roots(kind: str) -> list[Path]:
    path = get_config_path("permissions.yaml")
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        scope = raw.get("path_scope") if isinstance(raw.get("path_scope"), dict) else {}
        roots = scope.get(kind) or []
        return [Path(str(p)).expanduser().resolve() for p in roots if str(p).strip()]
    except Exception:
        return []


def resolve_tool_path(path: str, task_id: Optional[str], *, write: bool) -> Path:
    """解析并校验工具路径。相对路径基于任务沙箱；越界抛 PathGuardError。"""
    raw = (path or "").strip()
    if not raw:
        raise PathGuardError("路径不能为空")

    p = Path(raw).expanduser()
    if not p.is_absolute():
        base = get_task_dir(task_id) if task_id else get_sandbox_dir()
        p = base / p
    resolved = p.resolve()

    if write:
        allowed: list[Path] = []
        if task_id:
            allowed.append(get_task_dir(task_id).resolve())
        allowed.extend(_extra_roots("write_roots"))
    else:
        allowed = [get_project_root().resolve(), get_sandbox_dir().resolve()]
        allowed.extend(_extra_roots("read_roots"))

    for root in allowed:
        if resolved == root or root in resolved.parents:
            return resolved

    scope = "写范围" if write else "读范围"
    raise PathGuardError(f"路径超出允许的{scope}: {resolved}")