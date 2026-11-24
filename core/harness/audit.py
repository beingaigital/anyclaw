"""审计日志：memory/audit/{YYYY-MM-DD}.jsonl。

记录每个工具调用的权限决策与结果摘要，供追溯与 gate 去重。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from utils.path import get_audit_dir


class AuditLog:
    """追加式 JSONL 审计日志。"""

    def __init__(self, audit_dir: Path | None = None):
        self.audit_dir = audit_dir or get_audit_dir()
        self.audit_dir.mkdir(parents=True, exist_ok=True)

    def _file_for(self, day: datetime) -> Path:
        return self.audit_dir / f"{day.strftime('%Y-%m-%d')}.jsonl"

    def append(self, entry: dict[str, Any]) -> None:
        """追加一条审计记录（自动补 ts）。写入失败不中断主流程。"""
        record = {"ts": datetime.now().isoformat(), **entry}
        try:
            with open(self._file_for(datetime.now()), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    def has_entry(self, session_id: str, tool_call_id: str, kind: str = "decision") -> bool:
        """检查今天/昨天是否已有指定 (session_id, tool_call_id, kind) 记录。

        用于 permission_gate 重跑（interrupt resume）时的决策去重。
        """
        now = datetime.now()
        for day in (now, now - timedelta(days=1)):
            path = self._file_for(day)
            if not path.exists():
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        if (
                            obj.get("session_id") == session_id
                            and obj.get("tool_call_id") == tool_call_id
                            and obj.get("kind") == kind
                        ):
                            return True
            except Exception:
                continue
        return False