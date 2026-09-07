"""业务数据层：统一数据出口。

数据来源：
- data_generated.py  — 程序生成的人员/部门/预算/客户/合同（见 tools/generate_data.py）
- documents.py       — 15 篇长文规章制度文档

替换为真实数据库时，只需让本模块保持同样的导出变量名即可。
"""
from data_generated import (  # noqa: F401
    DEPARTMENTS,
    EMPLOYEES,
    BUDGETS,
    BG_BUDGETS,
    CUSTOMERS,
    CONTRACTS,
)
from documents import DOCUMENTS  # noqa: F401


def list_groups() -> list:
    """按事业群汇总部门。"""
    groups = {}
    for d in DEPARTMENTS:
        groups.setdefault(d["bg"], []).append(d["name"])
    return groups