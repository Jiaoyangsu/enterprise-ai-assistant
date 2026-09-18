"""审计日志加固测试：受管路径、0600 权限、PII 脱敏、按大小轮转与保留份数。"""
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import audit  # noqa: E402


class R:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, name, ok, detail=""):
        self.passed += 1 if ok else 0
        self.failed += 0 if ok else 1
        print(f"  {'✅' if ok else '❌'} {name} {detail}")


def main():
    r = R()
    tmp = Path(tempfile.mkdtemp(prefix="audit_"))
    log = tmp / "sub" / "audit.jsonl"
    os.environ["AUDIT_FILE"] = str(log)
    os.environ["AUDIT_MAX_BYTES"] = "2048"
    os.environ["AUDIT_BACKUPS"] = "2"

    # ---- 写入与字段 ----
    audit.audit_tool_call(
        {"name": "陈志强", "user_department": "技术中心", "user_role": "manager"},
        "lookup_employee",
        {"name": "韩梅梅", "phone": "13812345678"},
        "员工韩梅梅，电话 13812345678，年假余额 5 天",
    )
    r.check("写入-自动建目录", log.exists())
    mode = stat.S_IMODE(log.stat().st_mode)
    r.check("权限-0600", mode == 0o600, oct(mode))
    rec = json.loads(log.read_text("utf-8").strip())
    r.check("字段-身份/工具", rec["user"] == "陈志强" and rec["tool"] == "lookup_employee")
    r.check("字段-敏感工具标记", rec["sensitive"] is True)
    r.check("脱敏-args不含手机号", "13812345678" not in rec["args"]["phone"], rec["args"]["phone"])
    r.check("脱敏-result不含手机号", "13812345678" not in rec["result"], rec["result"][:40])

    # ---- 未识别身份 ----
    audit.audit_tool_call(None, "search_knowledge_base", {"query": "年假"}, "ok")
    r.check("未识别身份-记unknown", audit.read_audit(1)[0]["user"] == "unknown")

    # ---- audit_share 仅敏感工具 ----
    audit.audit_share({"name": "A"}, "search_knowledge_base", {}, "x")
    r.check("share-非敏感不记录", all(not x.get("shared") for x in audit.read_audit(50)))
    audit.audit_share({"name": "A"}, "query_contract", {}, "合同摘要", reason="导出")
    r.check("share-敏感记录", audit.read_audit(1)[0].get("shared") is True)

    # ---- 轮转与保留 ----
    for i in range(60):
        audit.audit_tool_call({"name": "B"}, "query_budget", {"department": f"D{i}"}, f"r{i}" * 30)
    r.check("轮转-生成 .1", (tmp / "sub" / "audit.jsonl.1").exists())
    r.check("保留-不超过备份数", (tmp / "sub" / "audit.jsonl.2").exists()
            and not (tmp / "sub" / "audit.jsonl.3").exists())
    r.check("轮转-备份也0600",
            stat.S_IMODE((tmp / "sub" / "audit.jsonl.1").stat().st_mode) == 0o600)

    print(f"\n结果：{r.passed}/{r.passed + r.failed} 通过，{r.failed} 失败")
    sys.exit(1 if r.failed else 0)


if __name__ == "__main__":
    main()
