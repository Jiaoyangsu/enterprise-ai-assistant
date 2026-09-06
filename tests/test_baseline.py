"""20 条业务查询基线测试（回归验证，对齐新工具/新数据）"""
import sys
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp_servers"))


def load_server(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.notes = []

    def check(self, name, ok, detail=""):
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        self.notes.append(f"{'✅' if ok else '❌'} {name} {detail}")


def main():
    t = TestResults()
    ops = load_server("ops", ROOT / "mcp_servers/ops_server.py")
    docs = load_server("docs", ROOT / "mcp_servers/docs_server.py")
    sec = load_server("sec", ROOT / "mcp_servers/security_server.py")

    # ---- 员工信息 ----
    r = ops.lookup_employee("陈志强")
    t.check("员工-陈志强部门", r["found"] and r["department"] == "技术部", str(r))

    r = ops.lookup_employee("不存在的人")
    t.check("员工-未找到处理", not r["found"])

    # ---- 年假 ----
    r = ops.lookup_employee("刘洋")
    t.check("年假-刘洋余额", r["leave_balance"] == 12, str(r))

    # ---- 部门/预算 ----
    r = ops.list_departments()
    t.check("部门列表", r["count"] == 8, str(r["count"]))

    r = ops.query_budget("技术部")
    t.check("预算-技术部", r["annual_budget"] == 500, str(r))

    r = ops.query_budget("不存在部门")
    t.check("预算-未找到", not r["found"])

    # ---- 客户(RBAC) ----
    r = ops.get_customer_info("华宇科技")
    t.check("客户-无权限拦截", r["access"] == "denied")

    r = ops.get_customer_info("华宇科技", user_role="manager")
    t.check("客户-有权限", r.get("found") and r["level"] == "VIP", str(r))

    # ---- 合同 ----
    r = ops.query_contract(contract_id="HT-2024-001")
    t.check("合同-状态", r["found"] and r["status"] == "已签署", str(r))

    # ---- 知识库(RBAC) ----
    r = docs.search_knowledge_base("请假流程", is_authenticated=True)
    t.check("知识库-内部检索", r["found"], r["message"])

    r = docs.search_knowledge_base("研发预算", is_authenticated=False)
    t.check("知识库-机密未登录拦截", any(d["title"] == "技术部年度研发预算细则" for d in r["denied"]))

    r = docs.search_knowledge_base("研发预算", user_department="技术部", is_authenticated=True)
    t.check("知识库-机密本部门可见", any(x["title"] == "技术部年度研发预算细则" for x in r["results"]))

    r = docs.search_knowledge_base("研发预算", user_department="财务部", is_authenticated=True)
    t.check("知识库-机密异部门拦截", any(x["title"] == "技术部年度研发预算细则" for x in r["results"]) is False)

    # ---- 脱敏 ----
    r = sec.redact_pii("手机号 13812345678，身份证 110101199001011234")
    t.check("脱敏-正文", r["count"] == 2, r["redacted"])

    r = sec.redact_pii("13812345678")
    t.check("脱敏-手机号格式", r["redacted"] == "138****5678", r["redacted"])

    # ---- 风险审查 ----
    r = sec.risk_review_text("讨论竞品和薪资倒挂问题")
    t.check("风险-命中敏感词", not r["safe"] and "竞品" in r["hits"])

    r = sec.risk_review_text("今天天气不错")
    t.check("风险-安全文本", r["safe"])

    # ---- 存储清洗 ----
    r = sec.sanitize_for_storage("联系 13911112222 关于竞品")
    t.check("存储清洗-PII去+风险标", r["pii_removed"] == 1 and not r["ready_for_storage"])

    # ---- 写入操作 ----
    r = ops.create_leave_request("黄国栋", "2024-05-06", "2024-05-07")
    t.check("请假-提交成功", r["success"], r["message"])

    r = ops.create_leave_request("张伟", "2024-05-06", "2024-05-20")  # 超余额
    t.check("请假-超额拒绝", not r["success"])

    r = ops.create_ticket("王强", "线上服务异常", priority="紧急")
    t.check("工单-创建成功", r["success"] and r["ticket_id"].startswith("TK-"))

    print(f"\n结果：{t.passed}/{t.passed + t.failed} 通过，{t.failed} 失败")
    for n in t.notes:
        print(f"  {n}")
    sys.exit(1 if t.failed else 0)


if __name__ == "__main__":
    main()