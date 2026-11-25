#!/usr/bin/env python3
"""Vigil 一键部署脚本（仅使用标准库，Windows / Linux 通用）。

在仓库根目录执行：
    python scripts/deploy.py client     # 构建并部署客户端 agent + 客户端 UI
    python scripts/deploy.py server     # 构建并部署服务端 app + 服务端 UI
    python scripts/deploy.py all        # 两者都部署

常用选项：
    --no-tests      跳过单元测试
    --no-build      跳过前端构建（复用已有 dist）
    --dry-run       只打印将执行的命令，不实际执行
    --keep-remote   保留远端 /tmp 下的部署包

连接参数可用环境变量覆盖：
    VIGIL_CLIENT_SSH     客户端 SSH 目标，默认 openwrt（读取 ~/.ssh/config）
    VIGIL_CLIENT_KEY     客户端 SSH 私钥（可选）
    VIGIL_SERVER_HOST    服务端主机，默认 vigil.example.com
    VIGIL_SERVER_PORT    服务端 SSH 端口，默认 10086
    VIGIL_SERVER_USER    服务端 SSH 用户，默认 root
    VIGIL_SERVER_KEY     服务端私钥，默认 ~/.ssh/vigil

部署方式：远端保留上一版本目录（.old），新版解包成功后再替换并重启服务，
最后做进程与端口健康检查；任一检查失败脚本以非 0 退出，便于人工回滚。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
AGENT_DIR = ROOT / "agent"
SERVER_DIR = ROOT / "server"
WEB_DIR = ROOT / "web"
CLIENT_UI_DIST = WEB_DIR / "client-ui" / "dist"
SERVER_UI_DIST = WEB_DIR / "server-ui" / "dist"

AGENT_PACKAGE = DIST / "vigil-agent.tar.gz"
CLIENT_UI_PACKAGE = DIST / "vigil-client-ui.tar.gz"
SERVER_APP_PACKAGE = DIST / "vigil-server-app.tar.gz"
SERVER_UI_PACKAGE = DIST / "vigil-server-ui.tar.gz"

CLIENT_SCRIPT = """
set -e
CODE_DIR=/usr/lib/vigil/vigil_agent
UI_DIR=/usr/share/vigil/www

rm -rf $CODE_DIR.new $UI_DIR.new
mkdir -p $CODE_DIR.new $UI_DIR.new
tar xzf /tmp/vigil-agent.tar.gz -C $CODE_DIR.new
tar xzf /tmp/vigil-client-ui.tar.gz -C $UI_DIR.new

rm -rf $CODE_DIR.old $UI_DIR.old
mv $CODE_DIR $CODE_DIR.old
mv $CODE_DIR.new $CODE_DIR
mv $UI_DIR $UI_DIR.old
mv $UI_DIR.new $UI_DIR

/etc/init.d/vigil restart
sleep 3

ps w | grep -v grep | grep -q "vigil_agent.main" || { echo "client: agent 进程未启动"; exit 1; }

ok=0
i=0
while [ $i -lt 10 ]; do
    if curl -fsS -o /dev/null http://127.0.0.1:8787/; then ok=1; break; fi
    i=$((i + 1))
    sleep 1
done
if [ $ok -ne 1 ]; then echo "client: UI 健康检查失败"; exit 1; fi
echo "client: 部署完成"
"""

SERVER_SCRIPT = """
set -e
cd /opt/vigil

rm -rf app.new
mkdir -p app.new
tar xzf /tmp/vigil-server-app.tar.gz -C app.new
rm -rf app.old
mv app app.old
mv app.new app
chown -R vigil:vigil app

rm -rf /var/www/vigil.new
mkdir -p /var/www/vigil.new
tar xzf /tmp/vigil-server-ui.tar.gz -C /var/www/vigil.new
rm -rf /var/www/vigil.old
mv /var/www/vigil /var/www/vigil.old
mv /var/www/vigil.new /var/www/vigil
chmod -R a+rX /var/www/vigil

systemctl restart vigil-api vigil-worker
sleep 3
systemctl is-active --quiet vigil-api || { echo "server: vigil-api 未运行"; exit 1; }
systemctl is-active --quiet vigil-worker || { echo "server: vigil-worker 未运行"; exit 1; }

ok=0
i=0
while [ $i -lt 5 ]; do
    if curl -fsS -o /dev/null http://127.0.0.1:8000/healthz; then ok=1; break; fi
    i=$((i + 1))
    sleep 1
done
if [ $ok -ne 1 ]; then echo "server: API 健康检查失败"; exit 1; fi

