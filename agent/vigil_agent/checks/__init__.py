"""Check 领域包：模型、Expectation 与执行器。"""

from .expect import EXPECTATIONS, evaluate, validate_expect  # noqa: F401
from .model import (  # noqa: F401
    CheckConfigError,
    check_map,
    dependency_closure,
    dependency_graph,
    normalize_check,
    normalize_checks,
    ordered_checks,
    validate_checks,
)
from .runner import (  # noqa: F401
    ABNORMAL_STATUSES,
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_SKIPPED,
    STATUS_UNKNOWN,
    CheckRunner,
    aggregate_status,
)
