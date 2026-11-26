"""配置加载、环境变量覆盖与校验（schema v3）。

配置文件默认位于 /etc/vigil/config.json。schema v3 直接使用顶层 `checks` 与
`guards`，不再有 `detect` 段，也不读取或迁移 V1/V2 配置。
"""

import copy
import json
import logging
import os
import tempfile
from urllib.parse import urlsplit

from .checks.model import CheckConfigError, normalize_checks, validate_checks
from .guards.model import (
    BUILTIN_NETWORK_ID,
    BUILTIN_NETWORK_TEMPLATE,
    DEFAULT_ACTION_SAFETY,
    DEFAULT_POLICY,
    GuardConfigError,
    normalize_guard,
    validate_guards,
)
from .probes.builtin import builtin_registry

LOGGER = logging.getLogger(__name__)

# 默认配置文件路径
DEFAULT_CONFIG_PATH = "/etc/vigil/config.json"

DEFAULT_CHECKS = [
    {
        "id": "wan-link",
        "name": "WAN 物理链路",
        "probe": "carrier",
        "params": {"device": "auto"},
        "expect": {"kind": "carrier", "up": True},
        "timeout": 3,
        "depends_on": [],
        "enabled": True,
    },
    {
        "id": "pppoe-session",
        "name": "PPPoE 会话",
        "probe": "pppoe",
        "params": {"interface": "wan"},
        "expect": {"kind": "pppoe", "up": True},
        "timeout": 3,
        "depends_on": ["wan-link"],
        "enabled": True,
    },
    {
        "id": "default-route",
        "name": "默认路由",
        "probe": "route",
        "params": {"interface": "wan"},
        "expect": {"kind": "route", "has_route": True},
        "timeout": 3,
        "depends_on": ["pppoe-session"],
        "enabled": True,
    },
    {
        "id": "dns",
        "name": "DNS 解析",
        "probe": "dns",
        "params": {"name": "www.baidu.com"},
        "expect": {"kind": "dns", "resolved": True},
        "timeout": 3,
        "depends_on": ["default-route"],
        "enabled": True,
    },
    {
        "id": "http204",
        "name": "HTTP 204",
        "probe": "http",
        "params": {"url": "http://connect.rom.miui.com/generate_204"},
        "expect": {"kind": "http", "status": [204]},
        "timeout": 5,
        "depends_on": ["default-route"],
        "enabled": True,
    },
]

DEFAULT_GUARDS = [
    {
        "id": BUILTIN_NETWORK_ID,
        "name": "断网自动恢复",
        "template": BUILTIN_NETWORK_TEMPLATE,
        "enabled": True,
        "check_refs": [
            "wan-link",
            "pppoe-session",
            "default-route",
            "dns",
            "http204",
        ],
        "evaluate": {"mode": "dependency_tree", "quorum": 1},
        "policy": copy.deepcopy(DEFAULT_POLICY),
        "recovery": [{"action": "ifup_wan", "verify_after": 20}],
        "limits": {"max_risk_level": "L1"},
    }
]

# 缺省配置：与设计文档 11.1 保持一致
DEFAULT_CONFIG = {
    "schema_version": 3,
    "server": {
        "base_url": "",
        "client_id": "",
        "secret": "",
        "bootstrap_token": "",
        "heartbeat_interval": 30,
        "config_poll_interval": 300,
        "verify_tls": True,
    },
    "checks": copy.deepcopy(DEFAULT_CHECKS),
    "guards": copy.deepcopy(DEFAULT_GUARDS),
    "action_safety": copy.deepcopy(DEFAULT_ACTION_SAFETY),
    # 动作运行时参数（非安全天花板，天花板在 action_safety）
    "action": {
        "cooldown_seconds": 300,
        "allowed_services": ["dnsmasq"],
        "timeout": 30,
        "lock_path": "/var/run/vigil/action.lock",
        "wan_interface": "wan",
    },
    "httpd": {
        "bind": "0.0.0.0",
        "port": 8787,
        "token": "",
        "session_ttl": 43200,
        "login_max_failures": 5,
        "login_window": 60,
    },
    "storage": {
        "state_path": "/overlay/vigil/state.json",
        "spool_dir": "/overlay/vigil/spool",
        "www_root": "/usr/share/vigil/www",
        "run_dir": "/var/run/vigil",
        "log_path": "/overlay/vigil/vigil.log",
        "log_max_bytes": 2097152,
        "log_backups": 2,
        "log_compress": True,
        "lock_path": "/var/run/vigil/vigil.lock",
    },
    "agent": {
        "name": "",
    },
}


class ConfigError(Exception):
    """配置非法或无法读取。"""


def default_config():
    """返回一份缺省配置的深拷贝，避免调用方相互污染。"""
    return copy.deepcopy(DEFAULT_CONFIG)


