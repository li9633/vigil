"""script：本地脚本探测器。

脚本存放在 `/etc/vigil/probes.d/`（权限 0700、属主 root），通过同目录的
`manifest.json` 登记白名单与 SHA-256，加载时逐次校验，服务端无法下发代码。

清单格式：

```json
{
  "openclash": {
    "file": "openclash.sh",
    "sha256": "<64 位十六进制>",
    "timeout": 10
  }
}
```

执行约定：
- 参数通过环境变量传入：`VIGIL_PROBE`、`VIGIL_PARAMS`（JSON）、`VIGIL_PARAM_<KEY>`
- stdout 必须为单行 JSON（可选 `status` / `evidence` / `message`）
- 退出码：0=OK，1=WARN，2=CRITICAL，3=UNKNOWN
- stderr 截断后写入证据，不参与状态判定
"""

import hashlib
import json
import os
import re
import stat
import tempfile

from .base import (
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_UNKNOWN,
    STATUS_WARN,
    make_result,
    truncate,
)

LAYER = "script"
DEPENDS_ON = ()
DEFAULT_DIR = "/etc/vigil/probes.d"
MANIFEST_NAME = "manifest.json"
DEFAULT_TIMEOUT = 10.0
MAX_STDOUT = 2048
MAX_SCRIPT_BYTES = 65536
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

EXIT_STATUS = {
    0: STATUS_OK,
    1: STATUS_WARN,
    2: STATUS_CRITICAL,
    3: STATUS_UNKNOWN,
}


def probes_dir():
    """脚本目录，可用 VIGIL_PROBES_DIR 覆盖（便于测试与自定义安装）。"""
    return os.environ.get("VIGIL_PROBES_DIR") or DEFAULT_DIR


def _check_directory(directory):
    """校验目录权限与属主，返回错误描述；合法时返回空串。"""
    if not os.path.isdir(directory):
        return "脚本探测器目录不存在：%s" % directory
    if os.name == "nt":
        return ""
    try:
        info = os.stat(directory)
    except OSError as exc:
        return "无法读取脚本探测器目录：%s" % exc
    if stat.S_IMODE(info.st_mode) & 0o077:
        return "脚本探测器目录权限必须为 0700"
    if os.geteuid() == 0 and info.st_uid != 0:
        return "脚本探测器目录必须属于 root"
    return ""


def _read_manifest(directory):
    path = os.path.join(directory, MANIFEST_NAME)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None, "未找到脚本探测器清单：%s" % path
    except OSError as exc:
        return None, "读取脚本探测器清单失败：%s" % exc
    except ValueError as exc:
        return None, "脚本探测器清单不是合法 JSON：%s" % exc
    if not isinstance(data, dict):
        return None, "脚本探测器清单根节点必须是对象"
    return data, ""


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry(manifest, name):
    entry = manifest.get(name)
    if not isinstance(entry, dict):
        return None, "未注册的脚本探测器：%s" % name
    filename = str(entry.get("file") or "").strip()
    if not filename:
        return None, "脚本探测器 %s 缺少 file" % name
    if os.path.basename(filename) != filename:
        return None, "脚本探测器 %s 的 file 不允许包含路径" % name
    expected = str(entry.get("sha256") or "").strip().lower()
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        return None, "脚本探测器 %s 缺少合法的 sha256" % name
    return entry, ""


def _env_key(key):
    cleaned = "".join(char if char.isalnum() else "_" for char in str(key))
    return cleaned.upper()


def _env_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _timeout(ctx, entry):
    raw = ctx.params.get("timeout")
    if raw is None:
        raw = entry.get("timeout") or DEFAULT_TIMEOUT
    try:
        return float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT


