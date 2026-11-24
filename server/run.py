"""Web 控制台启动入口：anyclaw web [--port N]。默认绑 127.0.0.1（单用户本地，无认证）。"""

from __future__ import annotations


def run_web(port: int = 8000, host: str = "127.0.0.1") -> None:
    import uvicorn

    from server.app import create_app

    print(f"AnyClaw Web 控制台: http://{host}:{port}")
    uvicorn.run(create_app(), host=host, port=port)