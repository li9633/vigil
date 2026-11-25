"""内置检测模块。"""

from .base import (  # noqa: F401  便于外部统一导入
    CommandResult,
    CommandRunner,
    DetectContext,
    DetectionEngine,
    DetectorRegistry,
    default_registry,
    make_result,
)
