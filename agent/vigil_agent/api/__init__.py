"""本地 HTTP API 控制层：只做路由、鉴权与响应封装。

业务数据通过 provider/handler 回调注入，本包不反向依赖 `AgentApp`。
"""

from .server import VigilHTTPServer, create_server

__all__ = ["VigilHTTPServer", "create_server"]
