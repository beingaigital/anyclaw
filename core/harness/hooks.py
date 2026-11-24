"""Hook 注册中心：pre_tool_use / post_tool_use。

两种来源：
- config/hooks.yaml 的 hooks 列表（dotted path 指向 callable）
- 自动发现 hooks/*.py 中暴露的 pre_tool_use / post_tool_use

导入失败 warn+skip，绝不破坏启动。
pre-hook 运行时异常 → fail-closed（拦截该次调用）；post-hook 异常 → 保留原结果。
"""

from __future__ import annotations

import importlib
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional

import yaml

from utils.path import get_config_path, get_project_root


@dataclass
class HookContext:
    """传给 hook 的工具调用上下文。"""

    session_id: str
    run_id: str
    tool_name: str
    tool_call_id: str
    args: dict


@dataclass
class HookResult:
    """pre-hook 返回值。action: allow | block | modify。"""

    action: Literal["allow", "block", "modify"] = "allow"
    args: Optional[dict] = None
    reason: str = ""


class HookRegistry:
    """pre/post hook 集合。"""

    def __init__(self) -> None:
        self._pre: list[Callable] = []
        self._post: list[Callable] = []

    @classmethod
    def load(cls, config_path: Path | None = None) -> "HookRegistry":
        registry = cls()
        path = config_path or get_config_path("hooks.yaml")
        raw: dict[str, Any] = {}
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    loaded = yaml.safe_load(fh) or {}
                if isinstance(loaded, dict):
                    raw = loaded
            except Exception as exc:
                warnings.warn(f"hooks.yaml 读取失败，跳过配置 hooks: {exc}")

        # 1. 配置列出的 callable
        hooks_cfg = raw.get("hooks")
        if isinstance(hooks_cfg, list):
            for dotted in hooks_cfg:
                dotted = str(dotted).strip()
                if not dotted:
                    continue
                fn = _import_callable(dotted)
                if fn is None:
                    continue
                name = getattr(fn, "__name__", "")
                if "post" in name:
                    registry._post.append(fn)
                else:
                    registry._pre.append(fn)

        # 2. 自动发现 hooks/*.py
        if bool(raw.get("auto_discover", True)):
            hooks_dir = get_project_root() / "hooks"
            if hooks_dir.is_dir():
                for py in sorted(hooks_dir.glob("*.py")):
                    if py.stem.startswith("_"):
                        continue
                    module_name = f"hooks.{py.stem}"
                    try:
                        module = importlib.import_module(module_name)
                    except Exception as exc:
                        warnings.warn(f"hook 模块导入失败，跳过 {module_name}: {exc}")
                        continue
                    pre = getattr(module, "pre_tool_use", None)
                    post = getattr(module, "post_tool_use", None)
                    if callable(pre):
                        registry._pre.append(pre)
                    if callable(post):
                        registry._post.append(post)

        return registry

    def run_pre(self, ctx: HookContext) -> HookResult:
        """依次执行 pre-hook。block 短路；modify 更新 args。异常 fail-closed。"""
        for fn in self._pre:
            try:
                result = fn(ctx)
            except Exception as exc:
                return HookResult(action="block", reason=f"pre-hook 异常: {exc}")
            if result is None:
                continue
            if isinstance(result, dict):
                result = HookResult(
                    action=result.get("action", "allow"),
                    args=result.get("args"),
                    reason=result.get("reason", ""),
                )
            if not isinstance(result, HookResult):
                continue
            if result.action == "block":
                return result
            if result.action == "modify" and isinstance(result.args, dict):
                ctx.args = result.args
        return HookResult(action="allow")

    def run_post(self, ctx: HookContext, result: str) -> str:
        """依次执行 post-hook，可修改结果字符串。异常保留原结果。"""
        for fn in self._post:
            try:
                modified = fn(ctx, result)
            except Exception as exc:
                warnings.warn(f"post-hook 异常，保留原结果: {exc}")
                continue
            if isinstance(modified, str):
                result = modified
        return result


def _import_callable(dotted: str) -> Optional[Callable]:
    """按 dotted path 导入 callable，失败 warn+skip。"""
    module_name, _, attr = dotted.rpartition(".")
    if not module_name:
        warnings.warn(f"hook 路径无效: {dotted}")
        return None
    try:
        module = importlib.import_module(module_name)
        fn = getattr(module, attr)
        if not callable(fn):
            warnings.warn(f"hook 不是 callable: {dotted}")
            return None
        return fn
    except Exception as exc:
        warnings.warn(f"hook 导入失败，跳过 {dotted}: {exc}")
        return None