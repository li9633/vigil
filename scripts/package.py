"""构建 OpenWrt 发布包（ipk + apk）。

与 ``scripts/deploy.py`` 的开发通道分离：deploy.py 负责把最新代码快速覆盖到
测试路由器和测试服务器，本脚本只产出可交付的安装包，由包管理器负责依赖、
目录、服务的安装与卸载。

用法::

    python scripts/package.py                     # 构建客户端 UI 后打包 ipk + apk
    python scripts/package.py --no-build          # 复用已有 web/client-ui/dist
    python scripts/package.py --format ipk        # 只打 ipk（22.03/23.05）
    python scripts/package.py --version 1.1.0 --out dist

产物::

    dist/vigil-agent_<version>_all.ipk
    dist/vigil-agent_<version>_noarch.apk

两个包都声明为架构无关（ipk: all / apk: noarch），因为 agent 只依赖 Python
标准库，没有编译产物。

ipk 面向 opkg 系统（OpenWrt 22.03 ~ 24.10），apk 面向 apk-tools 3 系统
（OpenWrt 25.12+ / ImmortalWrt 25.x）。apk v3 使用 ADB 二进制格式而非
v2 的 gzip tar 拼接，因此这里直接按 ADB 规范写出，保证包管理器可安装。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import zlib
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "agent"
WEB_DIR = ROOT / "web"
CLIENT_UI_DIST = WEB_DIR / "client-ui" / "dist"
DIST = ROOT / "dist"

PACKAGE = "vigil-agent"
DESCRIPTION = "Vigil 客户端 agent（OpenWrt 服务监测与自愈）"
MAINTAINER = "Vigil"
DEPENDS = [
    "python3-codecs",
    "python3-light",
    "python3-logging",
    "python3-openssl",
    "python3-urllib",
    "python3-uuid",
]
IPK_ARCH = "all"
APK_ARCH = "noarch"

# apk v3（ADB）格式常量，取值来自 apk-tools 的 adb.h / apk_adb.h
ADB_MAGIC = 0x2E424441  # "ADB."
ADB_SCHEMA_PACKAGE = 0x676B6370  # "pckg"
ADB_TYPE_INT = 0x10000000
ADB_TYPE_INT_32 = 0x20000000
ADB_TYPE_INT_64 = 0x30000000
ADB_TYPE_BLOB_8 = 0x80000000
ADB_TYPE_BLOB_16 = 0x90000000
ADB_TYPE_BLOB_32 = 0xA0000000
ADB_TYPE_ARRAY = 0xD0000000
ADB_TYPE_OBJECT = 0xE0000000
ADB_TYPE_MASK = 0xF0000000
ADB_VALUE_MASK = 0x0FFFFFFF
ADB_BLOCK_ADB = 0
ADB_BLOCK_DATA = 2
ADB_BLOCK_ALIGNMENT = 8

# apk 通过哈希长度判定算法：20=SHA1、32=SHA256、64=SHA512，blob 内不写算法字节
APK_DIGEST_LENGTH_SHA256 = 32

CONFIG_PATH = "etc/vigil/config.json"
PROTECTED_PATHS = "etc/apk/protected_paths.d/vigil.list"

# 打包时需要保留可执行位的路径；其余文件按 0644 处理
MODES = {
    "etc/init.d/vigil": 0o755,
    "usr/bin/vigil-beacon": 0o755,
    CONFIG_PATH: 0o600,
    "etc/vigil/probes.d": 0o700,
}

EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}

POSTINST = """#!/bin/sh
# Vigil agent 安装后脚本：创建运行目录、注册 beacon cron、放行 LAN 端口并启动服务。
set -e

mkdir -p /etc/vigil /etc/vigil/probes.d /var/run/vigil /overlay/vigil
chmod 700 /etc/vigil/probes.d 2>/dev/null || true
chmod 755 /etc/init.d/vigil /usr/bin/vigil-beacon 2>/dev/null || true

