"""配置加载、环境变量覆盖与校验。

配置文件默认位于 /etc/vigil/config.json。缺省值按设计文档第 3.6 节与第 13 章，
另补充 httpd / storage 等运行所需配置。所有键都支持通过环境变量覆盖，便于
安装脚本与排障时临时调整。
"""

import copy
import json
import os
import tempfile
from urllib.parse import urlsplit

# 默认配置文件路径
DEFAULT_CONFIG_PATH = "/etc/vigil/config.json"

# 缺省配置：与设计文档保持一致，额外补充运行所需字段
DEFAULT_CONFIG = {
    "schema_version": 1,
    "server": {
        "base_url": "",
        "client_id": "",
        "secret": "",
        "bootstrap_token": "",
        "heartbeat_interval": 30,
        "config_poll_interval": 300,
        "verify_tls": True,
    },
    "detect": {
        "wan_device": "auto",
        "wan_interface": "wan",
        "dns_probe": "www.baidu.com",
        "http_probes": ["http://connect.rom.miui.com/generate_204"],
        "timeout": 3,
        "interval": 30,
    },
    "action": {
        "enabled": True,
        "max_level": "L1",
        "failure_threshold": 2,
        "cooldown_seconds": 300,
        "max_per_hour": 3,
        "allowed_services": ["dnsmasq"],
        "timeout": 30,
        "lock_path": "/var/run/vigil/action.lock",
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
    ("VIGIL_WAN_DEVICE", "detect", "wan_device", "str"),
    ("VIGIL_WAN_INTERFACE", "detect", "wan_interface", "str"),
    ("VIGIL_DNS_PROBE", "detect", "dns_probe", "str"),
    ("VIGIL_HTTP_PROBES", "detect", "http_probes", "list"),
    ("VIGIL_DETECT_TIMEOUT", "detect", "timeout", "float"),
    ("VIGIL_DETECT_INTERVAL", "detect", "interval", "int"),
    ("VIGIL_ACTION_ENABLED", "action", "enabled", "bool"),
    ("VIGIL_ACTION_MAX_LEVEL", "action", "max_level", "str"),
    ("VIGIL_ACTION_FAILURE_THRESHOLD", "action", "failure_threshold", "int"),
    ("VIGIL_ACTION_COOLDOWN_SECONDS", "action", "cooldown_seconds", "int"),
    ("VIGIL_ACTION_MAX_PER_HOUR", "action", "max_per_hour", "int"),
    ("VIGIL_ACTION_ALLOWED_SERVICES", "action", "allowed_services", "list"),
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
            raise ConfigError("环境变量 %s 类型无效：%s" % (name, raw))
        section_obj = cfg.setdefault(section, {})
        if not isinstance(section_obj, dict):
            raise ConfigError("配置节 %s 必须是对象" % section)
        section_obj[key] = value
    return cfg


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_url(value):
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def validate_config(cfg):
    """校验配置，非法时抛出 ConfigError（汇总全部错误）。"""
    if not isinstance(cfg, dict):
        raise ConfigError("配置根节点必须是 JSON 对象")

    errors = []

    if cfg.get("schema_version") != 1:
        errors.append("schema_version 必须为 1")

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

    detect = cfg.get("detect")
    if not isinstance(detect, dict):
        errors.append("detect 必须是对象")
    else:
        for key in ("wan_device", "wan_interface", "dns_probe"):
            value = detect.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append("detect.%s 必须是非空字符串" % key)
        probes = detect.get("http_probes")
        if not isinstance(probes, list):
            errors.append("detect.http_probes 必须是数组")
        else:
            for probe in probes:
                if not isinstance(probe, str) or not _check_url(probe):
                    errors.append("detect.http_probes 含非法 URL：%r" % (probe,))
        timeout = detect.get("timeout", 3)
        if not _is_number(timeout) or not 0 < timeout <= 60:
            errors.append("detect.timeout 必须是 (0, 60] 的数值")
        interval = detect.get("interval", 30)
        if not _is_int(interval) or not 5 <= interval <= 3600:
            errors.append("detect.interval 必须是 5-3600 的整数")

    action = cfg.get("action")
    if not isinstance(action, dict):
        errors.append("action 必须是对象")
    else:
        if not isinstance(action.get("enabled", True), bool):
            errors.append("action.enabled 必须是布尔值")
        if action.get("max_level") not in ("L0", "L1"):
            errors.append("action.max_level 只能是 L0 或 L1")
        threshold = action.get("failure_threshold", 3)
        if not _is_int(threshold) or not 1 <= threshold <= 10:
            errors.append("action.failure_threshold 必须是 1-10 的整数")
        cooldown = action.get("cooldown_seconds", 300)
        if not _is_int(cooldown) or not 0 <= cooldown <= 86400:
            errors.append("action.cooldown_seconds 必须是 0-86400 的整数")
        max_per_hour = action.get("max_per_hour", 3)
        if not _is_int(max_per_hour) or not 1 <= max_per_hour <= 100:
            errors.append("action.max_per_hour 必须是 1-100 的整数")
        services = action.get("allowed_services", [])
        if not isinstance(services, list) or not all(
            isinstance(item, str) and item.strip() for item in services
        ):
            errors.append("action.allowed_services 必须是非空字符串数组")
        timeout = action.get("timeout", 30)
        if not _is_int(timeout) or not 1 <= timeout <= 300:
            errors.append("action.timeout 必须是 1-300 的整数")

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
    """
    if env is None:
        env = os.environ
    if path is None:
        path = env.get("VIGIL_CONFIG") or DEFAULT_CONFIG_PATH

    cfg = default_config()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except OSError as exc:
            raise ConfigError("读取配置文件失败：%s" % exc)
        except ValueError as exc:
            raise ConfigError("配置文件不是合法 JSON：%s" % exc)
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
