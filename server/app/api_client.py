"""兼容旧导入路径（M6 删除）：客户端路由已迁移到 `app.api.client`。"""

from __future__ import annotations

from app.api.client import router as router  # noqa: F401
