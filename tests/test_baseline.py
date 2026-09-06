"""20 条业务查询基线测试（验证工具逻辑，非端到端）"""
import json
import subprocess
import sys
import importlib.util


def load_server(path):
    """加载 server 模块而不启动网络"""
    spec = importlib.util.spec_from_file_location("server", path)
    mod = importlib.util.module_from_spec(spec)
    # 防止 __main__ 块误触发
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
    ops = load_server("mcp_servers/ops_server.py")
    docs = load_server("mcp_servers/docs_server.py")
    sec = load_server("mcp_servers/security_server.py")

    # ---- 员工信息 ----
    r = ops.lookup_employee("张三")
    t.check("员工-张三部门", r["found"] and r["department"] == "技术部", str(r))

    r = ops.lookup_employee("不存在的人")
    t.check("员工-未找到处理", not r["found"])

    # ---- 年假 ----
    r = ops.get_leave_balance("李四")
    t.check("年假-李四余额", r["leave_balance"] == 12, str(r))

    # ---- 部门/预算 ----
    r = ops.list_departments()
    t.check("部门列表", "技术部" in r)

    r = ops.query_budget("技术部")
    t.check("预算-技术部", r["budget"] == 500, str(r))

    r = ops.query_budget("不存在部门")
    t.check("预算-未找到", not r["found"])

    # ---- 客户(RBAC) ----
    r = ops.get_customer_info("甲公司")  # 无角色
    t.check("客户-无权限拦截", r["access"] == "denied")

    r = ops.get_customer_info("甲公司", user_role="manager")
    t.check("客户-有权限", r.get("found") and r["level"] == "VIP客户", str(r))

    # ---- 合同 ----
    r = ops.query_contract_status("CT-2023-001")
    t.check("合同-状态", r["found"] and r["status"] == "已审批")

    # ---- 知识库(RBAC) ----
    r = docs.search_knowledge_base("请假流程", is_authenticated=True)
    t.check("知识库-公开检索", r["found"], r["message"])

    r = docs.search_knowledge_base("研发预算", is_authenticated=False)
    t.check("知识库-机密未登录拦截", any(d["title"] == "技术部年度研发预算" for d in r["denied"]))

    r = docs.search_knowledge_base("研发预算", user_department="技术部", is_authenticated=True)
    t.check("知识库-机密本部门可见", any(x["title"] == "技术部年度研发预算" for x in r["results"]))

    r = docs.search_knowledge_base("研发预算", user_department="财务部", is_authenticated=True)
    t.check("知识库-机密异部门拦截", any(x["title"] == "技术部年度研发预算" for x in r["results"]) is False)

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
    r = ops.create_leave_request("张三", "2024-01-08", "2024-01-09")
    t.check("请假-提交成功", r["success"], r["message"])

    r = ops.create_leave_request("张三", "2024-01-08", "2024-01-20")  # 超余额
    t.check("请假-超额拒绝", not r["success"])

    r = ops.create_ticket("王五", "API 服务异常", priority="紧急")
    t.check("工单-创建成功", r["success"] and r["ticket_id"].startswith("TK-"))

    print(f"\n结果：{t.passed}/20 通过，{t.failed}/20 失败")
    for n in t.notes:
        print(f"  {n}")
    sys.exit(0 if t.failed == 0 else 1)


if __name__ == "__main__":
    main()
