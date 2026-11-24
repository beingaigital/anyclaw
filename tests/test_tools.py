"""真实工具：fs 往返 / edit 语义 / shell 超时截断 / grep glob 上限。"""

from __future__ import annotations

import json
import sys

from tools.fs import edit_file, list_dir, read_file, write_file
from tools.search import glob_files, grep
from tools.shell import run_shell
from utils.path import ensure_task_dirs
from utils.task_context import set_task_id


def _invoke(tool, args):
    return json.loads(tool.invoke(args))


def test_fs_roundtrip(project_env):
    set_task_id("t1")
    ensure_task_dirs("t1")

    r = _invoke(write_file, {"path": "a.txt", "content": "hello"})
    assert r["ok"] and r["bytes_written"] == 5

    r = _invoke(read_file, {"path": "a.txt"})
    assert r["ok"] and r["content"] == "hello" and r["total_lines"] == 1

    r = _invoke(edit_file, {"path": "a.txt", "old_string": "hello", "new_string": "world"})
    assert r["ok"] and r["replacements"] == 1
    assert _invoke(read_file, {"path": "a.txt"})["content"] == "world"

    r = _invoke(list_dir, {"path": "."})
    assert r["ok"] and any(e["name"] == "a.txt" for e in r["entries"])


def test_write_outside_sandbox_blocked(project_env):
    set_task_id("t1")
    ensure_task_dirs("t1")
    r = _invoke(write_file, {"path": "../evil.txt", "content": "x"})
    assert not r["ok"] and "范围" in r["error"]


def test_edit_ambiguous_requires_replace_all(project_env):
    set_task_id("t1")
    ensure_task_dirs("t1")
    _invoke(write_file, {"path": "b.txt", "content": "x x"})
    r = _invoke(edit_file, {"path": "b.txt", "old_string": "x", "new_string": "y"})
    assert not r["ok"]
    r = _invoke(
        edit_file, {"path": "b.txt", "old_string": "x", "new_string": "y", "replace_all": True}
    )
    assert r["ok"] and r["replacements"] == 2


def test_read_missing_file(project_env):
    set_task_id("t1")
    r = _invoke(read_file, {"path": "nope.txt"})
    assert not r["ok"]


def test_shell_echo(project_env):
    set_task_id("t1")
    r = _invoke(run_shell, {"command": f'"{sys.executable}" -c "print(\'hi\')"', "timeout": 30})
    assert r["ok"] and r["exit_code"] == 0 and "hi" in r["stdout"]


def test_shell_timeout(project_env):
    set_task_id("t1")
    r = _invoke(
        run_shell,
        {"command": f'"{sys.executable}" -c "import time; time.sleep(10)"', "timeout": 1},
    )
    assert not r["ok"] and "超时" in r["error"]


def test_shell_requires_task_id(project_env):
    set_task_id(None)
    r = _invoke(run_shell, {"command": "echo hi"})
    assert not r["ok"]


def test_grep_and_glob(project_env):
    set_task_id("t1")
    ensure_task_dirs("t1")
    _invoke(write_file, {"path": "code.py", "content": "def foo():\n    return 42\n"})
    _invoke(write_file, {"path": "notes.md", "content": "foo bar\n"})

    r = _invoke(grep, {"pattern": "foo", "path": "."})
    assert r["ok"] and len(r["matches"]) == 2

    r = _invoke(glob_files, {"pattern": "**/*.py", "path": "."})
    assert r["ok"] and "code.py" in r["files"]

    r = _invoke(grep, {"pattern": "[invalid", "path": "."})
    assert not r["ok"]