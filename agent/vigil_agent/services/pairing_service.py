"""客户端配对状态机：发起、轮询、取消与本地状态持久化。

配对码只在本地展示与提交，claim_token 只保存在本机（0600），
服务端通过管理员人工确认后放行，不再要求用户抄写 bootstrap_token。
"""

from __future__ import annotations

import json
import os
import secrets
import time
import uuid
from urllib.parse import urlsplit

from ..config import DEFAULT_CONFIG_PATH
from ..logstore import log_event, set_log_context
from ..runtime import read_first_line, read_fw_version

STATE_FILENAME = "pairing.json"
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 8

ACTIVE_STATUSES = ("pending", "approved")


def state_path(app):
    directory = os.path.dirname(app.config_path or DEFAULT_CONFIG_PATH) or "."
    return os.path.join(directory, STATE_FILENAME)


def _instance_path(app):
    directory = os.path.dirname(app.config_path or DEFAULT_CONFIG_PATH) or "."
    return os.path.join(directory, "instance_id")


def _write_private(path, text):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp_path = "%s.tmp-%s" % (path, secrets.token_hex(4))
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(text)
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_state(app):
    try:
        with open(state_path(app), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("request_id"):
        return None
    return data


def save_state(app, state):
    _write_private(state_path(app), json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def clear_state(app):
    try:
        os.unlink(state_path(app))
    except OSError:
        pass


def instance_id(app):
    """安装级稳定实例 ID，用于服务端去重；与 boot_id 无关。"""
    path = _instance_path(app)
    try:
        with open(path, encoding="utf-8") as handle:
            value = handle.read().strip()
        if value:
            return value
    except OSError:
        pass
    value = "vigil-" + uuid.uuid4().hex
    try:
        _write_private(path, value + "\n")
    except OSError:
        pass
    return value


def generate_code():
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return "%s-%s" % (raw[:4], raw[4:])


def public_state(app):
    """返回给本地 Web UI 的配对状态（绝不包含 claim_token）。"""
    if app.transport.is_registered:
        return {
            "status": "paired",
            "client_id": app.transport.client_id,
            "code": "",
            "code_expires_at": 0,
            "expires_at": 0,
            "last_error": "",
            "server_url": app.transport.base_url,
        }

    state = load_state(app)
    if not state:
        return {
            "status": "idle",
            "code": "",
            "code_expires_at": 0,
            "expires_at": 0,
            "last_error": "",
            "server_url": app.transport.base_url,
        }

    now = time.time()
    status = str(state.get("status") or "pending")
    if status == "pending":
        if float(state.get("expires_at") or 0) <= now:
            status = "expired"
        elif float(state.get("code_expires_at") or 0) <= now:
            status = "code_expired"
    return {
        "status": status,
        "code": str(state.get("code") or ""),
        "code_expires_at": int(state.get("code_expires_at") or 0),
        "expires_at": int(state.get("expires_at") or 0),
        "last_error": str(state.get("last_error") or ""),
        "server_url": str(state.get("server_url") or app.transport.base_url),
    }


def _validate_base_url(base_url):
    text = str(base_url or "").strip().rstrip("/")
    parsed = urlsplit(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("服务器地址必须是 http(s) URL")
    if any(char.isspace() for char in text):
        raise ValueError("服务器地址不能包含空白字符")
    return text


def start(app, base_url):
    """POST /api/pairing：保存服务端地址并发起配对，返回展示用状态。"""
    if app.transport.is_registered:
        raise ValueError("客户端已注册，无需配对")
    text = _validate_base_url(base_url)

    app.transport.update_server(text)
    app.config.setdefault("server", {})["base_url"] = text
    app._etag = None
    app._server_config = {}
    app._persist_config(app.config)

    code = generate_code()
    result = app.transport.create_pairing(
        instance_id(app),
        app.agent_name(),
        code,
        model=read_first_line("/tmp/sysinfo/model"),
        fw_version=read_fw_version(),
    )
    if not result.get("ok"):
        raise ValueError(str(result.get("error") or "无法连接服务端"))

    body = result.get("body") or {}
    request_id = body.get("request_id")
    claim_token = body.get("claim_token")
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("服务端响应缺少 request_id")
    if not isinstance(claim_token, str) or not claim_token:
        raise ValueError("服务端响应缺少 claim_token")

    state = {
        "request_id": request_id,
        "claim_token": claim_token,
        "code": code,
        "status": "pending",
        "code_expires_at": int(body.get("code_expires_at") or 0),
        "expires_at": int(body.get("expires_at") or 0),
        "server_url": text,
        "last_poll": 0,
        "last_error": "",
        "updated_at": int(time.time()),
    }
    save_state(app, state)
    app._next_register_at = 0.0
    log_event(
        "info",
        "pairing.start",
        "已发起配对，请在服务端确认配对码",
        request_id=request_id,
    )
    return public_state(app)


def poll(app, force=False):
    """轮询配对状态；批准后领取凭证并转为已注册。"""
    if app.transport.is_registered:
        clear_state(app)
        return public_state(app)

    state = load_state(app)
    if not state:
        return public_state(app)
    status = str(state.get("status") or "pending")
    if status not in ACTIVE_STATUSES:
        return public_state(app)

    now = time.time()
    if not force and now - float(state.get("last_poll") or 0) < 3:
        return public_state(app)
    state["last_poll"] = now

    result = app.transport.pairing_status(state["request_id"], state["claim_token"])
    if not result.get("ok"):
        if result.get("status") == 401:
            state["status"] = "expired"
            state["claim_token"] = ""
            state["last_error"] = "配对请求已失效，请重新注册"
        else:
            state["last_error"] = str(result.get("error") or "轮询配对状态失败")
        state["updated_at"] = int(now)
        save_state(app, state)
        return public_state(app)

    body = result.get("body") or {}
    remote = str(body.get("status") or "")
    if remote == "approved" and body.get("client_id") and body.get("secret"):
        app.transport.update_credentials(str(body["client_id"]), str(body["secret"]))
        heartbeat = body.get("heartbeat_interval")
        if isinstance(heartbeat, int) and not isinstance(heartbeat, bool):
            if 5 <= heartbeat <= 3600:
                app.config.setdefault("server", {})["heartbeat_interval"] = heartbeat
                app.scheduler.update_interval("heartbeat", heartbeat)
        app._persist_config(app.config)
        app._next_register_at = 0.0
        clear_state(app)
        app._record_event(
            "agent_registered", "agent", {"client_id": app.transport.client_id}
        )
        set_log_context(app.transport.client_id, app.transport.boot_id)
        log_event(
            "info",
            "client.register",
            "配对完成，客户端已注册",
            client_id=app.transport.client_id,
        )
        return public_state(app)

    if remote in ("denied", "expired"):
        state["status"] = remote
        state["claim_token"] = ""
        state["last_error"] = str(
            body.get("reason") or ("配对请求已被拒绝" if remote == "denied" else "配对请求已过期")
        )
        state["updated_at"] = int(now)
        save_state(app, state)
        log_event(
            "warn" if remote == "denied" else "info",
            "pairing.%s" % remote,
            "配对未通过",
            reason=state["last_error"],
        )
        return public_state(app)

    state["status"] = "pending"
    state["last_error"] = ""
    for key in ("expires_at", "code_expires_at"):
        value = body.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            state[key] = value
    state["updated_at"] = int(now)
    save_state(app, state)
    return public_state(app)


def cancel(app):
    """DELETE /api/pairing：放弃本地待审批状态。"""
    if not app.transport.is_registered:
        clear_state(app)
        app._next_register_at = 0.0
    log_event("info", "pairing.cancel", "已取消本地配对")
    return public_state(app)
