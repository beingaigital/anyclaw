"""测试夹具：把项目根隔离到 tmp_path，重置所有模块级单例/缓存。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

# 确保项目根可导入（editable install 之外的保险）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from core.harness import HookRegistry, PermissionPolicy
from core.kernel import AgentKernel
from tests.fake_model import ScriptedChatModel


@pytest.fixture(autouse=True)
def project_env(tmp_path, monkeypatch):
    """隔离项目根 + 重置单例，避免测试污染真实 memory/sandbox。"""
    import utils.path as path_mod

    monkeypatch.setattr(path_mod, "_PROJECT_ROOT", tmp_path)
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "skills").mkdir(exist_ok=True)

    import utils.session_manager as sm

    monkeypatch.setattr(sm, "_session_manager", None)

    import utils.profile_loader as pl

    monkeypatch.setattr(pl, "_profile_cache", None)
    monkeypatch.setattr(pl, "_profile_cache_key", None)

    import utils.skill_registry as sr

    monkeypatch.setattr(sr, "_registry_cache", None)
    monkeypatch.setattr(sr, "_registry_cache_key", None)

    import utils.tool_registry as tr

    monkeypatch.setattr(tr, "_registry_cache", None)
    monkeypatch.setattr(tr, "_registry_cache_key", None)

    yield tmp_path


@pytest_asyncio.fixture
async def make_kernel(tmp_path):
    """构造注入 fake model / 工具 / 策略的内核；teardown 关闭 saver。"""
    created = []

    async def _make(
        responses,
        tools,
        policy=None,
        db_path=None,
        hooks=None,
    ):
        model = ScriptedChatModel(responses=list(responses))
        saver_cm = AsyncSqliteSaver.from_conn_string(str(db_path or (tmp_path / "ckpt.sqlite")))
        saver = await saver_cm.__aenter__()
        await saver.setup()
        kernel = await AgentKernel.create(
            model=model,
            tools=list(tools),
            policy=policy or PermissionPolicy(default_tier="always"),
            hooks=hooks or HookRegistry(),
            checkpointer=saver,
        )
        created.append((kernel, saver_cm, model))
        return kernel

    yield _make

    for kernel, saver_cm, _model in created:
        try:
            await saver_cm.__aexit__(None, None, None)
        except Exception:
            pass