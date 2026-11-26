"""本地 probe 包管理：manifest 校验、目录扫描、tar.gz 安装与删除。

包格式见设计文档 3.6：
- 目录 `/etc/vigil/probes.d/<id>/`，目录名必须等于 manifest.id。
- id 以 `local.` 开头，remote_allowed 固定为 false。
- 包内文件总大小 ≤ 2 MB，单文件 ≤ 512 KB；拒绝符号链接与路径穿越。
- manifest.sha256 必须与入口文件一致，每次执行前由 runner 再次校验。
"""

import io
import json
import os
import re
import shutil
import tarfile
import tempfile

from ..checks.expect import validate_expect
from ..utils.fs import ensure_dir
from .builtin import builtin_registry
from .runner import LocalProbe, file_sha256

DEFAULT_PROBES_DIR = "/etc/vigil/probes.d"
LOCAL_PROBE_ID_RE = re.compile(r"^local\.[a-z0-9][a-z0-9._-]{0,48}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
INTERPRETERS = ("", "python3", "sh", "ash")
SCHEMA_TYPES = ("string", "number", "integer", "boolean", "enum")

MAX_PACKAGE_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024
MAX_FILES = 64
MAX_TIMEOUT = 30.0
DEFAULT_TIMEOUT = 5.0
DEFAULT_EXPECT = {"kind": "json", "exists": True}
MAX_ARCHIVE_BYTES = MAX_PACKAGE_BYTES * 4


class ProbePackageError(ValueError):
    """probe 包或 manifest 非法。"""


class ProbePackage:
    """一个本地 probe 包的解析结果；`error` 非空表示不可用。"""

    def __init__(
        self,
        probe_id="",
        name="",
        version="",
        description="",
        directory="",
        entry="",
        interpreter="",
        timeout=DEFAULT_TIMEOUT,
        sha256="",
        params_schema=None,
        default_expect=None,
        error="",
    ):
        self.id = probe_id
        self.name = name or probe_id
        self.version = version
        self.description = description
        self.directory = directory
        self.entry = entry
        self.interpreter = interpreter
        self.timeout = float(timeout or DEFAULT_TIMEOUT)
        self.sha256 = sha256
        self.params_schema = params_schema if isinstance(params_schema, dict) else {}
        self.default_expect = (
            default_expect if isinstance(default_expect, dict) else dict(DEFAULT_EXPECT)
        )
        self.error = str(error or "")

    @property
    def valid(self):
        return not self.error

    def probe(self):
        return LocalProbe(self)


def _merge_errors(errors):
    return ProbePackageError("；".join(errors))


def _validate_schema(schema, errors):
    if not isinstance(schema, dict):
        errors.append("manifest.params_schema 必须是对象")
        return
    for name, spec in schema.items():
        if not isinstance(name, str) or not name:
            errors.append("manifest.params_schema 的字段名必须是非空字符串")
            continue
        if not isinstance(spec, dict):
            errors.append("manifest.params_schema.%s 必须是对象" % name)
            continue
        kind = spec.get("type", "string")
        if kind not in SCHEMA_TYPES:
            errors.append(
                "manifest.params_schema.%s.type 必须是 %s" % (name, "/".join(SCHEMA_TYPES))
            )
        if kind == "enum" and not isinstance(spec.get("values"), list):
            errors.append("manifest.params_schema.%s 的 enum 必须提供 values 数组" % name)
        if "required" in spec and not isinstance(spec["required"], bool):
            errors.append("manifest.params_schema.%s.required 必须是布尔值" % name)


def _relative_entry(entry):
    text = str(entry or "").replace("\\", "/").strip()
    if not text:
        raise ProbePackageError("manifest.entry 不能为空")
    if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        raise ProbePackageError("manifest.entry 不允许绝对路径")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ProbePackageError("manifest.entry 不允许包含 ..")
    return "/".join(parts)


def _package_size(directory, errors):
    total = 0
    for root, _dirs, files in os.walk(directory, followlinks=False):
        for name in files:
            path = os.path.join(root, name)
            if os.path.islink(path):
                errors.append("probe 包不允许符号链接：%s" % name)
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                errors.append("无法读取文件：%s" % name)
                continue
            if size > MAX_FILE_BYTES:
                errors.append("单文件超过 512 KB：%s" % name)
            total += size
    if total > MAX_PACKAGE_BYTES:
        errors.append("probe 包总大小超过 2 MB")


def read_manifest(directory, dir_name=None):
    """读取并校验一个 probe 包目录，非法时抛 ProbePackageError。"""
    manifest_path = os.path.join(directory, "manifest.json")
    if not os.path.isfile(manifest_path) or os.path.islink(manifest_path):
        raise ProbePackageError("缺少 manifest.json")
    try:
        if os.path.getsize(manifest_path) > MAX_FILE_BYTES:
            raise ProbePackageError("manifest.json 超过 512 KB")
        with open(manifest_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise ProbePackageError("读取 manifest.json 失败：%s" % exc) from exc
    except ValueError as exc:
        raise ProbePackageError("manifest.json 不是合法 JSON：%s" % exc) from exc
    if not isinstance(data, dict):
        raise ProbePackageError("manifest.json 根节点必须是对象")

    errors = []
    if data.get("schema_version") != 1:
        errors.append("manifest.schema_version 必须为 1")

    probe_id = data.get("id")
    if not isinstance(probe_id, str) or not LOCAL_PROBE_ID_RE.match(probe_id):
        errors.append("manifest.id 必须匹配 local.<name>")
    elif dir_name and probe_id != dir_name:
        errors.append("目录名必须等于 manifest.id：%s" % probe_id)

    name = data.get("name", probe_id)
    if not isinstance(name, str):
        errors.append("manifest.name 必须是字符串")
        name = str(probe_id or "")
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        errors.append("manifest.version 必须是非空字符串")
        version = ""
    description = data.get("description", "")
    if not isinstance(description, str):
        errors.append("manifest.description 必须是字符串")
        description = ""

    interpreter = data.get("interpreter", "")
    if not isinstance(interpreter, str) or interpreter not in INTERPRETERS:
        errors.append("manifest.interpreter 必须是 python3/sh/ash 或省略")
        interpreter = ""

    timeout = data.get("timeout", DEFAULT_TIMEOUT)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < float(timeout) <= MAX_TIMEOUT
    ):
        errors.append("manifest.timeout 必须是 (0, 30] 的数值")
        timeout = DEFAULT_TIMEOUT

    entry = ""
    try:
        entry = _relative_entry(data.get("entry"))
    except ProbePackageError as exc:
        errors.append(str(exc))

    entry_path = os.path.join(directory, entry) if entry else ""
    if entry_path:
        if not os.path.isfile(entry_path) or os.path.islink(entry_path):
            errors.append("manifest.entry 指向的文件不存在")
        else:
            try:
                root_real = os.path.realpath(directory)
                entry_real = os.path.realpath(entry_path)
                if os.path.commonpath([root_real, entry_real]) != root_real:
                    errors.append("manifest.entry 越出 probe 包目录")
            except ValueError:
                errors.append("manifest.entry 路径非法")

    sha256 = data.get("sha256")
    if not isinstance(sha256, str) or not SHA256_RE.match(sha256.lower()):
        errors.append("manifest.sha256 必须是 64 位十六进制字符串")
        sha256 = ""
    elif entry_path and os.path.isfile(entry_path):
        try:
            actual = file_sha256(entry_path)
        except OSError as exc:
            errors.append("读取入口文件失败：%s" % exc)
        else:
            if actual != sha256.lower():
                errors.append("manifest.sha256 与入口文件不一致")

    params_schema = data.get("params_schema", {})
    _validate_schema(params_schema, errors)

    default_expect = data.get("default_expect")
    if default_expect is None:
        default_expect = dict(DEFAULT_EXPECT)
    validate_expect(default_expect, errors, "manifest.default_expect")

    _package_size(directory, errors)

    if errors:
        raise _merge_errors(errors)

    return ProbePackage(
        probe_id=probe_id,
        name=name,
        version=version,
        description=description,
        directory=directory,
        entry=entry,
        interpreter=interpreter,
        timeout=timeout,
        sha256=sha256.lower(),
        params_schema=dict(params_schema),
        default_expect=dict(default_expect),
    )


def _member_parts(name):
    text = str(name or "").replace("\\", "/")
    if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        raise ProbePackageError("probe 包包含绝对路径：%s" % name)
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ProbePackageError("probe 包包含非法路径：%s" % name)
    return parts


def extract_archive(data, destination):
    """解包 tar.gz 到 destination，逐项校验路径与大小。"""
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except tarfile.TarError as exc:
        raise ProbePackageError("上传内容不是合法的 tar.gz：%s" % exc) from exc

    total = 0
    count = 0
    root_real = os.path.realpath(destination)
    try:
        for member in archive.getmembers():
            count += 1
            if count > MAX_FILES:
                raise ProbePackageError("probe 包文件数超过 %d" % MAX_FILES)
            if member.issym() or member.islnk() or member.isdev():
                raise ProbePackageError("probe 包不允许符号链接或设备文件")
            parts = _member_parts(member.name)
            target = os.path.realpath(os.path.join(root_real, *parts))
            if os.path.commonpath([root_real, target]) != root_real:
                raise ProbePackageError("probe 包路径越界：%s" % member.name)
            if member.isdir():
                os.makedirs(target, mode=0o700, exist_ok=True)
                continue
            if not member.isfile():
                continue
            if member.size > MAX_FILE_BYTES:
                raise ProbePackageError("单文件超过 512 KB：%s" % member.name)
            total += member.size
            if total > MAX_PACKAGE_BYTES:
                raise ProbePackageError("probe 包总大小超过 2 MB")
            os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ProbePackageError("无法读取包内文件：%s" % member.name)
            with source, open(target, "wb") as handle:
                shutil.copyfileobj(source, handle, 65536)
            os.chmod(target, 0o700)
    finally:
        archive.close()
    return destination


def _find_package_root(directory):
    """返回包含 manifest.json 的根目录（允许包内多一层目录）。"""
    if os.path.isfile(os.path.join(directory, "manifest.json")):
        return directory
    candidates = []
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if name.startswith(".") or not os.path.isdir(path) or os.path.islink(path):
            continue
        if os.path.isfile(os.path.join(path, "manifest.json")):
            candidates.append(path)
    if len(candidates) == 1:
        return candidates[0]
    raise ProbePackageError("probe 包内必须且只能包含一个 manifest.json")


class LocalProbeStore:
    """管理 `/etc/vigil/probes.d` 下的本地 probe 包。"""

    def __init__(self, directory=DEFAULT_PROBES_DIR):
        self.directory = str(directory or DEFAULT_PROBES_DIR)

    def scan(self):
        """扫描目录，返回 ProbePackage 列表（含非法项，error 非空）。"""
        if not os.path.isdir(self.directory):
            return []
        packages = []
        for name in sorted(os.listdir(self.directory)):
            if name.startswith(".") or name.endswith(".old"):
                continue
            path = os.path.join(self.directory, name)
            if not os.path.isdir(path) or os.path.islink(path):
                continue
            try:
                packages.append(read_manifest(path, name))
            except ProbePackageError as exc:
                packages.append(
                    ProbePackage(probe_id=name, directory=path, error=str(exc))
                )
            except OSError as exc:
                packages.append(
                    ProbePackage(probe_id=name, directory=path, error="读取失败：%s" % exc)
                )
        return packages

    def load_into(self, registry):
        """把有效的本地 probe 注册进 registry，返回错误信息列表。"""
        errors = []
        for package in self.scan():
            if not package.valid:
                errors.append("%s: %s" % (package.id, package.error))
                continue
            try:
                registry.register(package.probe())
            except (TypeError, ValueError) as exc:
                errors.append(str(exc))
        return errors

    def install(self, data):
        """安装或升级 probe 包，返回已安装的 ProbePackage。"""
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ProbePackageError("probe 包为空")
        if len(data) > MAX_ARCHIVE_BYTES:
            raise ProbePackageError("probe 包超过允许大小")
        if not ensure_dir(self.directory, 0o700):
            raise ProbePackageError("无法创建 probe 目录：%s" % self.directory)

        temp_dir = tempfile.mkdtemp(prefix=".install-", dir=self.directory)
        try:
            extract_archive(bytes(data), temp_dir)
            root = _find_package_root(temp_dir)
            # 安装时以 manifest.id 为准，落盘目录名再做一致性约束
            package = read_manifest(root)
            target = os.path.join(self.directory, package.id)
            self._swap_in(root, target)
            package.directory = target
            return package
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def _swap_in(source, target):
        old = target + ".old"
        if os.path.isdir(old):
            shutil.rmtree(old, ignore_errors=True)
        elif os.path.exists(old):
            os.unlink(old)
        if os.path.exists(target):
            os.rename(target, old)
        try:
            os.rename(source, target)
        except OSError:
            if not os.path.exists(target) and os.path.isdir(old):
                os.rename(old, target)
            raise

    def remove(self, probe_id):
        """删除本地 probe 包；内置 probe 与非法 id 抛 ValueError。"""
        if not isinstance(probe_id, str) or not LOCAL_PROBE_ID_RE.match(probe_id):
            raise ValueError("非法的本地 probe id：%r" % (probe_id,))
        target = os.path.join(self.directory, probe_id)
        if not os.path.isdir(target) or os.path.islink(target):
            raise LookupError("本地 probe 不存在：%s" % probe_id)
        shutil.rmtree(target)
        old = target + ".old"
        if os.path.isdir(old):
            shutil.rmtree(old, ignore_errors=True)


def build_registry(directory=DEFAULT_PROBES_DIR):
    """内置 probe + 本地 probe 的注册表与存储。"""
    store = LocalProbeStore(directory)
    registry = builtin_registry()
    errors = store.load_into(registry)
    return registry, store, errors
