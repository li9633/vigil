"""兼容旧导入路径（M6 删除）：管理端路由已迁移到 `app.api.admin`。"""

from __future__ import annotations

from app.api.admin import router as router  # noqa: F401