# beacon cron：先删除旧行再追加，避免重复安装产生多条
CRON=/etc/crontabs/root
mkdir -p /etc/crontabs
if [ ! -f "$CRON" ]; then
    : > "$CRON"
fi
sed -i '/vigil-beacon/d' "$CRON" 2>/dev/null || true
echo '* * * * * /usr/bin/vigil-beacon >/dev/null 2>&1' >> "$CRON"
chmod 600 "$CRON" 2>/dev/null || true
/etc/init.d/cron reload >/dev/null 2>&1 || /etc/init.d/cron restart >/dev/null 2>&1 || true

# 仅放行 LAN 访问 8787；WAN 侧不开放
if command -v uci >/dev/null 2>&1; then
    if ! uci -q get firewall.vigil >/dev/null 2>&1; then
        uci set firewall.vigil=rule
        uci set firewall.vigil.name='Allow-Vigil-UI'
        uci set firewall.vigil.src='lan'
        uci set firewall.vigil.proto='tcp'
        uci set firewall.vigil.dest_port='8787'
        uci set firewall.vigil.target='ACCEPT'
        uci commit firewall
        /etc/init.d/firewall reload >/dev/null 2>&1 || true
    fi
fi

/etc/init.d/vigil enable >/dev/null 2>&1 || true
/etc/init.d/vigil restart >/dev/null 2>&1 || true
exit 0
"""

PRERM = """#!/bin/sh
# Vigil agent 卸载前脚本：停止服务、移除 cron 与防火墙规则。
# 默认保留 /etc/vigil（配置与令牌）和 /overlay/vigil（状态与 spool）。
/etc/init.d/vigil stop >/dev/null 2>&1 || true
/etc/init.d/vigil disable >/dev/null 2>&1 || true

if [ -f /etc/crontabs/root ]; then
    sed -i '/vigil-beacon/d' /etc/crontabs/root 2>/dev/null || true
fi
/etc/init.d/cron reload >/dev/null 2>&1 || true

if command -v uci >/dev/null 2>&1; then
    if uci -q get firewall.vigil >/dev/null 2>&1; then
        uci delete firewall.vigil
        uci commit firewall
        /etc/init.d/firewall reload >/dev/null 2>&1 || true
    fi
