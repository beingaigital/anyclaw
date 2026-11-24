"""FastAPI app 工厂：lifespan 管理内核生命周期；web/dist 存在则单端口服务前端。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from core.kernel import AgentKernel
from utils.path import get_project_root


def create_app(kernel: Optional[AgentKernel] = None) -> FastAPI:
    """创建 app。kernel 可注入（测试）；缺省在 lifespan 内创建并持有。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if kernel is None:
            app.state.kernel = await AgentKernel.create()
            app.state.owns_kernel = True
        else:
            app.state.kernel = kernel
            app.state.owns_kernel = False
        try:
            yield
        finally:
            if app.state.owns_kernel:
                await app.state.kernel.close()

    app = FastAPI(title="AnyClaw Console", lifespan=lifespan)

    from server.routes_chat import router as chat_router
    from server.routes_sessions import router as sessions_router
    from server.routes_status import router as status_router

    app.include_router(sessions_router)
    app.include_router(chat_router)
    app.include_router(status_router)

    # 生产模式：服务构建后的前端（npm run build → web/dist）
    dist = get_project_root() / "web" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="web")

    return app