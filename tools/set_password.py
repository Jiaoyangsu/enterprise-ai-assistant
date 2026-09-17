#!/usr/bin/env python3
"""账号口令管理：新增/更新/移除命名账号（PBKDF2 哈希写入账号表）。

用法:
  .venv/bin/python tools/set_password.py 陈志强              # 交互式输入口令
  .venv/bin/python tools/set_password.py 张伟 'Str0ng#Pass'  # 直接给口令
  .venv/bin/python tools/set_password.py --list             # 列出已登记账号
  .venv/bin/python tools/set_password.py --remove 张伟       # 移除账号

说明:
  - 生产环境优先用 `AUTH_USERS` 环境变量(JSON) 注入，不落盘、不提交仓库；
    本脚本用于本地/初始化。
  - 账号表路径可用 AUTH_FILE 覆盖（默认 data/auth_users.json）。
  - 口令最少 8 位，禁止使用弱口令。
"""
import getpass
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
import secure_auth  # noqa: E402

AUTH_FILE = os.environ.get("AUTH_FILE", os.path.join(ROOT, "data", "auth_users.json"))
WEAK = {"123456", "password", "admin", "12345678", "000000", "111111"}


def _load() -> dict:
    try:
        with open(AUTH_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(AUTH_FILE), exist_ok=True)
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] == "--list":
        store = _load()
        if not store:
            print(f"账号表为空：{AUTH_FILE}")
        for name, val in store.items():
            kind = "hashed" if str(val).startswith("pbkdf2$") else "PLAINTEXT(待升级)"
            print(f"  {name}: {kind}")
        return
    if args[0] == "--remove":
        if len(args) < 2:
            print("用法: --remove <姓名>")
            return
        store = _load()
        store.pop(args[1], None)
        _save(store)
        print(f"已移除账号：{args[1]}")
        return

    name = args[0]
    pwd = args[1] if len(args) > 1 else getpass.getpass(f"为 {name} 设置口令: ")
    if len(pwd) < 8:
        print("口令至少 8 位，已拒绝。")
        return
    if pwd in WEAK:
        print("检测到弱口令，已拒绝。")
        return
    store = _load()
    store[name] = secure_auth.make_entry(pwd)
    _save(store)
    print(f"已设置 {name} 的口令（PBKDF2 哈希 -> {AUTH_FILE}）")


if __name__ == "__main__":
    main()