def _deep_merge(base, override):
    """递归合并字典；非字典值（含列表）整体替换。"""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def _parse_bool(raw, name):
    text = str(raw).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ConfigError("环境变量 %s 的布尔值无效：%s" % (name, raw))


# 环境变量覆盖表：(环境变量名, 所属节, 键名, 类型)
ENV_MAP = (
    ("VIGIL_SERVER_BASE_URL", "server", "base_url", "str"),
    ("VIGIL_SERVER_CLIENT_ID", "server", "client_id", "str"),
    ("VIGIL_SERVER_SECRET", "server", "secret", "raw"),
    ("VIGIL_SERVER_BOOTSTRAP_TOKEN", "server", "bootstrap_token", "raw"),
    ("VIGIL_SERVER_HEARTBEAT_INTERVAL", "server", "heartbeat_interval", "int"),
    ("VIGIL_SERVER_CONFIG_POLL_INTERVAL", "server", "config_poll_interval", "int"),
    ("VIGIL_SERVER_VERIFY_TLS", "server", "verify_tls", "bool"),
    ("VIGIL_ACTION_ENABLED", "action_safety", "global_enabled", "bool"),
    ("VIGIL_ACTION_MAX_LEVEL", "action_safety", "max_risk_level", "str"),
    ("VIGIL_ACTION_MAX_PER_HOUR", "action_safety", "max_actions_per_hour", "int"),
    ("VIGIL_ACTION_COOLDOWN_SECONDS", "action", "cooldown_seconds", "int"),
    ("VIGIL_ACTION_ALLOWED_SERVICES", "action", "allowed_services", "list"),
    ("VIGIL_WAN_INTERFACE", "action", "wan_interface", "str"),
    ("VIGIL_HTTPD_BIND", "httpd", "bind", "str"),
    ("VIGIL_HTTPD_PORT", "httpd", "port", "int"),
    ("VIGIL_HTTPD_TOKEN", "httpd", "token", "raw"),
    ("VIGIL_STATE_PATH", "storage", "state_path", "str"),
    ("VIGIL_SPOOL_DIR", "storage", "spool_dir", "str"),
    ("VIGIL_WWW_ROOT", "storage", "www_root", "str"),
    ("VIGIL_RUN_DIR", "storage", "run_dir", "str"),
    ("VIGIL_LOG_PATH", "storage", "log_path", "str"),
    ("VIGIL_AGENT_NAME", "agent", "name", "str"),
)


def apply_env(cfg, env):
    """把环境变量写入配置对象（原地修改）。"""
    for name, section, key, kind in ENV_MAP:
        if name not in env:
            continue
        raw = env[name]
        try:
            if kind == "int":
                value = int(str(raw).strip())
            elif kind == "float":
                value = float(str(raw).strip())
            elif kind == "bool":
                value = _parse_bool(raw, name)
            elif kind == "list":
                value = [item.strip() for item in str(raw).split(",") if item.strip()]
            elif kind == "raw":
                value = str(raw)
            else:
                value = str(raw).strip()
        except ValueError:
            raise ConfigError("环境变量 %s 类型无效：%s" % (name, raw)) from None
        section_obj = cfg.setdefault(section, {})
        if not isinstance(section_obj, dict):
            raise ConfigError("配置节 %s 必须是对象" % section)
        section_obj[key] = value
    return cfg


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _check_url(value):
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _normalize(cfg, errors):
    """就地规范化 checks / guards，便于后续校验与运行。"""
    registry = builtin_registry()
    checks = cfg.get("checks")
    if isinstance(checks, list):
        try:
            cfg["checks"] = normalize_checks(checks, registry)
        except CheckConfigError as exc:
            errors.append(str(exc))
    guards = cfg.get("guards")
    if isinstance(guards, list):
        normalized = []
        for item in guards:
            if isinstance(item, dict):
                try:
                    normalized.append(normalize_guard(item))
                except GuardConfigError as exc:
                    errors.append(str(exc))
            else:
                normalized.append(item)
        cfg["guards"] = normalized
    return registry


