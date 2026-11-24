"""状态路由：工具 / 模型 / profile / skills 查看与热重载（复用 CLI 同一套加载器）。"""

from __future__ import annotations

import yaml
from fastapi import APIRouter, Request

from utils.path import get_config_path
from utils.profile_loader import get_profile_context
from utils.skill_registry import get_active_skills_for_query, get_skill_registry
from utils.tool_registry import get_tool_registry

router = APIRouter(prefix="/api", tags=["status"])


def _tools_payload(request: Request) -> dict:
    registry = get_tool_registry()
    kernel = getattr(request.app.state, "kernel", None)
    return {
        "enabled": [
            {"name": t.name, "description": t.description, "module": t.module}
            for t in registry.enabled_tools
        ],
        "disabled": registry.disabled_tools,
        "module_errors": registry.module_errors,
        "mcp_status": getattr(kernel, "mcp_status", {}),
    }


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/tools")
def tools_status(request: Request):
    return _tools_payload(request)


@router.post("/tools/reload")
async def tools_reload(request: Request):
    kernel = request.app.state.kernel
    await kernel.reload_tools()
    get_tool_registry(force_reload=True)
    return _tools_payload(request)


@router.get("/models")
def models_status():
    path = get_config_path("model.yaml")
    if not path.exists():
        return {"models": {}}
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return {"models": raw}


@router.get("/profile")
def profile_status():
    ctx = get_profile_context()
    return {
        "enabled": ctx.enabled,
        "sections": [
            {
                "key": key,
                "file": str(ctx.sections[key].file_path.name),
                "exists": ctx.sections[key].exists,
                "chars": len(ctx.sections[key].content),
                "truncated": ctx.sections[key].truncated,
                "error": ctx.sections[key].error,
            }
            for key in ctx.section_order
            if key in ctx.sections
        ],
    }


@router.post("/profile/reload")
def profile_reload():
    get_profile_context(force_reload=True)
    return profile_status()


@router.get("/skills")
def skills_status(query: str | None = None):
    registry = get_skill_registry()
    skills = [
        {
            "skill_id": s.skill_id,
            "title": s.title,
            "enabled": s.enabled,
            "priority": s.priority,
            "keywords": s.keywords,
            "summary": s.summary,
            "source": s.source,
            "error": s.error,
        }
        for s in registry.skills.values()
    ]
    payload: dict = {"skills": skills, "load_errors": registry.load_errors}
    if query:
        payload["active_for_query"] = [
            s.skill_id for s in get_active_skills_for_query(query)
        ]
    return payload


@router.post("/skills/reload")
def skills_reload():
    get_skill_registry(force_reload=True)
    return skills_status()