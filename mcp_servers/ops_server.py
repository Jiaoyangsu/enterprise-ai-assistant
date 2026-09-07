"""Ops Server - 员工/部门/预算/请假/工单业务逻辑（端口 8002）

P3 优化：
- 工具精简：7 -> 6（合并 lookup_employee 与 get_leave_balance 为 lookup_employee）
- description 加场景示例，避免工具混淆
- 从 data.py 读真实业务数据
"""
from fastmcp import FastMCP

from data import (
    DEPARTMENTS,
    EMPLOYEES,
    BUDGETS,
    CUSTOMERS,
    CONTRACTS,
)

mcp = FastMCP("ops")


@mcp.tool()
def lookup_employee(name: str) -> dict:
    """查询员工信息：部门、职位、职级、年假余额、入职日期。
    场景示例：'张三在哪个部门？' '我年假还剩几天？' '陈志强什么时候入职的？'
    —— 这是年假余额查询入口，不是考勤/打卡。"""
    emp = EMPLOYEES.get(name)
    if not emp:
        return {"found": False, "message": f"未找到员工：{name}"}
    return {
        "found": True,
        "name": name,
        "department": emp["department"],
        "position": emp["position"],
        "level": emp["level"],
        "leave_balance": emp["leave_balance"],
        "hire_date": emp["hire_date"],
        "unit": "天",
    }


@mcp.tool()
def query_budget(department: str) -> dict:
    """查询部门年度预算（万元）、已用、剩余。只读操作。
    场景示例：'技术部预算多少？' '技术部预算花了多少？' '还有多少预算可用？'"""
    bud = BUDGETS.get(department)
    if not bud:
        return {"found": False, "message": f"未找到部门：{department}"}
    return {
        "found": True,
        "department": department,
        "annual_budget": bud["annual"],
        "spent": bud["spent"],
        "remaining": bud["remaining"],
        "unit": "万元",
        "items": bud["items"],
    }


@mcp.tool()
def list_departments() -> dict:
    """列出全部部门、部门负责人、人数。只读操作。
    场景示例：'公司有哪些部门？' '技术部经理是谁？'"""
    return {
        "found": True,
        "count": len(DEPARTMENTS),
        "departments": [
            {"name": d["name"], "manager": d["manager"], "headcount": d["headcount"]}
            for d in DEPARTMENTS
        ],
    }


@mcp.tool()
def get_customer_info(customer_name: str, user_role: str = "") -> dict:
    """查询客户信息（行业、联系人、等级、合同额、信用）。需 RBAC：仅管理层(admin/manager)可查看。
    场景示例：'华宇科技是什么客户？' '天穹金融的信用情况？'"""
    if user_role not in ("admin", "manager"):
        return {"access": "denied", "message": "无权访问客户信息，需要管理层权限"}
    info = CUSTOMERS.get(customer_name)
    if not info:
        return {"found": False, "message": f"未找到客户：{customer_name}"}
    return {"found": True, "customer": customer_name, **info}


@mcp.tool()
def query_contract(contract_id: str = "", customer: str = "") -> dict:
    """查询合同状态（审批进度/金额/负责人）。只读操作。支持按合同号或客户名查询。
    场景示例：'HT-2024-002审批到哪了？' '华宇科技的合同是什么状态？'"""
    if not contract_id and not customer:
        return {"found": False, "message": "请输入合同号或客户名称"}

    contracts = [
        {"contract_id": cid, **c} for cid, c in CONTRACTS.items()
    ] if not contract_id else []
    if contract_id:
        c = CONTRACTS.get(contract_id)
        return {
            "found": bool(c),
            "contract_id": contract_id,
            **(c or {"message": f"未找到合同：{contract_id}"}),
        }

    results = [c for c in contracts if c["customer"] == customer]
    if not results:
        return {"found": False, "message": f"未找到客户 {customer} 的合同"}
    return {"found": True, "customer": customer, "contracts": results}


@mcp.tool()
def create_leave_request(
    name: str,
    start_date: str,
    end_date: str,
    leave_type: str = "年假",
    reason: str = "",
) -> dict:
    """写入操作：提交请假申请。必须用户明确表达'申请/提交请假'意图才执行。
    参数：name(姓名) start_date(YYYY-MM-DD) end_date(YYYY-MM-DD) leave_type(年假/事假/病假) reason(原因)。
    场景示例：'帮我提交3月5日到3月6日的年假申请'"""
    emp = EMPLOYEES.get(name)
    if not emp:
        return {"success": False, "message": f"未找到员工：{name}，无法提交申请"}
    days = _calc_days(start_date, end_date)
    if days <= 0:
        return {"success": False, "message": "结束日期必须晚于开始日期"}
    if days > emp["leave_balance"]:
        return {
            "success": False,
            "message": f"请假 {days} 天超过剩余年假 {emp['leave_balance']} 天，可用 事假/病假 或缩短假期",
        }
    emp["leave_balance"] -= days
    return {
        "success": True,
        "ticket_id": f"LV-{start_date}-{name}",
        "message": f"请假申请已提交：{name} {start_date}~{end_date} 共 {days} 天（{leave_type}），剩余年假 {emp['leave_balance']} 天",
    }


@mcp.tool()
def create_ticket(
    requester: str,
    title: str,
    description: str = "",
    priority: str = "普通",
    category: str = "通用",
) -> dict:
    """写入操作：创建工单。必须用户明确说'创建/开单/报障'才执行。
    参数：requester(申请人) title(标题) description(描述) priority(紧急/高/普通) category(IT/HR/行政/通用)。
    场景示例：'给运维开个紧急工单：线上服务挂了'"""
    tickets = (
        "IT故障" if "运维" in title or "服务" in title or "系统" in title
        else "IT故障" if category == "IT"
        else category
    )
    return {
        "success": True,
        "ticket_id": f"TK-{_gen_seq()}",
        "message": f"工单已创建：{title}（优先级：{priority}，类别：{tickets}），申请人：{requester}",
    }


def _calc_days(start: str, end: str) -> int:
    from datetime import date

    try:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
    except ValueError:
        return -1
    return (e - s).days + 1


_seq = 1000


def _gen_seq() -> str:
    global _seq
    _seq += 1
    return f"{_seq:04d}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http", port=8002)