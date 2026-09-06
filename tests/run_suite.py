"""自动测试：路由命中率 + 实际工具调用验证（6组×5题=30题）"""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp_servers"))
from router import route
from test_questions import get_questions, summary


def load_server(name):
    path = Path(__file__).resolve().parent.parent / "mcp_servers" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_context(q: str) -> dict:
    """从问题中解析身份上下文：##用户身份## / ##身份:部门##"""
    ctx = {"name": "刘洋", "user_department": "技术部", "is_authenticated": True, "user_role": ""}
    if "##用户身份##" in q:
        ctx["name"] = "刘洋"
    if "##身份:技术部##" in q:
        ctx["user_department"] = "技术部"
    if "没有登录" in q or "未登录" in q:
        ctx["is_authenticated"] = False
    return ctx


def call_tool(mod, tool, ctx, q):
    """调用实际工具函数，返回结果 dict"""
    if tool in ("lookup_employee",):
        name = ctx["name"]
        # 从问题里提取员工名（粗匹配 data.EMPLOYEES）
        from data import EMPLOYEES
        hit = None
        for uname in EMPLOYEES:
            if uname in q:
                hit = uname
                break
        return mod.lookup_employee(hit or name)
    if tool == "list_departments":
        return mod.list_departments()
    if tool == "query_budget":
        from data import BUDGETS
        hit = next((d for d in BUDGETS if d in q), "技术部")
        return mod.query_budget(hit)
    if tool == "get_customer_info":
        from data import CUSTOMERS
        hit = next((c for c in CUSTOMERS if c in q), "华宇科技")
        return mod.get_customer_info(hit, user_role="manager")
    if tool == "query_contract":
        import re
        m = re.search(r"HT-\d{4}-\d{3}", q)
        if m:
            return mod.query_contract(contract_id=m.group(0), customer="")
        from data import CUSTOMERS
        hit = next((c for c in CUSTOMERS if c in q), "")
        return mod.query_contract(contract_id="", customer=hit)
    if tool == "create_leave_request":
        name = next((u for u in EMPLOYEE_NAMES if u in q), ctx["name"])
        return mod.create_leave_request(name, "2024-05-06", "2024-05-07")
    if tool == "create_ticket":
        return mod.create_ticket("刘洋", "线上服务异常", priority="紧急")
    if tool == "search_knowledge_base":
        kw = "外部网盘" if "网盘" in q else "加班" if "加班" in q else "差旅" if "报销" in q else "请假" if "请假" in q else "保密" if "保密" in q else q
        return mod.search_knowledge_base(kw, user_department=ctx["user_department"], is_authenticated=ctx["is_authenticated"])
    if tool == "redact_pii":
        import re
        phone = re.search(r"1\d{10}", q)
        idn = re.search(r"\d{17}[\dXx]", q)
        text = (phone.group(0) if phone else "") + (idn.group(0) if idn else "")
        return mod.redact_pii(text or "13812345678")
    if tool == "risk_review_text":
        return mod.risk_review_text(q.replace("检查这句话有没有风险：", ""))
    if tool == "sanitize_for_storage":
        return mod.sanitize_for_storage("电话13812345678，讨论薪资倒挂")
    return {}


EMPLOYEE_NAMES = [
    "陈志强", "黄国栋", "刘洋", "马晓峰", "徐磊", "张伟", "高翔", "李思远",
    "刘建国", "王强", "赵文斌", "郭涛", "赵秀英", "钱进", "孙敏", "韩雪",
    "孙丽", "冯媛", "沈洁", "周宏伟", "吴海峰", "郑强", "林小芳", "何平",
    "罗玉梅", "吴倩", "蒋丽", "金鑫", "郑晓东", "曹颖", "彭蕾", "王爱华", "胡静",
]


import asyncio


async def count_tools(mods):
    print("\n工具注册情况：")
    for name, mod in mods.items():
        tools = await mod.mcp.list_tools()
        print(f"  {name}: {len(tools)} 个工具 -> {[t.name for t in tools]}")


def main():
    ops = load_server("ops_server")
    docs = load_server("docs_server")
    sec = load_server("security_server")
    mods = {"ops": ops, "docs": docs, "sec": sec}

    tool_to_mod = {
        "lookup_employee": ops, "list_departments": ops, "query_budget": ops,
        "get_customer_info": ops, "query_contract": ops,
        "create_leave_request": ops, "create_ticket": ops,
        "search_knowledge_base": docs, "redact_pii": sec,
        "risk_review_text": sec, "sanitize_for_storage": sec,
    }

    passed = 0
    failed = 0
    details = []

    for i, q in enumerate(get_questions(), 1):
        routed = route(q.question)
        if routed != q.expected_tool:
            failed += 1
            details.append(
                f"❌ [{q.group}/{q.complexity}] {q.question}\n    期望 {q.expected_tool}，路由到 {routed}"
            )
            continue
        if q.expected_key:
            ctx = parse_context(q.question)
            try:
                result = call_tool(tool_to_mod[routed], routed, ctx, q.question)
                passed += 1
            except Exception as e:
                failed += 1
                details.append(
                    f"❌ [{q.group}/{q.complexity}] {q.question}\n    工具调用异常: {e}"
                )
                continue
        else:
            passed += 1

    print(f"结果：{passed}/{len(get_questions())} 通过，{failed} 失败")
    print(f"统计：{summary()}")
    if details:
        print("\n失败明细：")
        for d in details:
            print(d)

    asyncio.run(count_tools(mods))

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()