def detect(ctx):
    name = str(ctx.param("name") or "").strip()
    if not name:
        return make_result(LAYER, STATUS_UNKNOWN, 0, {"reason": "缺少 name 参数"})

    directory = probes_dir()
    problem = _check_directory(directory)
    if problem:
        return make_result(LAYER, STATUS_UNKNOWN, 0, {"directory": directory, "reason": problem})

    manifest, error = _read_manifest(directory)
    if manifest is None:
        return make_result(LAYER, STATUS_UNKNOWN, 0, {"directory": directory, "reason": error})

    entry, error = _entry(manifest, name)
    if entry is None:
        return make_result(LAYER, STATUS_UNKNOWN, 0, {"script": name, "reason": error})

    filename = str(entry.get("file")).strip()
    path = os.path.join(directory, filename)
    if not os.path.isfile(path):
        return make_result(
            LAYER,
            STATUS_UNKNOWN,
            0,
            {"script": name, "file": filename, "reason": "脚本文件不存在"},
        )
    if os.name != "nt" and not os.access(path, os.X_OK):
        return make_result(
            LAYER,
            STATUS_UNKNOWN,
            0,
            {"script": name, "file": filename, "reason": "脚本不可执行"},
        )

    expected = str(entry.get("sha256")).strip().lower()
    try:
        actual = _sha256(path)
    except OSError as exc:
        return make_result(
            LAYER,
            STATUS_UNKNOWN,
            0,
            {"script": name, "file": filename, "reason": "读取脚本失败：%s" % exc},
        )
    if actual != expected:
        return make_result(
            LAYER,
            STATUS_UNKNOWN,
            0,
            {
                "script": name,
                "file": filename,
                "reason": "脚本哈希校验失败",
                "expected": expected,
                "actual": actual,
            },
        )

    timeout = _timeout(ctx, entry)
    env = {
        "VIGIL_PROBE": name,
        "VIGIL_PARAMS": json.dumps(ctx.params, ensure_ascii=False, sort_keys=True),
    }
    for key, value in ctx.params.items():
        env["VIGIL_PARAM_%s" % _env_key(key)] = _env_value(value)

    result = ctx.runner.run([path], timeout=timeout, env=env)
    latency = result.elapsed_ms
    evidence = {"script": name, "file": filename, "exit_code": result.exit_code}
    if result.stderr.strip():
        evidence["stderr"] = truncate(result.stderr, 4096)

    if result.timed_out:
        return make_result(
            LAYER, STATUS_CRITICAL, latency, {**evidence, "reason": "脚本执行超时"}
        )
    if result.error:
        return make_result(
            LAYER,
            STATUS_UNKNOWN,
            latency,
            {**evidence, "reason": truncate(result.error, 200)},
        )

    status = EXIT_STATUS.get(result.exit_code, STATUS_UNKNOWN)
    stdout = result.stdout.strip()
    if stdout:
        if "\n" in stdout:
            return make_result(
                LAYER,
                STATUS_UNKNOWN,
                latency,
                {
                    **evidence,
                    "reason": "stdout 必须为单行 JSON",
                    "stdout": truncate(stdout, MAX_STDOUT),
                },
            )
        try:
            payload = json.loads(stdout)
        except ValueError:
            return make_result(
                LAYER,
                STATUS_UNKNOWN,
                latency,
                {
                    **evidence,
                    "reason": "stdout 不是合法 JSON",
                    "stdout": truncate(stdout, MAX_STDOUT),
                },
            )
        if not isinstance(payload, dict):
            return make_result(
                LAYER,
                STATUS_UNKNOWN,
                latency,
                {
                    **evidence,
                    "reason": "stdout JSON 必须是对象",
                    "stdout": truncate(stdout, MAX_STDOUT),
                },
            )
        payload_evidence = payload.get("evidence")
        if isinstance(payload_evidence, dict):
            evidence.update(payload_evidence)
        if payload.get("message"):
            evidence["message"] = truncate(str(payload["message"]), 512)
        reported = payload.get("status")
        if isinstance(reported, str) and reported:
            evidence["reported_status"] = reported

    if status != STATUS_OK:
        evidence.setdefault("reason", "脚本退出码 %s" % result.exit_code)
    return make_result(LAYER, status, latency, evidence)


def _list_scripts(directory, registered):
    """列出目录下未登记的 .sh 文件。"""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return sorted(
        name
        for name in names
        if name.endswith(".sh")
        and name not in registered
        and os.path.isfile(os.path.join(directory, name))
    )


