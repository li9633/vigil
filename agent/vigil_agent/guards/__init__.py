"""Guard 领域模型：状态机、恢复链与配置校验。"""

from .fsm import (  # noqa: F401
    STATE_FAILED,
    STATE_GAVE_UP,
    STATE_OK,
    STATE_PENDING,
    STATE_RECOVERING,
    GuardStateMachine,
)
from .model import (  # noqa: F401
    ALLOWED_ACTIONS,
    BUILTIN_NETWORK_ID,
    BUILTIN_NETWORK_TEMPLATE,
    DEFAULT_ACTION_SAFETY,
    DEFAULT_POLICY,
    GuardConfigError,
    normalize_guard,
    validate_guards,
)
