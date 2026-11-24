"""权限策略：Always / Ask / Never 三级 + 参数模式规则。

评估顺序：
1. arg-pattern 规则（命中即返回，never 优先）
2. 会话级 allowlist（用户在本会话标记过"始终允许"）
3. per-tool tier（精确名优先，支持 glob 如 weather_*）
4. MCP server tier（仅对已登记的 MCP 工具）
5. default_tier

prompt 级约束只是请求，这里才是工具级强制。
"""

from __future__ import annotations

import fnmatch
import json
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import yaml

from utils.path import get_config_path

Tier = Literal["always", "ask", "never"]
_VALID_TIERS = {"always", "ask", "never"}


@dataclass
class PermissionDecision:
    """一次权限评估的结果。"""

    tier: Tier
    reason: str
    source: str  # rule | session_allowlist | tool | mcp_server | mcp_default | default


@dataclass
class _Rule:
    tool: str  # 工具名或 glob
    pattern: str
    action: Tier
    reason: str
    arg: Optional[str] = None  # 指定参数名；缺省对 json.dumps(args) 匹配


class PermissionPolicy:
    """从 config/permissions.yaml 加载的权限策略。"""

    def __init__(
        self,
        *,
        default_tier: Tier = "ask",
        tool_tiers: Optional[dict[str, Tier]] = None,
        rules: Optional[list[_Rule]] = None,
        mcp_default_tier: Tier = "ask",
        mcp_servers: Optional[dict[str, Tier]] = None,
        path_scope: Optional[dict[str, list[str]]] = None,
    ):
        self.default_tier = default_tier
        self.tool_tiers = tool_tiers or {}
        self.rules = rules or []
        self.mcp_default_tier = mcp_default_tier
        self.mcp_servers = mcp_servers or {}
        self.path_scope = path_scope or {}
        # 由内核登记的 MCP 工具名集合（用于区分本地工具与 MCP 工具）
        self.mcp_tool_names: set[str] = set()

    @classmethod
    def load(cls, path: Path | None = None) -> "PermissionPolicy":
        """加载策略；配置缺失/损坏时回退安全默认（default_tier=ask）。"""
        config_path = path or get_config_path("permissions.yaml")
        raw: dict[str, Any] = {}
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as fh:
                    loaded = yaml.safe_load(fh) or {}
                if isinstance(loaded, dict):
                    raw = loaded
                else:
                    warnings.warn("permissions.yaml 格式错误（应为字典），使用默认策略")
            except Exception as exc:
                warnings.warn(f"permissions.yaml 读取失败，使用默认策略: {exc}")

        default_tier = _normalize_tier(raw.get("default_tier"), "ask")

        tool_tiers: dict[str, Tier] = {}
        tools_cfg = raw.get("tools")
        if isinstance(tools_cfg, dict):
            for name, tier in tools_cfg.items():
                tool_tiers[str(name).strip()] = _normalize_tier(tier, default_tier)

        rules: list[_Rule] = []
        rules_cfg = raw.get("rules")
        if isinstance(rules_cfg, list):
            for item in rules_cfg:
                if not isinstance(item, dict):
                    continue
                pattern = str(item.get("pattern", "")).strip()
                tool = str(item.get("tool", "*")).strip()
                if not pattern or not tool:
                    continue
                try:
                    re.compile(pattern)
                except re.error as exc:
                    warnings.warn(f"permissions.yaml 规则正则无效，跳过: {pattern} ({exc})")
                    continue
                arg = item.get("arg")
                rules.append(
                    _Rule(
                        tool=tool,
                        pattern=pattern,
                        action=_normalize_tier(item.get("action"), "never"),
                        reason=str(item.get("reason", "命中参数规则")),
                        arg=str(arg).strip() if arg else None,
                    )
                )

        mcp_cfg = raw.get("mcp") if isinstance(raw.get("mcp"), dict) else {}
        mcp_default_tier = _normalize_tier(mcp_cfg.get("default_tier"), "ask")
        mcp_servers: dict[str, Tier] = {}
        servers_cfg = mcp_cfg.get("servers")
        if isinstance(servers_cfg, dict):
            for name, tier in servers_cfg.items():
                mcp_servers[str(name).strip()] = _normalize_tier(tier, mcp_default_tier)

        path_scope_cfg = raw.get("path_scope") if isinstance(raw.get("path_scope"), dict) else {}
        path_scope = {
            "write_roots": [str(p) for p in (path_scope_cfg.get("write_roots") or [])],
            "read_roots": [str(p) for p in (path_scope_cfg.get("read_roots") or [])],
        }

        return cls(
            default_tier=default_tier,
            tool_tiers=tool_tiers,
            rules=rules,
            mcp_default_tier=mcp_default_tier,
            mcp_servers=mcp_servers,
            path_scope=path_scope,
        )

    def evaluate(
        self,
        tool_name: str,
        args: dict,
        session_allowlist: Optional[list[str]] = None,
    ) -> PermissionDecision:
        """评估一次工具调用的权限等级。"""
        args = args or {}

        # 1. 参数模式规则
        for rule in self.rules:
            if not fnmatch.fnmatch(tool_name, rule.tool):
                continue
            if rule.arg is not None:
                target = str(args.get(rule.arg, ""))
            else:
                target = json.dumps(args, ensure_ascii=False, default=str)
            if re.search(rule.pattern, target):
                return PermissionDecision(tier=rule.action, reason=rule.reason, source="rule")

        # 2. 会话级 allowlist
        if session_allowlist and tool_name in session_allowlist:
            return PermissionDecision(
                tier="always", reason="本会话已标记始终允许", source="session_allowlist"
            )

        # 3. per-tool tier：精确名优先，再 glob
        if tool_name in self.tool_tiers:
            return PermissionDecision(
                tier=self.tool_tiers[tool_name], reason="工具级配置", source="tool"
            )
        for pattern, tier in self.tool_tiers.items():
            if ("*" in pattern or "?" in pattern) and fnmatch.fnmatch(tool_name, pattern):
                return PermissionDecision(tier=tier, reason=f"工具级配置({pattern})", source="tool")

        # 4. MCP 工具
        if tool_name in self.mcp_tool_names:
            server = tool_name.split("_", 1)[0]
            if server in self.mcp_servers:
                return PermissionDecision(
                    tier=self.mcp_servers[server],
                    reason=f"MCP server({server}) 配置",
                    source="mcp_server",
                )
            return PermissionDecision(
                tier=self.mcp_default_tier, reason="MCP 默认策略", source="mcp_default"
            )

        # 5. 默认
        return PermissionDecision(tier=self.default_tier, reason="默认策略", source="default")


def _normalize_tier(value: Any, fallback: Tier) -> Tier:
    tier = str(value or "").strip().lower()
    if tier in _VALID_TIERS:
        return tier  # type: ignore[return-value]
    if value is not None:
        warnings.warn(f"无效的权限等级: {value!r}，回退 {fallback}")
    return fallback