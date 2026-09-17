"""安全审计日志：记录每次工具调用（谁/何时/何工具/参数/结果摘要/密级命中）。

- 写入路径：/tmp/audit.jsonl（可用 AUDIT_FILE 环境变量覆盖）
- 每条：{ts, user, department, role, tool, args, result_summary, sensitive}
- 不写完整观测正文（体积与隐私兼得），只保留 ≤200 字的敏感摘要
- 写失败静默（审计日志绝不影响主流程）
"""
import json
import os
import time

AUDIT_FILE = os.environ.get("AUDIT_FILE", "/tmp/audit.jsonl")

# 涉及敏感/机密数据访问的工具：这些操作的结果摘要含密级提示
_SENSITIVE_TOOLS = {"get_customer_info", "list_customers", "query_contract", "lookup_employee"}


def _summary(obs: str, max_chars: int = 200) -> str:
    s = (obs or "").strip().replace("\n", " ")
    return s[:max_chars]


def _ctx_user(ctx: dict | None) -> dict:
    """把 resolved_context 扁平结构映射成审计习惯的 {name, department, role}。"""
    return {
        "name": (ctx or {}).get("name") or "",
        "department": (ctx or {}).get("user_department") or (ctx or {}).get("department") or "",
        "user_role": (ctx or {}).get("user_role") or "",
    }


def audit_tool_call(user: dict | None, tool: str, args: dict, obs: str) -> None:
    """记录一次工具调用。user 可为 None（未识别身份时记 unknown）。"""
    u = _ctx_user(user)
    rec = {
        "ts": time.time(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "user": u["name"] or "unknown",
        "department": u["department"],
        "role": u["user_role"],
        "tool": tool,
        "args": {k: str(v)[:80] for k, v in (args or {}).items()},
        "sensitive": tool in _SENSITIVE_TOOLS,
        "shared": False,
        "result": _summary(obs),
    }
    try:
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def audit_share(user: dict | None, tool: str, args: dict, obs: str, reason: str = "") -> None:
    """记录一次对外共享/导出类动作（当前系统无此能力，预留扩展位）。"""
    if tool not in _SENSITIVE_TOOLS:
        return
    rec = {
        "ts": time.time(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "user": (user or {}).get("name") or "unknown",
        "department": (user or {}).get("department") or "",
        "role": (user or {}).get("user_role") or "",
        "tool": tool,
        "args": {k: str(v)[:80] for k, v in (args or {}).items()},
        "sensitive": True,
        "shared": True,
        "reason": reason,
        "result": _summary(obs),
    }
    try:
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_audit(limit: int = 100) -> list[dict]:
    """读取最近审计记录（供监控/分析，不对外暴露）。"""
    if not os.path.exists(AUDIT_FILE):
        return []
    try:
        with open(AUDIT_FILE, encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
    except (OSError, json.JSONDecodeError):
        return []
    return lines[-limit:]