fi
exit 0
"""


class PackageError(RuntimeError):
    """打包过程中的可预期失败。"""


def log(message):
    print("[package] %s" % message, flush=True)


def _agent_version():
    text = (AGENT_DIR / "vigil_agent" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise PackageError("无法从 agent/vigil_agent/__init__.py 读取版本号")
    return match.group(1)


def _apk_version(version):
    return version if "-r" in version else "%s-r0" % version


def build_ui(dry=False):
    if dry:
        log("(dry-run) npm run build:client-ui")
        return
    log("构建客户端 UI")
    if os.name == "nt":
        cmd = ["cmd", "/c", "npm run build:client-ui"]
    else:
        cmd = [shutil.which("npm") or "npm", "run", "build:client-ui"]
    result = subprocess.run(cmd, cwd=str(WEB_DIR))
    if result.returncode != 0:
        raise PackageError("客户端 UI 构建失败（退出码 %s）" % result.returncode)


def _copy_tree(src, dst):
    for path in sorted(src.rglob("*")):
        relative = path.relative_to(src)
        if any(part in EXCLUDE_DIRS for part in relative.parts):
            continue
        if path.suffix in EXCLUDE_SUFFIXES:
            continue
        target = dst / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


def _write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _shell_bytes(path):
    """脚本必须使用 LF 换行：Windows 检出会带 CRLF，导致 shebang 解析失败。"""
    return path.read_bytes().replace(b"\r\n", b"\n")


def _default_config():
    """复用 agent 的缺省配置，避免打包模板与代码漂移。"""
    sys.path.insert(0, str(AGENT_DIR))
    try:
        from vigil_agent.config import default_config
    finally:
        sys.path.pop(0)
    return default_config()


def stage_root(staging):
    """把要安装的文件按目标路径铺到 staging 目录。"""
    log("准备安装目录 %s" % staging)
    if staging.exists():
        shutil.rmtree(staging)

    _copy_tree(
        AGENT_DIR / "vigil_agent", staging / "usr/lib/vigil/vigil_agent"
    )

    if not (CLIENT_UI_DIST / "index.html").is_file():
        raise PackageError(
            "客户端 UI 构建产物不存在：%s（去掉 --no-build 重新构建）"
            % CLIENT_UI_DIST
        )
    _copy_tree(CLIENT_UI_DIST, staging / "usr/share/vigil/www")

    _write(
        staging / "etc/init.d/vigil",
        _shell_bytes(AGENT_DIR / "init.d" / "vigil"),
        0o755,
    )
    _write(
        staging / "usr/bin/vigil-beacon",
        _shell_bytes(AGENT_DIR / "beacon" / "vigil-beacon.sh"),
        0o755,
    )

    config = json.dumps(_default_config(), ensure_ascii=False, indent=2) + "\n"
    _write(staging / CONFIG_PATH, config.encode("utf-8"), 0o600)

    (staging / "etc/vigil/probes.d").mkdir(parents=True, exist_ok=True)
    _write(
        staging / PROTECTED_PATHS,
        ("%s\n" % CONFIG_PATH).encode("utf-8"),
        0o644,
    )


def _tar_bytes(entries):
    """entries: [(arcname, data 或 None(目录), mode)]。mtime/属主固定，便于复现。"""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for arcname, data, mode in entries:
            info = tarfile.TarInfo(arcname)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mode = mode
            if data is None:
                info.type = tarfile.DIRTYPE
                info.size = 0
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _gzip_bytes(data):
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as handle:
        handle.write(data)
    return buffer.getvalue()


def _data_entries(staging, prefix):
    entries = []
    for path in sorted(staging.rglob("*")):
        relative = path.relative_to(staging).as_posix()
        mode = MODES.get(relative, 0o755 if path.is_dir() else 0o644)
        if path.is_dir():
            entries.append((prefix + relative + "/", None, mode))
        else:
            entries.append((prefix + relative, path.read_bytes(), mode))
    return entries


def _installed_size(staging):
    total = 0
    for path in staging.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return max(1, (total + 1023) // 1024)


def _control_text(version, staging):
    lines = [
        "Package: %s" % PACKAGE,
        "Version: %s" % version,
        "Depends: %s" % ", ".join(DEPENDS),
        "Source: vigil",
        "Section: net",
        "Priority: optional",
        "Maintainer: %s" % MAINTAINER,
        "Architecture: %s" % IPK_ARCH,
        "Installed-Size: %d" % _installed_size(staging),
        "Description: %s" % DESCRIPTION,
    ]
    return "\n".join(lines) + "\n"


def build_ipk(staging, output, version):
    """OpenWrt ipk：外层是 gzip 压缩的 tar，内含 debian-binary 与两个 tar.gz。

    opkg（opkg-lede）的解包实现直接用 `gzip -d` 读外层，再按 tar 头扫描
    control.tar.gz / data.tar.gz，因此不能用 Debian 的 ar 封装。
    """
    log("生成 %s" % output.name)
    control_entries = [
        ("./control", _control_text(version, staging).encode("utf-8"), 0o644),
        ("./conffiles", ("/%s\n" % CONFIG_PATH).encode("utf-8"), 0o644),
        ("./postinst", POSTINST.encode("utf-8"), 0o755),
        ("./prerm", PRERM.encode("utf-8"), 0o755),
    ]
    control = _gzip_bytes(_tar_bytes(control_entries))
    data = _gzip_bytes(_tar_bytes(_data_entries(staging, "./")))
    outer = _tar_bytes(
        [
            ("./debian-binary", b"2.0\n", 0o644),
            ("./control.tar.gz", control, 0o644),
            ("./data.tar.gz", data, 0o644),
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_gzip_bytes(outer))
    return output


class _AdbBuilder:
    """够用的 ADB 写入器：只覆盖 apk 包元数据所需的值类型。

    偏移量相对 ADB 块 payload（含 8 字节 adb_hdr），与 apk-tools 的
    adb_w_data() 语义一致；写入顺序可以自底向上，读取端只认偏移。
    """

    def __init__(self):
        self.buf = bytearray(struct.pack("<BBHI", 0, 0, 0, 0))

    def _raw(self, data, alignment):
        pad = (-len(self.buf)) % alignment
        if pad:
            self.buf += b"\x00" * pad
        offset = len(self.buf)
        self.buf += data
        return offset

    def blob(self, data):
        data = bytes(data)
        if not data:
            return 0
        if len(data) <= 0xFF:
            prefix, alignment, kind = struct.pack("<B", len(data)), 1, ADB_TYPE_BLOB_8
        elif len(data) <= 0xFFFF:
            prefix, alignment, kind = struct.pack("<H", len(data)), 2, ADB_TYPE_BLOB_16
        else:
            prefix, alignment, kind = struct.pack("<I", len(data)), 4, ADB_TYPE_BLOB_32
        return kind | self._raw(prefix + data, alignment)

    def integer(self, value):
        if value < 0x10000000:
            return ADB_TYPE_INT | value
        if value < 0x100000000:
            return ADB_TYPE_INT_32 | self._raw(struct.pack("<I", value), 4)
        return ADB_TYPE_INT_64 | self._raw(struct.pack("<Q", value), 4)

    def obj(self, fields):
        """fields 为 {槽位号(1 起): adb_val}，缺失槽位写 NULL。"""
        last = max(fields) if fields else 0
        return self._container(ADB_TYPE_OBJECT, [fields.get(i, 0) for i in range(1, last + 1)])

    def arr(self, items):
        return self._container(ADB_TYPE_ARRAY, list(items))

    def _container(self, kind, slots):
        values = [0] + slots
        num = len(values)
        while num > 1 and values[num - 1] == 0:
            num -= 1
        if num <= 1:
            return 0
        values = values[:num]
        values[0] = num
        offset = self._raw(struct.pack("<%dI" % num, *values), 4)
        return kind | offset

    def finish(self, root):
        struct.pack_into("<I", self.buf, 4, root)
        return bytes(self.buf)


def _apk_entries(staging):
    """收集安装树：目录名 → [(文件名, Path)]，根目录用空名表示。"""
    dirs = {}
    for path in staging.rglob("*"):
        if path.is_dir():
            dirs.setdefault(path.relative_to(staging).as_posix(), [])
    for path in staging.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(staging).as_posix()
        parent = relative.rpartition("/")[0]
        dirs.setdefault(parent, []).append((path.name, path))
    return dirs


def _apk_mode(staging, path):
    relative = path.relative_to(staging).as_posix()
    return MODES.get(relative, 0o755 if path.is_dir() else 0o644)


def _apk_acl(builder, mode):
    return builder.obj(
        {
            1: builder.integer(mode),
            2: builder.blob(b"root"),
            3: builder.blob(b"root"),
        }
    )


def build_apk(staging, output, version):
    """apk v3：文件头 + 一个 ADB 元数据块 + 若干 DATA 块，整体用 deflate 包裹。"""
    log("生成 %s" % output.name)
    dirs = _apk_entries(staging)
    path_names = [""] + sorted(name for name in dirs if name)

    builder = _AdbBuilder()
    installed_size = 0
    paths = []
    data_blocks = []

    for path_index, dirname in enumerate(path_names, start=1):
        files = []
        for file_index, (name, path) in enumerate(
            sorted(dirs.get(dirname, []), key=lambda item: item[0].encode()), start=1
        ):
            content = path.read_bytes()
            size = len(content)
            installed_size += size
            digest = hashlib.sha256(content).digest()
            fields = {
                1: builder.blob(name.encode("utf-8")),
                2: _apk_acl(builder, _apk_mode(staging, path)),
                3: builder.integer(size),
                4: builder.integer(0),
                5: builder.blob(digest),
            }
            files.append(builder.obj(fields))
            if size:
                data_blocks.append((path_index, file_index, content))

        paths.append(
            builder.obj(
                {
                    1: builder.blob(dirname.encode("utf-8")),
                    2: _apk_acl(
                        builder,
                        _apk_mode(staging, staging if not dirname else staging / dirname),
                    ),
                    3: builder.arr(files),
                }
            )
        )

    depends = builder.arr(
        [builder.obj({1: builder.blob(name.encode("utf-8"))}) for name in sorted(DEPENDS)]
    )

    # HASHES 是 20 字节的包唯一标识：先占位，最后用 payload 的 SHA-256 前 20 字节覆盖
    hashes_tag = builder.blob(b"\x00" * 20)
    hashes_offset = (hashes_tag & ADB_VALUE_MASK) + 1
    pkginfo = builder.obj(
        {
            1: builder.blob(PACKAGE.encode("utf-8")),
            2: builder.blob(_apk_version(version).encode("utf-8")),
            3: hashes_tag,
            4: builder.blob(DESCRIPTION.encode("utf-8")),
            5: builder.blob(APK_ARCH.encode("utf-8")),
            6: builder.blob(b"MIT"),
            7: builder.blob(b"vigil"),
            8: builder.blob(MAINTAINER.encode("utf-8")),
            12: builder.integer(max(1, installed_size)),
            15: depends,
        }
    )

    scripts = builder.obj(
        {
            3: builder.blob(POSTINST.encode("utf-8")),
            4: builder.blob(PRERM.encode("utf-8")),
            7: builder.blob(POSTINST.encode("utf-8")),
        }
    )

    package = builder.obj({1: pkginfo, 2: builder.arr(paths), 3: scripts})
    payload = bytearray(builder.finish(package))
    payload[hashes_offset : hashes_offset + 20] = hashlib.sha256(payload).digest()[:20]

    body = struct.pack("<II", ADB_MAGIC, ADB_SCHEMA_PACKAGE)
    body += _adb_block(ADB_BLOCK_ADB, bytes(payload))
    for path_index, file_index, content in data_blocks:
        header = struct.pack("<II", path_index, file_index) + content
        body += _adb_block(ADB_BLOCK_DATA, header)

    compressor = zlib.compressobj(0, zlib.DEFLATED, -zlib.MAX_WBITS)
    compressed = compressor.compress(body) + compressor.flush()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"ADBd" + compressed)
    return output


def _adb_block(block_type, payload):
    raw_size = 4 + len(payload)
    if raw_size > 0x3FFFFFFF:
        raise PackageError("ADB 块过大，需要扩展块头")
    raw = struct.pack("<I", (block_type << 30) | raw_size) + payload
    padding = (-len(raw)) % ADB_BLOCK_ALIGNMENT
    return raw + b"\x00" * padding


def _read_ipk(blob):
    """解析 OpenWrt ipk：gzip 外层 tar → 内含 control.tar.gz / data.tar.gz。"""
    outer = gzip.decompress(blob)
    members = {}
    for name in _tar_names(outer):
        members[name] = _tar_member(outer, name)
    return members


class _AdbReader:
    """与 _AdbBuilder 对称的最小读取器，用于打包后自检。"""

    def __init__(self, data):
        self.data = data

    def u32(self, offset):
        return struct.unpack_from("<I", self.data, offset)[0]

    def slots(self, tag):
        if tag == 0:
            return []
        offset = tag & ADB_VALUE_MASK
        count = self.u32(offset)
        return [self.u32(offset + 4 * index) for index in range(count)]

    def blob(self, tag):
        kind = tag & ADB_TYPE_MASK
        offset = tag & ADB_VALUE_MASK
        if kind == ADB_TYPE_BLOB_8:
            length, start = self.data[offset], offset + 1
        elif kind == ADB_TYPE_BLOB_16:
            length, start = struct.unpack_from("<H", self.data, offset)[0], offset + 2
        elif kind == ADB_TYPE_BLOB_32:
            length, start = struct.unpack_from("<I", self.data, offset)[0], offset + 4
        else:
            return b""
        return bytes(self.data[start : start + length])

    def integer(self, tag):
        kind = tag & ADB_TYPE_MASK
        if kind == ADB_TYPE_INT:
            return tag & ADB_VALUE_MASK
        offset = tag & ADB_VALUE_MASK
        if kind == ADB_TYPE_INT_32:
            return struct.unpack_from("<I", self.data, offset)[0]
        if kind == ADB_TYPE_INT_64:
            return struct.unpack_from("<Q", self.data, offset)[0]
        return 0


def _adb_slot(slots, index):
    return slots[index] if index < len(slots) else 0


def _apk_body(blob):
    if blob[:3] != b"ADB":
        raise PackageError("不是 apk v3（ADB）格式")
    mode = blob[3:4]
    if mode == b".":
        return blob
    if mode == b"d":
        return zlib.decompress(blob[4:], -zlib.MAX_WBITS)
    if mode == b"c" and len(blob) >= 6:
        algorithm = blob[4]
        if algorithm == 0:
            return blob[6:]
        if algorithm == 1:
            return zlib.decompress(blob[6:], -zlib.MAX_WBITS)
        raise PackageError("不支持的 apk 压缩算法 %d" % algorithm)
    raise PackageError("无法识别的 apk 压缩头")


def _apk_blocks(body):
    if len(body) < 8 or body[:4] != struct.pack("<I", ADB_MAGIC):
        raise PackageError("ADB 文件头不合法")
    blocks = []
    position = 8
    while position < len(body):
        if position + 4 > len(body):
            raise PackageError("ADB 块头不完整")
        tag = struct.unpack_from("<I", body, position)[0]
        if tag >> 30 == 3:
            if position + 16 > len(body):
                raise PackageError("ADB 扩展块头不完整")
            raw_size = struct.unpack_from("<Q", body, position + 8)[0]
            header, block_type = 16, tag & 0x3FFFFFFF
        else:
            raw_size, header, block_type = tag & 0x3FFFFFFF, 4, tag >> 30
        if raw_size < header or position + raw_size > len(body):
            raise PackageError("ADB 块长度不合法")
        blocks.append((block_type, body[position + header : position + raw_size]))
        aligned = (raw_size + ADB_BLOCK_ALIGNMENT - 1) // ADB_BLOCK_ALIGNMENT
        position += aligned * ADB_BLOCK_ALIGNMENT
    return blocks


def verify_apk(path, staging=None):
    blob = path.read_bytes()
    body = _apk_body(blob)
    if struct.unpack_from("<I", body, 4)[0] != ADB_SCHEMA_PACKAGE:
        raise PackageError("%s: ADB schema 不是 package" % path.name)

    blocks = _apk_blocks(body)
    if not blocks or blocks[0][0] != ADB_BLOCK_ADB:
        raise PackageError("%s: 缺少 ADB 元数据块" % path.name)
    if any(block_type != ADB_BLOCK_DATA for block_type, _ in blocks[1:]):
        raise PackageError("%s: 存在非 DATA 的多余块" % path.name)

    reader = _AdbReader(blocks[0][1])
    root = reader.slots(reader.u32(4))
    info = reader.slots(_adb_slot(root, 1))
    if reader.blob(_adb_slot(info, 1)) != PACKAGE.encode("utf-8"):
        raise PackageError("%s: 包名不是 %s" % (path.name, PACKAGE))
    if reader.blob(_adb_slot(info, 5)) != APK_ARCH.encode("utf-8"):
        raise PackageError("%s: arch 不是 %s" % (path.name, APK_ARCH))
    if len(reader.blob(_adb_slot(info, 3))) != 20:
        raise PackageError("%s: hashes 长度不是 20 字节" % path.name)
    if not reader.blob(_adb_slot(info, 2)):
        raise PackageError("%s: 缺少版本号" % path.name)

    depends = {
        reader.blob(_adb_slot(reader.slots(item), 1)).decode("utf-8")
        for item in reader.slots(_adb_slot(info, 15))[1:]
    }
    if depends != set(DEPENDS):
        raise PackageError("%s: 依赖项不一致 %s" % (path.name, sorted(depends)))

    scripts = reader.slots(_adb_slot(root, 3))
    for index, name in ((3, "post-install"), (4, "pre-deinstall"), (7, "post-upgrade")):
        if not reader.blob(_adb_slot(scripts, index)):
            raise PackageError("%s: 缺少 %s 脚本" % (path.name, name))

    installed = {}
    data_files = []
    paths = reader.slots(_adb_slot(root, 2))
    for path_index in range(1, len(paths)):
        path_slots = reader.slots(paths[path_index])
        dirname = reader.blob(_adb_slot(path_slots, 1)).decode("utf-8")
        acl = reader.slots(_adb_slot(path_slots, 2))
        installed[dirname] = ("dir", reader.integer(_adb_slot(acl, 1)))
        files = reader.slots(_adb_slot(path_slots, 3))
        for file_index in range(1, len(files)):
            file_slots = reader.slots(files[file_index])
            filename = reader.blob(_adb_slot(file_slots, 1)).decode("utf-8")
            file_acl = reader.slots(_adb_slot(file_slots, 2))
            size = reader.integer(_adb_slot(file_slots, 3))
            digest = reader.blob(_adb_slot(file_slots, 5))
            target = reader.blob(_adb_slot(file_slots, 6))
            full = "%s/%s" % (dirname, filename) if dirname else filename
            installed[full] = (
                "file",
                reader.integer(_adb_slot(file_acl, 1)),
                size,
                digest,
                target,
            )
            if len(digest) != APK_DIGEST_LENGTH_SHA256:
                raise PackageError("%s: %s 缺少 sha256" % (path.name, full))
            if size and not target:
                data_files.append((path_index, file_index, size, digest))

    missing = REQUIRED_PATHS - set(installed)
    if missing:
        raise PackageError("%s: 缺少 %s" % (path.name, sorted(missing)))

    if staging is not None:
        _verify_apk_staging(path, staging, installed)

    data_blocks = [payload for block_type, payload in blocks[1:]]
    if len(data_blocks) != len(data_files):
        raise PackageError(
            "%s: DATA 块数量 %d 与文件数 %d 不一致"
            % (path.name, len(data_blocks), len(data_files))
        )
    for payload, (path_index, file_index, size, digest) in zip(data_blocks, data_files):
        if len(payload) != 8 + size:
            raise PackageError("%s: DATA 块长度不匹配" % path.name)
        header = struct.unpack_from("<II", payload, 0)
        if header != (path_index, file_index):
            raise PackageError("%s: DATA 块索引不匹配" % path.name)
        if hashlib.sha256(payload[8:]).digest() != digest:
            raise PackageError("%s: DATA 块内容哈希不匹配" % path.name)

    log("校验通过：%s（%d 个文件）" % (path.name, len(installed)))
    return None


def _verify_apk_staging(path, staging, installed):
    expected = {"": ("dir", _apk_mode(staging, staging))}
    for item in staging.rglob("*"):
        relative = item.relative_to(staging).as_posix()
        if item.is_dir():
            expected[relative] = ("dir", _apk_mode(staging, item))
            continue
        content = item.read_bytes()
        expected[relative] = (
            "file",
            _apk_mode(staging, item),
            len(content),
            hashlib.sha256(content).digest(),
        )
    if set(installed) != set(expected):
        missing = sorted(set(expected) - set(installed))
        extra = sorted(set(installed) - set(expected))
        raise PackageError(
            "%s: 安装树与暂存目录不一致 缺少=%s 多余=%s" % (path.name, missing, extra)
        )
    for name, want in expected.items():
        got = installed[name]
        if got[0] != want[0] or got[1] != want[1]:
            raise PackageError("%s: %s 的类型或权限不一致" % (path.name, name))
        if want[0] == "file" and (got[2] != want[2] or got[3] != want[3]):
            raise PackageError("%s: %s 的大小或哈希不一致" % (path.name, name))


def _norm_name(name):
    """去掉 tar 里的 "./" 或 "/" 前缀；保留 .PKGINFO 这类以点开头的文件名。"""
    if name.startswith("./"):
        return name[2:]
    if name.startswith("/"):
        return name[1:]
    return name


def _tar_names(data):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        return [_norm_name(member.name) for member in tar.getmembers()]


def _tar_member(data, name):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        for member in tar.getmembers():
            if _norm_name(member.name) == name:
                handle = tar.extractfile(member)
                return handle.read() if handle is not None else None
    return None


REQUIRED_PATHS = {
    "usr/lib/vigil/vigil_agent/main.py",
    "usr/lib/vigil/vigil_agent/config.py",
    "usr/share/vigil/www/index.html",
    "etc/init.d/vigil",
    "etc/vigil/probes.d",
    "usr/bin/vigil-beacon",
    CONFIG_PATH,
}


def verify_ipk(path):
    members = _read_ipk(path.read_bytes())
    if members.get("debian-binary") != b"2.0\n":
        raise PackageError("%s: debian-binary 不合法" % path.name)
    control_gz = members.get("control.tar.gz")
    data_gz = members.get("data.tar.gz")
    if control_gz is None or data_gz is None:
        raise PackageError("%s: 缺少 control.tar.gz 或 data.tar.gz" % path.name)
    control = _tar_names(gzip.decompress(control_gz))
    for name in ("control", "conffiles", "postinst", "prerm"):
        if name not in control:
            raise PackageError("%s: control 缺少 %s" % (path.name, name))
    text = _tar_member(gzip.decompress(control_gz), "control").decode("utf-8")
    for field in ("Package: %s" % PACKAGE, "Architecture: %s" % IPK_ARCH):
        if field not in text:
            raise PackageError("%s: control 缺少字段 %s" % (path.name, field))
    names = set(_tar_names(gzip.decompress(data_gz)))
    missing = REQUIRED_PATHS - names
    if missing:
        raise PackageError("%s: data 缺少 %s" % (path.name, sorted(missing)))
    log("校验通过：%s（%d 个文件）" % (path.name, len(names)))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="构建 Vigil 的 OpenWrt 安装包")
    parser.add_argument(
        "--version", default=None, help="包版本，默认取 agent/vigil_agent/__init__.py"
    )
    parser.add_argument(
        "--format",
        dest="formats",
        default="all",
        choices=["all", "ipk", "apk"],
        help="产出格式，默认 all",
    )
    parser.add_argument("--out", default=str(DIST), help="输出目录，默认 dist/")
    parser.add_argument(
        "--no-build", action="store_true", help="跳过客户端 UI 构建，复用已有 dist"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印将执行的动作，不写文件"
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    version = args.version or _agent_version()
    output_dir = Path(args.out)
    formats = ["ipk", "apk"] if args.formats == "all" else [args.formats]

    if not args.no_build:
        build_ui(dry=args.dry_run)

    staging = output_dir / ".staging"
    if args.dry_run:
        log("(dry-run) 暂存目录 %s" % staging)
    else:
        stage_root(staging)

    artifacts = []
    if "ipk" in formats:
        target = output_dir / ("%s_%s_%s.ipk" % (PACKAGE, version, IPK_ARCH))
        artifacts.append(("ipk", target, build_ipk, verify_ipk))
    if "apk" in formats:
        target = output_dir / ("%s_%s_%s.apk" % (PACKAGE, version, APK_ARCH))
        artifacts.append(
            ("apk", target, build_apk, partial(verify_apk, staging=staging))
        )

    for _, target, builder, verifier in artifacts:
        if args.dry_run:
            log("(dry-run) 生成 %s" % target)
            continue
        builder(staging, target, version)
        verifier(target)

    if not args.dry_run:
        shutil.rmtree(staging, ignore_errors=True)
        for _, target, _, _ in artifacts:
            log("产物：%s（%.1f KB）" % (target, target.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PackageError as exc:
        print("[package] 失败：%s" % exc, file=sys.stderr)
        sys.exit(1)
