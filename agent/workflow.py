"""Workflow v1：复杂流程（OA 报销、请假提交等）的只读向导。

设计边界：系统是只读顾问，不代员工提交单子。对命中流程的问题，
注入引导指令——收集字段、调用只读工具核对（制度上限/人员部门），
输出"草稿表单 JSON + 出处引用"，并提示在 OA 完成正式提交。

实现为 react_agent 的 SYSTEM 增量，不做状态机持久化；
多轮字段补齐依赖 dsh 会话记忆（自建前端每轮独立时可依赖对话补全）。
"""
from __future__ import annotations

WORKFLOWS = [
    {
        "name": "报销申请",
        "keywords": ["报销", "报销申请", "差旅报销", "费用报销", "单据"],
        "fields": [
            {"key": "employee", "label": "申请人姓名", "optional": False},
            {"key": "department", "label": "部门", "optional": False, "via": "lookup_employee"},
            {"key": "reason", "label": "事由", "optional": False},
            {"key": "date", "label": "费用发生日期", "optional": False},
            {"key": "amount", "label": "金额(元)", "optional": False},
            {"key": "invoices", "label": "票据张数", "optional": True},
        ],
        "check_hint": "若涉及住宿/交通/餐补，先查《差旅与报销管理制度》报销上限(如住宿分档750/500/350)，超标部分个人承担；不得编造额度。",
    },
    {
        "name": "请假申请",
        "keywords": ["请假", "休假", "调休", "年假", "事假", "病假"],
        "fields": [
            {"key": "employee", "label": "申请人姓名", "optional": False},
            {"key": "department", "label": "部门", "optional": False, "via": "lookup_employee"},
            {"key": "leave_type", "label": "假别(年假/事假/病假/调休)", "optional": False},
            {"key": "start", "label": "起始日期", "optional": False},
            {"key": "end", "label": "结束日期", "optional": False},
            {"key": "reason", "label": "事由", "optional": True},
        ],
        "check_hint": "涉及年假需说明剩余额度口径；调休/病假按《请假管理制度》对应要求（如需证明、需提前的时限）。",
    },
    {
        "name": "资产申领",
        "keywords": ["领用", "申领", "资产", "办公用品", "设备申请"],
        "fields": [
            {"key": "employee", "label": "申请人姓名", "optional": False},
            {"key": "department", "label": "部门", "optional": False, "via": "lookup_employee"},
            {"key": "asset", "label": "申领资产", "optional": False},
            {"key": "reason", "label": "用途/事由", "optional": False},
        ],
        "check_hint": "按《资产管理办法》核对申领范围与审批线（如 IT 设备走 IT 审批）。",
    },
    {
        "name": "加班申请",
        "keywords": ["加班", "加班申请", "加班费"],
        "fields": [
            {"key": "employee", "label": "申请人姓名", "optional": False},
            {"key": "department", "label": "部门", "optional": False, "via": "lookup_employee"},
            {"key": "date", "label": "加班日期", "optional": False},
            {"key": "hours", "label": "加班时长(小时)", "optional": False},
            {"key": "reason", "label": "事由", "optional": True},
        ],
        "check_hint": "按《考勤与加班管理制度》核对加班认定与补偿口径。",
    },
]


def detect_workflow(question: str) -> str | None:
    q = question.lower()
    for w in WORKFLOWS:
        if any(k.lower() in q for k in w["keywords"]):
            return w["name"]
    return None


def workflow_system(name: str, question: str) -> str:
    wf = next((w for w in WORKFLOWS if w["name"] == name), None)
    if not wf:
        return ""
    fields = "；".join(f["label"] for f in wf["fields"])
    via = "；".join(
        f"'{f['label']}'用 mcp__ops__lookup_employee 核实" for f in wf["fields"] if f.get("via")
    )
    return (
        "\n\n【工作流：%s】\n"
        "本问题是一个需要走流程的申请类事务。请按以下步骤办理（只读顾问，绝不代提交）：\n"
        "1. 核对字段是否齐全（需要：%s）。从用户已给的描述中提取；缺失的逐项向用户询问补齐，不要臆造。\n"
        "2. 用只读工具核对：%s（若返回部门/人员有出入，以工具返回为准）。\n"
        "3. %s\n"
        "4. 字段齐全并核对后，输出可提交的草稿单（JSON：%s），字段值必须来自对话或工具返回，"
        "不得编造金额/日期/编号；并注明依据的制度编号。\n"
        "5. 结尾明确提示：系统不代提交，请员工核对后在 OA 完成正式提交流程。\n"
    ) % (name, fields, via or "（按需）", wf["check_hint"], ", ".join(f["key"] for f in wf["fields"]))