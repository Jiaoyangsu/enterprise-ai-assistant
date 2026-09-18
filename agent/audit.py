"""安全审计日志：记录每次工具调用（谁/何时/何工具/参数/结果摘要/密级命中）。

- 写入路径：`AUDIT_FILE` 环境变量（默认 `/tmp/audit.jsonl`）。生产请指向**受管目录**
  （如 `/var/log/kbai/audit.jsonl`），由服务账号独占可写。
- 每条：{ts, time, user, department, role, tool, args, sensitive, shared, result}
- 不写完整观测正文（体积与隐私兼得），只保留 ≤200 字的敏感摘要
- **日志脱敏**：args 与 result 写入前统一走 security_server.redact_pii（手机/身份证/
  邮箱/银行卡/地址/内部人名），避免用户把待脱敏的 PII 作为参数传入时反被明文落盘
- **文件权限**：新建即 `0600`（仅服务账号可读写）；每次写入后校正，防止被外部改权限
- **轮转与保留**：单文件超过 `AUDIT_MAX_BYTES`（默认 5 MiB）即轮转为 `.1`，保留
  `AUDIT_BACKUPS`（默认 3）份，最旧的被删除（保留期由份数 × 单文件上限决定）
- 写失败静默（审计日志绝不影响主流程）
"""
import json
import os
import sys
import time

AUDIT_FILE = os.environ.get("AUDIT_FILE", "/tmp/audit.jsonl")

# 脱敏复用 MCP security_server 的单一 PII 实现（含邮箱/银行卡/地址/内部人名，比 injection.mask_pii 更全）
_MCP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp_servers")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)
try:
    from security_server import redact_pii as _redact_pii  # noqa: E402
except Exception:  # pragma: no cover - 无 fastmcp 时降级为仅截断
    _redact_pii = None

# 涉及敏感/机密数据访问的工具：这些操作的结果摘要含密级提示
_SENSITIVE_TOOLS = {"get_customer_info", "list_customers", "query_contract", "lookup_employee"}

_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_DEFAULT_BACKUPS = 3


def _audit_file() -> str:
    """运行时解析，便于生产切受管目录/测试注入（改写环境变量即生效）。"""
    return os.environ.get("AUDIT_FILE") or AUDIT_FILE


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
        return value if value >= 0 else default
    except ValueError:
        return default


def _scrub(text: str) -> str:
    """PII 脱敏（失败即原样返回，审计绝不因脱敏异常而中断）。"""
    if not text or _redact_pii is None:
        return text
    try:
        return _redact_pii(text)["redacted"]
    except Exception:  # pragma: no cover
        return text


def _summary(obs: str, max_chars: int = 200) -> str:
    s = _scrub((obs or "").strip().replace("\n", " "))
    return s[:max_chars]


def _ctx_user(ctx: dict | None) -> dict:
    """把 resolved_context 扁平结构映射成审计习惯的 {name, department, role}。"""
    return {
        "name": (ctx or {}).get("name") or "",
        "department": (ctx or {}).get("user_department") or (ctx or {}).get("department") or "",
        "user_role": (ctx or {}).get("user_role") or "",
    }


def _rotate(path: str, max_bytes: int, backups: int) -> None:
    """按大小轮转：path -> path.1 -> path.2 …，超出 backups 份的最旧文件删除。"""
    if backups <= 0 or max_bytes <= 0:
        return
    try:
        if os.path.getsize(path) < max_bytes:
            return
    except OSError:
        return
    for i in range(backups - 1, 0, -1):
        src, dst = f"{path}.{i}", f"{path}.{i + 1}"
        if os.path.exists(src):
            try:
                os.replace(src, dst)
            except OSError:
                pass
    if os.path.exists(path):
        try:
            os.replace(path, f"{path}.1")
        except OSError:
            pass


def _write(rec: dict) -> None:
    """追加一条审计记录：控制权限 + 轮转；任何异常都静默（不影响主流程）。"""
    path = _audit_file()
    if not path:
        return
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        _rotate(path, _int_env("AUDIT_MAX_BYTES", _DEFAULT_MAX_BYTES),
                _int_env("AUDIT_BACKUPS", _DEFAULT_BACKUPS))
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        pass


def audit_tool_call(user: dict | None, tool: str, args: dict, obs: str) -> None:
    """记录一次工具调用。user 可为 None（未识别身份时记 unknown）。"""
    u = _ctx_user(user)
    _write({
        "ts": time.time(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "user": u["name"] or "unknown",
        "department": u["department"],
        "role": u["user_role"],
        "tool": tool,
        "args": {k: _scrub(str(v))[:80] for k, v in (args or {}).items()},
        "sensitive": tool in _SENSITIVE_TOOLS,
        "shared": False,
        "result": _summary(obs),
    })


def audit_share(user: dict | None, tool: str, args: dict, obs: str, reason: str = "") -> None:
    """记录一次对外共享/导出类动作（当前系统无此能力，预留扩展位）。"""
    if tool not in _SENSITIVE_TOOLS:
        return
    _write({
        "ts": time.time(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "user": (user or {}).get("name") or "unknown",
        "department": (user or {}).get("department") or "",
        "role": (user or {}).get("user_role") or "",
        "tool": tool,
        "args": {k: _scrub(str(v))[:80] for k, v in (args or {}).items()},
        "sensitive": True,
        "shared": True,
        "reason": reason,
        "result": _summary(obs),
    })


def read_audit(limit: int = 100) -> list[dict]:
    """读取最近审计记录（供监控/分析，不对外暴露）。"""
    path = _audit_file()
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
    except (OSError, json.JSONDecodeError):
        return []
    return lines[-limit:]
