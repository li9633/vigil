"""管理端只读探测器目录接口。

Check / Guard 由客户端本地管理，服务端不再提供配置写入；
客户端详情中的检测项与守护项均来自最近一次心跳快照。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.schemas.admin import ProbeCatalogResponse
from app.services import check_service

router = APIRouter()


@router.get("/clients/{client_id}/probes")
def get_client_probes(
    client_id: str,
    db: Session = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> ApiResponse[ProbeCatalogResponse]:
    return ok(check_service.get_probe_catalog(db, client_id))
