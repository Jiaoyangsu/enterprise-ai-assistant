"""认证与口令管理（安全加固）。

设计原则：
- **fail-closed**：只有登记在册的账号能登录；无 `*` 通配、无隐式默认口令。
- 口令哈希：PBKDF2-HMAC-SHA256（120k 迭代），格式 `pbkdf2$<salt>$<hash>`；
  兼容旧明文条目，命中后由调用方就地升级为哈希（迁移期）。
- 账号来源优先级：`AUTH_USERS` 环境变量(JSON) > `AUTH_FILE`（data/auth_users.json）。
  生产用环境变量/密钥管理注入，口令不落库、不提交仓库。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets

ITERATIONS = 120_000
_ALGO = "sha256"


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """PBKDF2-HMAC-SHA256 哈希。返回 (salt, hash)。"""
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(_ALGO, str(password).encode(), salt.encode(), ITERATIONS)
    return salt, dk.hex()


def verify_password(password: str, stored: str | None) -> bool:
    """常量时间校验口令。stored 为空/None 一律拒绝（fail-closed）。"""
    if not stored:
        return False
    if str(stored).startswith("pbkdf2$"):
        try:
            _, salt, expect = str(stored).split("$", 2)
        except ValueError:
            return False
        return hmac.compare_digest(hash_password(password, salt)[1], expect)
    return hmac.compare_digest(str(password), str(stored))  # 迁移期明文兼容


def make_entry(password: str) -> str:
    """生成可写入账号表的哈希条目。"""
    salt, h = hash_password(password)
    return f"pbkdf2${salt}${h}"


def needs_upgrade(stored: str | None) -> bool:
    """该条目是否需要从明文升级为哈希。"""
    return bool(stored) and not str(stored).startswith("pbkdf2$")


def load_store(path: str) -> dict:
    """加载账号表。`AUTH_USERS` 环境变量优先于文件。"""
    env = os.environ.get("AUTH_USERS")
    if env:
        try:
            data = json.loads(env)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def authenticate(name: str, password: str, employees: dict, store: dict) -> dict | None:
    """校验姓名+口令。姓名必须在员工目录且口令匹配，否则返回 None。"""
    emp = employees.get(name)
    if not emp:
        return None
    stored = store.get(name)
    if not stored:
        return None  # fail-closed：未登记账号拒绝
    if not verify_password(password, stored):
        return None
    return emp
