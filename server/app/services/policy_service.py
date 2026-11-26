"""运行时策略配置用例：读取、部分更新、审计与提交。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.errors import ValidationError
from app.logstore import log_event
from app.policies import default_policies, load_policies, save_policies
from app.repositories import audit_repo
from app.schemas.admin import PolicyResponse
from app.settings import Settings


def get_policies(db: Session, settings: Settings) -> PolicyResponse:
    return PolicyResponse(
        policies=load_policies(db, settings),
        defaults=default_policies(settings),
    )


def update_policies(
    db: Session, settings: Settings, payload: dict[str, Any], *, actor: str
) -> PolicyResponse:
    try:
        policies = save_policies(db, payload, settings)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    audit_repo.add_audit(
        db,
        actor=actor,
        action="update_policies",
        target="policies",
        detail=policies,
    )
    db.commit()
    log_event("info", "policies.update", "运行时策略已更新", actor=actor, policies=policies)
    return PolicyResponse(
        policies=policies,
        defaults=default_policies(settings),
    )
