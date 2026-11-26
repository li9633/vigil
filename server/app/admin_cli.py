"""管理员密码命令行：忘记密码或密钥丢失时使用。

用法（生产环境通过 /usr/local/bin/vigil-admin 以 root 执行）：
    vigil-admin reset-password
    vigil-admin reset-password --password '新密码'
    vigil-admin reset-key --password '新密码'
    vigil-admin status
"""

from __future__ import annotations

import argparse
import sys

from app.db import init_db, init_engine, session_scope
from app.settings import Settings
from app.system_settings import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    AdminKeyError,
    admin_auth_state,
    load_admin_auth,
    load_pepper,
    new_password,
    remove_initial_password,
    rotate_pepper,
    set_admin_password,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vigil-admin", description="Vigil 管理员密码与密钥维护"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("reset-password", "重置管理员密码（保留现有密钥）"),
        ("reset-key", "重置密钥并同时重置密码（旧密码会失效）"),
    ):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--password", help="新密码；省略则随机生成并打印一次")
    sub.add_parser("status", help="查看密码记录与密钥文件状态")
    return parser


def _validate(password: str) -> str | None:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        return "密码长度需在 %d-%d 位之间" % (MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH)
    return None


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = Settings.from_env()
    init_engine(settings.database_url)
    init_db()

    if args.command == "status":
        with session_scope() as db:
            state = admin_auth_state(db, settings.admin_key_file)
        print("更新时间：%s" % (state["updated_at"] or "未初始化"))
        print("密钥文件：%s" % settings.admin_key_file)
        key_ok = state.get("key_ok")
        if key_ok is True:
            print("密钥状态：正常")
        elif key_ok is False:
            print("密钥状态：不匹配或缺失（请执行 reset-key）")
        else:
            print("密钥状态：旧记录未绑定密钥")
        return 0

    generated = args.password is None
    password = args.password or new_password()
    error = _validate(password)
    if error:
        print(error, file=sys.stderr)
        return 2

    try:
        with session_scope() as db:
            if args.command == "reset-key":
                pepper = rotate_pepper(settings.admin_key_file)
            elif load_admin_auth(db) is None:
                pepper = load_pepper(settings.admin_key_file, create=True)
            else:
                pepper = load_pepper(settings.admin_key_file)
            set_admin_password(db, password, pepper)
            remove_initial_password(settings.admin_key_file)
    except AdminKeyError as exc:
        print(str(exc), file=sys.stderr)
        if args.command == "reset-password":
            print("密钥文件不可用，请改用：vigil-admin reset-key", file=sys.stderr)
        return 1

    if args.command == "reset-key":
        print("管理员密钥与密码已重置，旧密码已失效。")
    else:
        print("管理员密码已重置（数据库）。")
    if generated:
        print("新密码：%s" % password)
        print("请立即登录并妥善保存；该密码不会再次显示。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
