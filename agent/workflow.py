"""Workflow v1：复杂流程（OA 报销、请假提交等）的只读向导。

设计边界：系统是只读顾问，不代员工提交单子。对命中流程的问题，
注入引导指令——收集字段、调用只读工具核对（制度上限/人员部门），
输出"草稿表单 JSON + 出处引用"，并提示在 OA 完成正式提交。

实现为 react_agent 的 SYSTEM 增量，不做状态机持久化；
多轮字段补齐依赖 dsh 会话记忆（自建前端每轮独立时可依赖对话补全）。
"""
from __future__ import annotations

import re


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
        "check_hint": "若涉及住宿/交通/餐补，先查《差旅与报销管理制度》报销上限(如住宿分档750/500/350)，超标部分个人承担；核对票据张数与金额，不得编造额度、不得虚构票据。",
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
        "check_hint": "涉及年假先调 lookup_employee 核对剩余额度（以返回为准），不足时提示用 事假/病假 或缩短假期；病假/调休按《请假管理制度》核对所需证明（如病假需证明、提前申请的时限）；不得编造假期余额。",
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
    {
        "name": "出差申请",
        "keywords": ["出差", "出差申请", "出差审批", "差旅申请"],
        "kind": "apply",
        "fields": [
            {"key": "employee", "label": "申请人姓名", "optional": False},
            {"key": "department", "label": "部门", "optional": False, "via": "lookup_employee"},
            {"key": "destination", "label": "目的地", "optional": False},
            {"key": "start", "label": "预计出发日期", "optional": False},
            {"key": "end", "label": "预计返回日期", "optional": False},
            {"key": "reason", "label": "事由", "optional": False},
            {"key": "budget", "label": "预估费用(元)", "optional": True},
        ],
        "check_hint": "先查《差旅与报销管理制度》：住宿分档上限（如一线750/二线500/其他350）、交通与餐补标准，草稿中预填可报销金额并提示超标部分个人承担；不得编造额度。",
    },
    {
        "name": "权限申请",
        "keywords": ["权限", "权限申请", "开通权限", "申请权限", "访问权限", "IT账号", "系统权限", "数据权限", "授权"],
        "kind": "apply",
        "fields": [
            {"key": "employee", "label": "申请人姓名", "optional": False},
            {"key": "department", "label": "部门", "optional": False, "via": "lookup_employee"},
            {"key": "resource", "label": "系统/资源", "optional": False},
            {"key": "grant_level", "label": "所需权限级别", "optional": True},
            {"key": "reason", "label": "用途/事由", "optional": False},
            {"key": "need_by", "label": "需要时间(期望开通日期)", "optional": True},
        ],
        "check_hint": "先查《信息安全管理制度》关于账号权限申请与审批的口径；IT 类权限告知走 IT 审批单，系统数据权限按最小授权原则；不得编造开通结果。",
    },
    {
        "name": "证明开具",
        "keywords": ["在职证明", "收入证明", "离职证明", "开具证明", "证明材料", "证明开具", "开证明", "工作证明"],
        "kind": "apply",
        "fields": [
            {"key": "employee", "label": "申请人姓名", "optional": False},
            {"key": "department", "label": "部门", "optional": False, "via": "lookup_employee"},
            {"key": "cert_type", "label": "证明类型(在职/收入/离职)", "optional": False},
            {"key": "copies", "label": "份数", "optional": True},
            {"key": "purpose", "label": "用途/抬头单位", "optional": True},
        ],
        "check_hint": "按《员工档案与证明管理制度》核对开具流程与所需材料；离职证明需在离职审批完成后开具；不得编造开具结果。",
    },
    {
        "name": "培训申请",
        "keywords": ["培训申请", "报名培训", "申请培训", "参加培训", "培训报名", "外训", "学习申请"],
        "kind": "apply",
        "fields": [
            {"key": "employee", "label": "申请人姓名", "optional": False},
            {"key": "department", "label": "部门", "optional": False, "via": "lookup_employee"},
            {"key": "course", "label": "培训名称", "optional": False},
            {"key": "date", "label": "培训时间", "optional": False},
            {"key": "cost", "label": "培训费用(元)", "optional": True},
            {"key": "reason", "label": "报名事由", "optional": False},
        ],
        "check_hint": "先查《培训管理制度》：内部培训报名/外训申请与费用审批线，费用上限与企业报销口径；不得编造审批结果。",
    },
    {
        "name": "审批查询",
        "keywords": ["审批进度", "审批到哪", "批到哪", "审批状态", "通过了吗", "批了吗", "审批中", "流程到哪", "催办"],
        "kind": "inquiry",
        "fields": [
            {"key": "doc_type", "label": "单据/事项类型(请假/报销/合同/工单)", "optional": False},
            {"key": "doc_id", "label": "单号", "optional": True},
            {"key": "submitted_at", "label": "提交日期", "optional": True},
        ],
        "check_hint": "合同审批以 mcp__ops__query_contract 真实状态为准（按合同号/客户/状态查）；请假/报销等自建流程的审批进度系统未接入 OA，无在线数据，明确告知员工到 OA「我的申请」自行查看，严禁编造审批节点或结果。",
    },
]


WORKFLOW_RE = [
    ("加班申请", re.compile(r"加班|加[^\s，。]{0,3}班")),
    ("资产申领", re.compile(r"领\s*\S{1,5}台|领取|领用|申领|资产")),
    ("培训申请", re.compile(r"报名|培训.*申请|申请.*培训|外训")),
    ("审批查询", re.compile(r"审批.*到哪|到哪了|批到哪|审批结果|批了没有|通过没有|审批通过")),
]


def detect_workflow(question: str) -> str | None:
    q = question.lower()
    hits: list[dict] = []
    for w in WORKFLOWS:
        if any(k.lower() in q for k in w["keywords"]):
            hits.append(w)
    for name, rx in WORKFLOW_RE:
        if name in {w["name"] for w in hits}:
            continue
        if re.search(rx, q):
            hits.append(next(w for w in WORKFLOWS if w["name"] == name))
    if not hits:
        return None
    for w in hits:
        if w.get("kind") == "inquiry":
            return w["name"]
    return hits[0]["name"]


def workflow_system(name: str, question: str) -> str:
    wf = next((w for w in WORKFLOWS if w["name"] == name), None)
    if not wf:
        return ""
    if wf.get("kind") == "inquiry":
        fields = "；".join(f["label"] for f in wf["fields"])
        return (
            "\n\n【工作流：%s（查询/审批进度）】\n"
            "本问题是查进度类事务，请按以下步骤（只读顾问）：\n"
            "1. 核对查询要素（需要：%s）。从用户描述提取；缺失的逐项询问，不要臆造单号/日期。\n"
            "2. %s\n"
            "3. 合同类审批用 mcp__ops__query_contract(status=...) 查真实状态并以返回为准；"
            "请假/报销等自建流程审批进度系统无在线数据，明确告知员工到 OA「我的申请」查看，"
            "绝不编造审批节点/处理人/是否通过。\n"
        ) % (name, fields, wf["check_hint"])
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