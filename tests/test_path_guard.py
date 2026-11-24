"""路径护栏：越界 / .. 逃逸 / 符号链接逃逸 / 读写范围 / 额外根配置。"""

from __future__ import annotations

import pytest

from utils.path import ensure_task_dirs
from utils.path_guard import PathGuardError, resolve_tool_path


def test_write_inside_sandbox_ok(project_env):
    task_dir = ensure_task_dirs("t1")
    p = resolve_tool_path("out.txt", "t1", write=True)
    assert p == (task_dir / "out.txt").resolve()


def test_write_dotdot_escape_blocked(project_env):
    ensure_task_dirs("t1")
    with pytest.raises(PathGuardError):
        resolve_tool_path("../escape.txt", "t1", write=True)


def test_write_absolute_outside_blocked(project_env):
    ensure_task_dirs("t1")
    with pytest.raises(PathGuardError):
        resolve_tool_path(str(project_env.parent / "evil.txt"), "t1", write=True)


def test_write_without_task_id_blocked(project_env):
    with pytest.raises(PathGuardError):
        resolve_tool_path("x.txt", None, write=True)


def test_symlink_escape_blocked(project_env):
    task_dir = ensure_task_dirs("t1")
    outside = project_env / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    (task_dir / "link.txt").symlink_to(outside)
    with pytest.raises(PathGuardError):
        resolve_tool_path("link.txt", "t1", write=True)


def test_read_project_root_ok(project_env):
    p = resolve_tool_path("sub/file.txt", None, write=False)
    assert str(p).startswith(str(project_env))


def test_read_outside_blocked(project_env):
    with pytest.raises(PathGuardError):
        resolve_tool_path(str(project_env.parent / "secret.txt"), None, write=False)


def test_extra_write_root_from_config(project_env):
    extra = project_env / "extra"
    extra.mkdir()
    (project_env / "config" / "permissions.yaml").write_text(
        f"path_scope:\n  write_roots:\n    - {extra}\n", encoding="utf-8"
    )
    p = resolve_tool_path(str(extra / "ok.txt"), "t1", write=True)
    assert p == (extra / "ok.txt").resolve()