curl -fsS -o /dev/null http://127.0.0.1:5000/ || { echo "server: UI 健康检查失败"; exit 1; }
echo "server: 部署完成"
"""


class DeployError(RuntimeError):
    """部署过程中的可预期失败。"""


def log(message):
    print("[deploy] %s" % message, flush=True)


def run(cmd, cwd=None, dry=False, input_text=None):
    display = " ".join(str(item) for item in cmd)
    if cwd:
        log("(cd %s) $ %s" % (cwd, display))
    else:
        log("$ %s" % display)
    if dry:
        return
    result = subprocess.run(
        [str(item) for item in cmd],
        cwd=str(cwd) if cwd else None,
        input=input_text.encode("utf-8") if input_text is not None else None,
    )
    if result.returncode != 0:
        raise DeployError("命令失败（退出码 %s）：%s" % (result.returncode, display))


def npm_run(script, dry=False):
    if os.name == "nt":
        # npm 在 Windows 上是 npm.cmd，需要经 cmd 解释器执行
        cmd = ["cmd", "/c", "npm run %s" % script]
    else:
        cmd = [shutil.which("npm") or "npm", "run", script]
    run(cmd, cwd=WEB_DIR, dry=dry)


def pack(src, out, dry=False):
    log("打包 %s -> %s" % (src, out))
    if dry:
        return
    if not src.is_dir():
        raise DeployError("待打包目录不存在：%s" % src)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        for path in sorted(src.rglob("*")):
            if "__pycache__" in path.parts:
                continue
            if path.suffix in (".pyc", ".pyo"):
                continue
            tar.add(path, arcname=str(Path(".") / path.relative_to(src)))


def client_conn():
    return {
        "host": os.environ.get("VIGIL_CLIENT_SSH", "openwrt"),
        "user": None,
        "port": None,
        "key": os.environ.get("VIGIL_CLIENT_KEY"),
        "alias": True,
    }


def server_conn():
    try:
        port = int(os.environ.get("VIGIL_SERVER_PORT", "10086"))
    except ValueError:
        raise DeployError("VIGIL_SERVER_PORT 必须是整数")
    return {
        "host": os.environ.get("VIGIL_SERVER_HOST", "vigil.example.com"),
        "user": os.environ.get("VIGIL_SERVER_USER", "root"),
        "port": port,
        "key": os.environ.get("VIGIL_SERVER_KEY", "~/.ssh/vigil"),
        "alias": False,
    }


def _dest(conn):
    if conn.get("user"):
        return "%s@%s" % (conn["user"], conn["host"])
    return conn["host"]


def ssh(conn, script, dry=False):
    cmd = ["ssh"]
    if conn.get("key"):
        cmd += ["-i", conn["key"]]
    if conn.get("port"):
        cmd += ["-p", str(conn["port"])]
    cmd += [_dest(conn), "sh -s"]
    run(cmd, dry=dry, input_text=script)


def scp(conn, files, remote_dir, dry=False):
    cmd = ["scp"]
    if conn.get("key"):
        cmd += ["-i", conn["key"]]
    if conn.get("port"):
        cmd += ["-P", str(conn["port"])]
    cmd += [str(item) for item in files]
    cmd.append("%s:%s" % (_dest(conn), remote_dir))
    run(cmd, dry=dry)


def run_tests(target, dry=False):
    if target in ("client", "all"):
        run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
            cwd=AGENT_DIR,
            dry=dry,
        )
    if target in ("server", "all"):
        run([shutil.which("uv") or "uv", "run", "pytest", "-q"], cwd=SERVER_DIR, dry=dry)


def cleanup_suffix(paths, keep_remote):
    if keep_remote:
        return ""
    return "rm -f %s\n" % " ".join(paths)


def deploy_client(args):
    if not args.no_build:
        npm_run("build:client-ui", dry=args.dry_run)
    pack(AGENT_DIR / "vigil_agent", AGENT_PACKAGE, dry=args.dry_run)
    pack(CLIENT_UI_DIST, CLIENT_UI_PACKAGE, dry=args.dry_run)

    conn = client_conn()
    scp(conn, [AGENT_PACKAGE, CLIENT_UI_PACKAGE], "/tmp/", dry=args.dry_run)
    script = CLIENT_SCRIPT + cleanup_suffix(
        ["/tmp/vigil-agent.tar.gz", "/tmp/vigil-client-ui.tar.gz"], args.keep_remote
    )
    ssh(conn, script, dry=args.dry_run)


def deploy_server(args):
    if not args.no_build:
        npm_run("build:server-ui", dry=args.dry_run)
    pack(SERVER_DIR / "app", SERVER_APP_PACKAGE, dry=args.dry_run)
    pack(SERVER_UI_DIST, SERVER_UI_PACKAGE, dry=args.dry_run)

    conn = server_conn()
    scp(conn, [SERVER_APP_PACKAGE, SERVER_UI_PACKAGE], "/tmp/", dry=args.dry_run)
    script = SERVER_SCRIPT + cleanup_suffix(
        ["/tmp/vigil-server-app.tar.gz", "/tmp/vigil-server-ui.tar.gz"], args.keep_remote
    )
    ssh(conn, script, dry=args.dry_run)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Vigil 一键部署（构建、上传、替换、重启、健康检查）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("target", nargs="?", default="all", choices=["client", "server", "all"])
    parser.add_argument("--no-tests", action="store_true", help="跳过单元测试")
    parser.add_argument("--no-build", action="store_true", help="跳过前端构建")
    parser.add_argument("--dry-run", action="store_true", help="只打印命令，不执行")
    parser.add_argument("--keep-remote", action="store_true", help="保留远端临时部署包")
    args = parser.parse_args(argv)

    try:
        if not args.no_tests:
            log("运行测试（%s）" % args.target)
            run_tests(args.target, dry=args.dry_run)
        if args.target in ("client", "all"):
            log("部署客户端")
            deploy_client(args)
        if args.target in ("server", "all"):
            log("部署服务端")
            deploy_server(args)
    except DeployError as exc:
        log("失败：%s" % exc)
        return 1
    log("全部完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
