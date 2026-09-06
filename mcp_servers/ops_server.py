"""Ops Server - 员工/部门/预算/请假/工单业务逻辑（端口 8002）"""
from fastmcp import FastMCP, Context

mcp = FastMCP("ops")

# 内存数据（演示用，后续可接真实数据库）
EMPLOYEES = {
    "张三": {"department": "技术部", "leave_balance": 8},
    "李四": {"department": "财务部", "leave_balance": 12},
    "王五": {"department": "销售部", "leave_balance": 5},
    "赵六": {"department": "人事部", "leave_balance": 10},
}

DEPARTMENTS = ["技术部", "财务部", "销售部", "人事部", "市场部", "运维部"]

BUDGETS = {
    "技术部": 500,
    "财务部": 200,
    "销售部": 300,
    "人事部": 150,
    "市场部": 250,
    "运维部": 180,
}

CONTRACTS = {
    "CT-2023-001": {"status": "已审批", "customer": "甲公司", "amount": 120},
    "CT-2023-002": {"status": "审批中", "customer": "乙公司", "amount": 80},
    "CT-2023-003": {"status": "已审批", "customer": "丙公司", "amount": 250},
}

CUSTOMERS = {
    "甲公司": {"industry": "制造业", "contact": "张经理", "level": "VIP客户"},
    "乙公司": {"industry": "互联网", "contact": "刘经理", "level": "普通客户"},
    "丙公司": {"industry": "金融", "contact": "陈经理", "level": "重点客户"},
}


@mcp.tool()
def lookup_employee(name: str) -> dict:
    """根据姓名查询员工信息（部门、年假余额）。支持中文姓名。例如：张三在哪个部门？"""
    emp = EMPLOYEES.get(name)
    if not emp:
        return {"found": False, "message": f"未找到员工：{name}"}
    return {
        "found": True,
        "name": name,
        "department": emp["department"],
        "leave_balance": emp["leave_balance"],
    }


@mcp.tool()
def get_leave_balance(name: str) -> dict:
    """查询指定员工剩余年假天数（天）。这是年假余额查询，不是考勤。例如：我年假还剩几天？"""
    emp = EMPLOYEES.get(name)
    if not emp:
        return {"found": False, "message": f"未找到员工：{name}"}
    return {"name": name, "leave_balance": emp["leave_balance"], "unit": "天"}


@mcp.tool()
def list_departments() -> list:
    """列出公司所有部门。只读操作。例如：公司有哪些部门？"""
    return DEPARTMENTS


@mcp.tool()
def query_budget(department: str) -> dict:
    """查询指定部门的年度预算（单位：万元）。只读操作。例如：技术部预算多少？"""
    if department not in BUDGETS:
        return {"found": False, "message": f"未找到部门：{department}"}
    return {"department": department, "budget": BUDGETS[department], "unit": "万元"}


@mcp.tool()
def get_customer_info(customer_name: str, user_role: str = "") -> dict:
    """查询客户信息（行业、联系人、等级）。需要 RBAC 权限：仅管理层可查看。"""
    if user_role not in ("admin", "manager"):
        return {
            "access": "denied",
            "message": "无权访问客户信息，需要管理层权限",
        }
    info = CUSTOMERS.get(customer_name)
    if not info:
        return {"found": False, "message": f"未找到客户：{customer_name}"}
    return {"found": True, "customer": customer_name, **info}


@mcp.tool()
def query_contract_status(contract_id: str) -> dict:
    """查询合同审批状态。只读操作。例如：CT-2023-001 审批通过了吗？"""
    contract = CONTRACTS.get(contract_id)
    if not contract:
        return {"found": False, "message": f"未找到合同：{contract_id}"}
    return {
        "found": True,
        "contract_id": contract_id,
        **contract,
    }


@mcp.tool()
def create_leave_request(
    name: str,
    start_date: str,
    end_date: str,
    leave_type: str = "年假",
    reason: str = "",
) -> dict:
    """写入操作：提交请假申请。需要用户明确表达"申请/提交请假"意图。
    参数：name 员工姓名，start_date 开始日期(YYYY-MM-DD)，end_date 结束日期，leave_type 假类型，reason 请假原因。"""
    emp = EMPLOYEES.get(name)
    if not emp:
        return {"success": False, "message": f"未找到员工：{name}，无法提交申请"}
    days_requested = _calc_days(start_date, end_date)
    if days_requested > emp["leave_balance"]:
        return {
            "success": False,
            "message": f"请假 {days_requested} 天超过剩余年假 {emp['leave_balance']} 天",
        }
    return {
        "success": True,
        "ticket_id": f"LV-{start_date}-{name}",
        "message": f"请假申请已提交：{name} {start_date}~{end_date} 共 {days_requested} 天（{leave_type}）",
    }


@mcp.tool()
def create_ticket(
    requester: str,
    title: str,
    description: str = "",
    priority: str = "普通",
) -> dict:
    """写入操作：创建工单。需要用户明确说"创建/开单"。
    参数：requester 申请人姓名，title 工单标题，description 详细描述，priority 优先级(紧急/高/普通)。"""
    return {
        "success": True,
        "ticket_id": f"TK-{_gen_seq()}",
        "message": f"工单已创建：{title}（优先级：{priority}），申请人：{requester}",
    }


def _calc_days(start: str, end: str) -> int:
    from datetime import date

    fmt = "%Y-%m-%d"
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    return max((e - s).days + 1, 1)


_seq = 0


def _gen_seq() -> str:
    global _seq
    _seq += 1
    return f"{_seq:06d}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http", port=8002)
