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
import subprocess
import sys
import tarfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "agent"
WEB_DIR = ROOT / "web"
CLIENT_UI_DIST = WEB_DIR / "client-ui" / "dist"
DIST = ROOT / "dist"

PACKAGE = "vigil-agent"
DESCRIPTION = "Vigil 客户端 agent（OpenWrt 服务监测与自愈）"
MAINTAINER = "Vigil"
DEPENDS = ["python3-light", "python3-urllib", "python3-openssl"]
IPK_ARCH = "all"
APK_ARCH = "noarch"

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
        (AGENT_DIR / "init.d" / "vigil").read_bytes(),
        0o755,
    )
    _write(
        staging / "usr/bin/vigil-beacon",
        (AGENT_DIR / "beacon" / "vigil-beacon.sh").read_bytes(),
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


def _pkginfo(staging, version, data_gz):
    lines = [
        "# Generated by scripts/package.py",
        "pkgname = %s" % PACKAGE,
        "pkgver = %s" % _apk_version(version),
        "pkgdesc = %s" % DESCRIPTION,
        "builddate = %d" % 0,
        "packager = %s" % MAINTAINER,
        "size = %d" % sum(
            path.stat().st_size for path in staging.rglob("*") if path.is_file()
        ),
        "arch = %s" % APK_ARCH,
    ]
    lines.extend("depend = %s" % item for item in DEPENDS)
    lines.append("datahash = %s" % hashlib.sha256(data_gz).hexdigest())
    return "\n".join(lines) + "\n"


def build_apk(staging, output, version):
    """apk v2：control 与 data 各自是独立的 gzip tar 流，顺序拼接。"""
    log("生成 %s" % output.name)
    data_gz = _gzip_bytes(_tar_bytes(_data_entries(staging, "")))
    control_entries = [
        (".PKGINFO", _pkginfo(staging, version, data_gz).encode("utf-8"), 0o644),
        (".post-install", POSTINST.encode("utf-8"), 0o755),
        (".pre-deinstall", PRERM.encode("utf-8"), 0o755),
    ]
    control_gz = _gzip_bytes(_tar_bytes(control_entries))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(control_gz + data_gz)
    return output


def _read_ipk(blob):
    """解析 OpenWrt ipk：gzip 外层 tar → 内含 control.tar.gz / data.tar.gz。"""
    outer = gzip.decompress(blob)
    members = {}
    for name in _tar_names(outer):
        members[name] = _tar_member(outer, name)
    return members


def _gzip_members(blob):
    members = []
    position = 0
    while position < len(blob):
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        data = decompressor.decompress(blob[position:])
        data += decompressor.flush()
        if not decompressor.eof:
            raise PackageError("gzip 流不完整")
        consumed = len(blob) - position - len(decompressor.unused_data)
        members.append((data, blob[position : position + consumed]))
        position += consumed
    return members


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


def verify_apk(path):
    members = _gzip_members(path.read_bytes())
    if len(members) != 2:
        raise PackageError("%s: 期望 2 个 gzip 流，实际 %d" % (path.name, len(members)))
    control_raw, _ = members[0]
    data_raw, data_gz = members[1]
    control = _tar_names(control_raw)
    for name in (".PKGINFO", ".post-install", ".pre-deinstall"):
        if name not in control:
            raise PackageError("%s: control 缺少 %s" % (path.name, name))

    pkginfo = _tar_member(control_raw, ".PKGINFO").decode("utf-8")
    fields = dict(
        line.split("=", 1) for line in pkginfo.splitlines() if "=" in line
    )
    fields = {key.strip(): value.strip() for key, value in fields.items()}
    if fields.get("pkgname") != PACKAGE:
        raise PackageError("%s: .PKGINFO pkgname 不合法" % path.name)
    expected = hashlib.sha256(data_gz).hexdigest()
    if fields.get("datahash") != expected:
        raise PackageError("%s: .PKGINFO datahash 与数据段不一致" % path.name)

    names = set(_tar_names(data_raw))
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
        artifacts.append(("apk", target, build_apk, verify_apk))

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