def validate_config(cfg):
    """校验配置，非法时抛出 ConfigError（汇总全部错误）。"""
    if not isinstance(cfg, dict):
        raise ConfigError("配置根节点必须是 JSON 对象")

    errors = []

    if cfg.get("schema_version") != 3:
        errors.append("schema_version 必须为 3")

    server = cfg.get("server")
    if not isinstance(server, dict):
        errors.append("server 必须是对象")
    else:
        base_url = server.get("base_url", "")
        if not isinstance(base_url, str):
            errors.append("server.base_url 必须是字符串")
        elif base_url and not _check_url(base_url):
            errors.append("server.base_url 必须是 http(s) URL：%s" % base_url)
        for key in ("client_id", "secret", "bootstrap_token"):
            if not isinstance(server.get(key, ""), str):
                errors.append("server.%s 必须是字符串" % key)
        hb = server.get("heartbeat_interval", 30)
        if not _is_int(hb) or not 5 <= hb <= 3600:
            errors.append("server.heartbeat_interval 必须是 5-3600 的整数")
        poll = server.get("config_poll_interval", 300)
        if not _is_int(poll) or not 10 <= poll <= 86400:
            errors.append("server.config_poll_interval 必须是 10-86400 的整数")
        if not isinstance(server.get("verify_tls", True), bool):
            errors.append("server.verify_tls 必须是布尔值")

    registry = _normalize(cfg, errors)
    validate_checks(cfg.get("checks"), registry, errors)
    validate_guards(cfg, errors)

    action_safety = cfg.get("action_safety")
    if not isinstance(action_safety, dict):
        errors.append("action_safety 必须是对象")

    action = cfg.get("action")
    if not isinstance(action, dict):
        errors.append("action 必须是对象")
    else:
        cooldown = action.get("cooldown_seconds", 300)
        if not _is_int(cooldown) or not 0 <= cooldown <= 86400:
            errors.append("action.cooldown_seconds 必须是 0-86400 的整数")
        services = action.get("allowed_services", [])
        if not isinstance(services, list) or not all(
            isinstance(item, str) and item.strip() for item in services
        ):
            errors.append("action.allowed_services 必须是非空字符串数组")
        timeout = action.get("timeout", 30)
        if not _is_int(timeout) or not 1 <= timeout <= 300:
            errors.append("action.timeout 必须是 1-300 的整数")
        for key in ("lock_path", "wan_interface"):
            if not isinstance(action.get(key), str) or not action.get(key).strip():
                errors.append("action.%s 必须是非空字符串" % key)

    httpd = cfg.get("httpd")
    if not isinstance(httpd, dict):
        errors.append("httpd 必须是对象")
    else:
        if not isinstance(httpd.get("bind"), str) or not httpd.get("bind").strip():
            errors.append("httpd.bind 必须是非空字符串")
        port = httpd.get("port")
        if not _is_int(port) or not 1 <= port <= 65535:
            errors.append("httpd.port 必须是 1-65535 的整数")
        if not isinstance(httpd.get("token", ""), str):
            errors.append("httpd.token 必须是字符串")
        ttl = httpd.get("session_ttl", 43200)
        if not _is_int(ttl) or not 60 <= ttl <= 604800:
            errors.append("httpd.session_ttl 必须是 60-604800 的整数")
        failures = httpd.get("login_max_failures", 5)
        if not _is_int(failures) or not 1 <= failures <= 100:
            errors.append("httpd.login_max_failures 必须是 1-100 的整数")
        window = httpd.get("login_window", 60)
        if not _is_int(window) or not 1 <= window <= 3600:
            errors.append("httpd.login_window 必须是 1-3600 的整数")

    storage = cfg.get("storage")
    if not isinstance(storage, dict):
        errors.append("storage 必须是对象")
    else:
        for key in ("state_path", "spool_dir", "www_root", "run_dir", "log_path", "lock_path"):
            value = storage.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append("storage.%s 必须是非空字符串" % key)

    agent = cfg.get("agent")
    if not isinstance(agent, dict):
        errors.append("agent 必须是对象")
    elif not isinstance(agent.get("name", ""), str):
        errors.append("agent.name 必须是字符串")

    if errors:
        raise ConfigError("；".join(errors))
    return cfg


def load_config(path=None, env=None):
    """加载配置：缺省值 + 文件 + 环境变量，最后做校验。

    path 为空时依次取参数、环境变量 VIGIL_CONFIG、默认路径。
    文件不存在时使用缺省值；文件存在但 JSON 非法时抛 ConfigError。
    schema_version 不是 3 时整份配置不生效，不做任何迁移。
    """
    if env is None:
        env = os.environ
    if path is None:
        path = env.get("VIGIL_CONFIG") or DEFAULT_CONFIG_PATH

    cfg = default_config()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except OSError as exc:
            raise ConfigError("读取配置文件失败：%s" % exc) from exc
        except ValueError as exc:
            raise ConfigError("配置文件不是合法 JSON：%s" % exc) from exc
        if not isinstance(data, dict):
            raise ConfigError("配置文件根节点必须是 JSON 对象")
        _deep_merge(cfg, data)

    apply_env(cfg, env)
    validate_config(cfg)
    return cfg


def save_config(cfg, path=None):
    """原子写入配置文件，权限尽量收紧到 0600。"""
    path = path or DEFAULT_CONFIG_PATH
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".vigil-config-", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def get_in(cfg, dotted, default=None):
    """按 a.b.c 取值，任一层缺失时返回 default。"""
    current = cfg
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current