def inspect_scripts(directory=None):
    """返回脚本探测器清单与校验结果（不执行脚本）。"""
    directory = directory or probes_dir()
    result = {
        "directory": directory,
        "manifest": os.path.join(directory, MANIFEST_NAME),
        "directory_ok": False,
        "manifest_exists": False,
        "error": "",
        "scripts": [],
        "unregistered": [],
    }
    problem = _check_directory(directory)
    if problem:
        result["error"] = problem
        return result
    result["directory_ok"] = True

    manifest, error = _read_manifest(directory)
    if manifest is None:
        # 首次使用尚未创建清单时按空清单处理，导入后自动生成。
        if not error.startswith("未找到"):
            result["error"] = error
        result["unregistered"] = _list_scripts(directory, set())
        return result
    result["manifest_exists"] = True

    registered = set()
    for name in sorted(manifest):
        entry = manifest.get(name)
        item = {
            "name": name,
            "file": "",
            "sha256": "",
            "actual_sha256": "",
            "timeout": int(DEFAULT_TIMEOUT),
            "valid": False,
            "error": "",
        }
        if not isinstance(entry, dict):
            item["error"] = "清单项必须是对象"
            result["scripts"].append(item)
            continue

        filename = str(entry.get("file") or "").strip()
        item["file"] = filename
        item["sha256"] = str(entry.get("sha256") or "").strip().lower()
        try:
            item["timeout"] = int(entry.get("timeout") or DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            item["timeout"] = int(DEFAULT_TIMEOUT)

        if not filename or os.path.basename(filename) != filename:
            item["error"] = "file 非法"
        else:
            registered.add(filename)
            path = os.path.join(directory, filename)
            if not os.path.isfile(path):
                item["error"] = "脚本文件不存在"
            elif os.name != "nt" and not os.access(path, os.X_OK):
                item["error"] = "脚本不可执行"
            elif len(item["sha256"]) != 64:
                item["error"] = "缺少合法的 sha256"
            else:
                try:
                    actual = _sha256(path)
                except OSError as exc:
                    item["error"] = "读取脚本失败：%s" % exc
                else:
                    item["actual_sha256"] = actual
                    item["valid"] = actual == item["sha256"]
                    if not item["valid"]:
                        item["error"] = "脚本哈希校验失败"
        result["scripts"].append(item)

    result["unregistered"] = _list_scripts(directory, registered)
    return result


def _atomic_write(path, content, mode):
    directory = os.path.dirname(path)
    handle_fd, temp_path = tempfile.mkstemp(prefix=".vigil-", dir=directory)
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def save_script(name, content, timeout=DEFAULT_TIMEOUT, directory=None):
    """导入/覆盖一个脚本探测器，返回该脚本的校验信息。"""
    directory = directory or probes_dir()
    name = str(name or "").strip()
    if not NAME_PATTERN.match(name):
        raise ValueError("name 只能包含字母、数字、下划线和短横线，长度 1-32")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("脚本内容不能为空")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_SCRIPT_BYTES:
        raise ValueError("脚本内容不能超过 64 KB")
    if "\x00" in content:
        raise ValueError("脚本内容不能包含 NUL")

    if not os.path.isdir(directory):
        os.makedirs(directory, mode=0o700, exist_ok=True)
    problem = _check_directory(directory)
    if problem:
        raise ValueError(problem)

    manifest, error = _read_manifest(directory)
    if manifest is None:
        if not error.startswith("未找到"):
            raise ValueError(error)
        manifest = {}

    filename = "%s.sh" % name
    path = os.path.join(directory, filename)
    _atomic_write(path, content, 0o700)
    digest = _sha256(path)

    try:
        seconds = int(timeout)
    except (TypeError, ValueError):
        seconds = int(DEFAULT_TIMEOUT)
    seconds = max(1, min(60, seconds))
    manifest[name] = {"file": filename, "sha256": digest, "timeout": seconds}
    _atomic_write(
        os.path.join(directory, MANIFEST_NAME),
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        0o600,
    )
    return next(
        item for item in inspect_scripts(directory)["scripts"] if item["name"] == name
    )


def delete_script(name, directory=None):
    """从清单与磁盘删除一个脚本探测器。"""
    directory = directory or probes_dir()
    name = str(name or "").strip()
    if not NAME_PATTERN.match(name):
        raise ValueError("name 非法")
    manifest, error = _read_manifest(directory)
    if manifest is None:
        raise ValueError(error)
    entry = manifest.get(name)
    if not isinstance(entry, dict):
        raise ValueError("未注册的脚本探测器：%s" % name)

    filename = str(entry.get("file") or "").strip()
    del manifest[name]
    _atomic_write(
        os.path.join(directory, MANIFEST_NAME),
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        0o600,
    )
    if filename and os.path.basename(filename) == filename:
        path = os.path.join(directory, filename)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ValueError("删除脚本文件失败：%s" % exc) from exc
    return True

