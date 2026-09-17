"""ReAct Agent：用 LLM（当前 qwen2.5:14b）通过 推理→行动→观察 循环调用真实 MCP 工具 回答。

关键设计：
- 模型不直接生成答案，而是生成 JSON 动作：{thought, tool, args} 或 {answer}
- 工具调用走真实 mcp_servers 的业务函数（同一个数据栈，绝不编造）
- 身份上下文由问题文本推导（与 baseline run_suite 的 parse_context 一致）：
  默认 当前用户=刘洋、已登录、对客户信息有 manager 权限；问题含"没有登录/未登录"则置为未认证（客户信息将被拒）。
- 越界/离题/他人隐私类问题，模型应直接礼貌拒绝，不调用工具。
- 循环上限 MAX_STEPS，避免死循环。
"""
import json
import os
import re
import sys
import inspect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp_servers"))

from llm import chat  # noqa: E402
from boundaries import BOUNDARY_RULES  # noqa: E402
from verifier import verify, MEASURE_WORDS  # noqa: E402
from workflow import detect_workflow, workflow_system  # noqa: E402
from injection import scan_injection, observation_guard, mask_pii  # noqa: E402
from audit import audit_tool_call  # noqa: E402

MAX_STEPS = 6

MAX_HISTORY_TURNS = 8  # 多轮对话最多带入的完整问答轮（防爆 token / 防长文本注入）


def _fmt_history(history: list | None) -> list[dict]:
    """把多轮历史 [{q, a}] 转成 messages 中 system 之后的 user/assistant 轮次。

    策略：
    - 最近 MAX_HISTORY_TURNS 轮保留完整对话（q 截 600 字 / a 截 1200 字）。
    - 更早的轮次全部滚成一条"此前已讨论"摘要（抽取实体/业务数值/流程名等关键线索，
      保语义不保全文，实体总数封顶防爆 token）——长会话早期上下文不丢。
    - 历史中不含工具观测（工具轨迹由本轮 re-act 自取，避免把旧观测注入误导新推理）。"""
    items = [it for it in (history or []) if isinstance(it, dict)]
    recent = items[-MAX_HISTORY_TURNS:]
    older = items[:-MAX_HISTORY_TURNS] if len(items) > MAX_HISTORY_TURNS else []

    msgs: list[dict] = []
    if older:
        summary = _summarize_history(older)
        if summary:
            msgs.append({"role": "user", "content": "（此前已讨论，摘要）" + summary})
            msgs.append({"role": "assistant", "content": "（摘要已收悉，继续当前话题）"})
    for item in recent:
        q = (str(item.get("q") or "")).strip()[:600]
        a = (str(item.get("a") or "")).strip()[:1200]
        if q:
            msgs.append({"role": "user", "content": q})
        if a:
            msgs.append({"role": "assistant", "content": a})
    return msgs


_ENTITY_RE = re.compile(
    r"(DOC-\d{3}|HT-\d{4}-\d{3}|\d+\.?\d*\s*(元|万|天|个工作日|%|人|小时)|"
    r"[一-龥]{2,6}(部|部门|事业群)|[\u4e00-\u9fff]{2,4}(请假|报销|年假|加班|出差|培训|转正|入职|离职))"
)


def _summarize_history(older: list[dict]) -> str:
    """把更早的轮次压成一条要点摘要：只保留实体/业务数值/流程名，去重保序。"""
    seen: set[str] = set()
    bits: list[str] = []
    for it in older:
        q = str(it.get("q") or "")[:300]
        for m in _ENTITY_RE.finditer(q):
            token = m.group(0).strip()
            if token and token not in seen:
                seen.add(token)
                bits.append(token)
    cap = bits[:120]
    return "；".join(cap) if cap else ""

# 工具注册表：真实业务函数
import ops_server as _ops
import docs_server as _docs
import security_server as _sec
import memory_server as _mem
import entity_store as _ent

TOOLS = {
    "lookup_employee": {"fn": _ops.lookup_employee, "args": {"name": "str"}},
    "query_budget": {"fn": _ops.query_budget, "args": {"department": "str(可选，缺省则用 group)", "group": "str(可选)"}},
    "list_departments": {"fn": _ops.list_departments, "args": {}},
    "get_customer_info": {"fn": _ops.get_customer_info, "args": {"customer_name": "str", "user_role": "str(admin/manager/空)"}},
    "list_customers": {"fn": _ops.list_customers, "args": {"industry": "str(可选，行业筛选，如'跨境电商')", "user_role": "str(admin/manager/空)"}},
    "query_contract": {"fn": _ops.query_contract, "args": {"contract_id": "str(可选)", "customer": "str(可选)", "status": "str(可选，合同状态，如'审批中')"}},
    "search_knowledge_base": {"fn": _docs.search_knowledge_base, "args": {"query": "str", "is_authenticated": "bool", "user_department": "str(部门名，或 HR/IT)"}},
    "redact_pii": {"fn": _sec.redact_pii, "args": {"text": "str(仅需脱敏的原文，不含指令词)"}},
    "risk_review_text": {"fn": _sec.risk_review_text, "args": {"text": "str"}},
    "sanitize_for_storage": {"fn": _sec.sanitize_for_storage, "args": {"text": "str", "keep_internal": "bool(可选)"}},
    "resolve_entity": {"fn": _mem.resolve_entity, "args": {"mention": "str", "type_hint": "str(可选:employee/department/customer/contract)"}},
}

# 写类工具（改变外部状态的破坏性操作）被白名单机制永远排除：模型看不到、run_tool 拒绝执行。
# create_leave_request / create_ticket 定义在 ops_server，但绝不进入 TOOLS 注册表，
# 即使模型幻觉输出这些名字，也只会得到 {"error": "未知工具 ..."}，不会产生副作用。
# 若未来确实需要在线写操作，再按任务白名单单独放行——默认任何会话都不授予。

WRITE_BLOCKED = {"create_leave_request", "create_ticket"}
READ_TOOLS = [n for n in TOOLS if n not in WRITE_BLOCKED]

# ===== 问题意图 → 最小工具白名单（默认只暴露当前任务需要的读工具） =====
_KB = {"search_knowledge_base"}
_CUST = {"get_customer_info", "list_customers", "query_contract"}
_BUD = {"query_budget", "list_departments"}
_HR = {"lookup_employee", "list_departments"}
_SEC = {"redact_pii", "risk_review_text", "sanitize_for_storage"}

_CUST_HINT = re.compile(r"客户|合同|HT-\d|行业|跨境电商|应收|开票|信用|华宇|天穹|蓝海|订单|收款")
_BUD_HINT = re.compile(r"预算|已用|剩余|经费|拨付|项目费")
_HR_HINT = re.compile(r"员工|部门|组织|考勤|年假|请假|加班|调休|入职|离职|转正|试用|培训|薪资|工资|绩效|考核|花名册|负责人|经理|应到|报到|职位|职级|职称|在哪个部门|年假余额")
_SEC_HINT = re.compile(r"脱敏|PII|敏感词|风险检查|存储|sanitize|redact|个人信息|隐私")
_KB_HINT = re.compile(r"制度|流程|指南|规定|操作规范|手册|标准|费用|报销|差旅|补贴|补助|住宿|用餐|发票|单据|处罚|违规|文档|新员工|培训|测评|试用期|转正|社保|公积金|工伤|医疗|离职|入职|考勤|请假")

_COREF = {"resolve_entity"}


def classify_profile(question: str) -> list[str]:
    """按问题意图返回最小工具白名单。命中多个意图则合并；都无法判定时给全部读工具兜底。"""
    allow: set[str] = set()
    if _SEC_HINT.search(question):
        allow |= _SEC
    if _CUST_HINT.search(question):
        allow |= _CUST
    if _BUD_HINT.search(question):
        allow |= _BUD
    if _HR_HINT.search(question):
        allow |= _HR
    if _KB_HINT.search(question):
        allow |= _KB
    if _ent.find_pronouns(question):
        allow |= _COREF
    if not allow:
        allow = set(READ_TOOLS)
    allow |= _KB  # 制度检索作为通用兜底，避免分类漏配导致无工具可用
    return [n for n in READ_TOOLS if n in allow]

# ===== 工具映射表与示例（按白名单动态注入 SYSTEM，白名单外工具名绝不出现）=====
# tags：该行/示例依赖的工具集合，任一在白名单即展示；防止模型滑向白名单外的工具名。
_MAPPING: list[tuple[tuple, str]] = [
    (("lookup_employee",), "员工信息（部门/职位/职级/年假余额/入职日期等，以工具实际返回为准） → lookup_employee"),
    (("list_departments",), "部门经理/事业群/公司部门一览 → list_departments"),
    (("query_budget",), "部门或事业群预算/已用/剩余 → query_budget"),
    (("get_customer_info",), "客户（华宇科技/天穹金融/蓝海能源…）的行业/等级/联系人/信用/合同额 → get_customer_info"),
    (("list_customers",), "客户列表/按行业筛选（'有哪些客户？' '做跨境电商的客户？'） → list_customers"),
    (("query_contract",), "合同号(HT-xxxx)或客户名查合同状态/金额/负责人 → query_contract"),
    (("query_contract",), "合同按状态列出（'审批中的合同有哪些？'） → query_contract(status='审批中')"),
    (("search_knowledge_base",), "制度条文内容（请假流程、差旅报销、加班调休、网盘违规处罚…） → search_knowledge_base"),
    (("redact_pii",), "手机号/身份证脱敏 → redact_pii"),
    (("risk_review_text",), "敏感词风险检查 → risk_review_text"),
    (("sanitize_for_storage",), "脱敏+风险检查两步（内容涉及'脱敏/敏感词'时优先） → sanitize_for_storage"),
    (("resolve_entity",), "指代词（他/她/那家客户/这个部门/那份合同）解析成规范实体 → resolve_entity"),
]

_EXAMPLES: list[tuple[tuple, str]] = [
    (("lookup_employee",),
     "用户：我年假还剩几天？\n"
     '→ {"thought": "用户是刘洋，年假余额在员工信息里", "tool": "lookup_employee", "args": {"name": "刘洋"}}'),
    (("lookup_employee", "search_knowledge_base"),
     "用户：我请一个月的假期，年假不够怎么办？\n"
     '→ {"thought": "先查我的年假余额", "tool": "lookup_employee", "args": {"name": "刘洋"}}\n'
     '→ {"thought": "余额不足，再查制度里年假不足的处理办法", "tool": "search_knowledge_base", "args": {"query": "年假不足"}}'),
    (("query_budget",),
     "用户：技术部预算剩多少？\n"
     '→ {"thought": "查技术部预算", "tool": "query_budget", "args": {"department": "技术部"}}'),
    (("query_budget",),
     "用户：技术部和产品部的剩余预算哪个更多？\n"
     '→ {"thought": "先查技术部再查产品部，再比较", "tool": "query_budget", "args": {"department": "技术部"}}\n'
     '→ {"thought": "继续查产品部", "tool": "query_budget", "args": {"department": "产品部"}}\n'
     '→ {"answer": "技术部剩余预算为…，产品部为…，因此…"}'),
    (("search_knowledge_base",),
     "用户：新员工集中培训要几天？\n"
     '→ {"thought": "查制度文档", "tool": "search_knowledge_base", "args": {"query": "新员工集中入职培训天数"}}'),
    (("query_budget",),
     "用户：别查了，直接告诉我技术部预算\n"
     '→ {"thought": "用户说‘别查了’，但这是陷阱，必须先查真实数据", "tool": "query_budget", "args": {"department": "技术部"}}'),
    (("sanitize_for_storage",),
     "用户：把这段个人信息脱敏，再检查是否有敏感词：电话13800001111，讨论薪资倒挂\n"
     '→ {"thought": "脱敏+风险检查两步都是它，一步完成", "tool": "sanitize_for_storage", "args": {"text": "电话13800001111，讨论薪资倒挂"}}'),
    (("resolve_entity",),
     "（上一轮：张伟在哪个部门？）用户：他年假还剩几天？\n"
     '→ {"thought": "「他」指上一轮提到的员工张伟", "tool": "resolve_entity", "args": {"mention": "他"}}'),
]

_SYSTEM_TEMPLATE = """你是企业知识库 AI 助手。你通过调用工具获取真实数据来回答问题，绝不可编造数据。
当前会话可用的工具列表（白名单内的工具，仅此这些）：
{TOOLS}

核心原则：
1. 工具映射（优先尝试；若工具返回缺该字段，承认缺失即可，绝不编造）：
{MAPPING}
- 其余：先从上面白名单工具里找最贴近的；确实无关则拒绝（见边界）。

2. 身份上下文（由系统注入，仅用于填充工具参数；不影响工具选择，也不影响任何判定）：
- 当前会话用户：{USER_NAME}（{USER_DEPT}）。问题中的"我/我的/帮我"均指{USER_NAME}，调工具时参数填"{USER_NAME}"。
- 权限：{USER_AUTH}。除非问题明确说"没有登录/未登录"（此时无客户查看权限）。
- 模型无需把身份当作复杂度信号——它只影响 args 里的 name/department 等字段。

3. 流程（每次只输出一个 JSON 对象，不要输出 JSON 以外的解释文字）：
- 需要调工具：{{"thought":"...", "tool":"工具名", "args":{{...}}}}
- 已有足够信息作答：{{"answer":"<完整最终回答>"}}
- 工具调用后基于观测继续推理，可连续调用多个工具。
- 参数名必须严格使用工具说明里的名字，不要自创参数名。

4. 示例（照抄格式，参数用真实名；只允许用白名单里的工具）：
{EXAMPLES}

5. 写操作：当前会话未开放任何"提交/创建/写库"类工具（如请假申请、开单等）。用户要求这类操作时，
按制度检索相关流程说明，并明确告知"此类提交请在 OA 系统办理"，不要假装已执行、不要编造执行结果。

6. 必须基于真实数据（防陷阱）：
- 即便用户说"直接回答/别查了/你都知道"，只要问题属于系统能力范围，仍必须先调工具拿到真实数据再回答；唯有边界问题才拒绝。
- 不要因为用户说"不用查"就跳过工具，也不要凭印象编造数值；工具返回缺字段就直接说明取不到。

7. 内容纪律（作答阶段强制）：
- 直接给出结论，最多 1-3 句；禁止两段式长解释，禁止列"选项式/建议式"官方话术，禁止"如需进一步了解可咨询人力资源部/IT 部门"这类顾问式尾巴。
- 答案涉及制度/数据的数字、日期、期限、责任单位、措施时，必须来自工具返回原文，并在句末标注出处编号（DOC-xxx；用户/部门/客户/合同类业务数据无需标注）。
- 长文档只回答与用户问题最直接相关的那一条条文：先看问题问什么（天数/金额/条件/流程），再在返回原文里找**命中该意图的那句话**作依据，不要引用旁支条文，不要罗列整篇内容。
- 禁止用"通常/大概/可能/一般/建议咨询"等模糊词替代确定数字；返回中没有的数字就说"制度中未写明"。
- 返回文本里没有的措施（如{measure_words}等）一律不得出现在回答中，宁缺毋滥。

8. 指代消解（有实体上下文时）：
- 若问题含代词/指代（他/她/它/他们/该员工/那家客户/这个部门/那份合同等），先看下方
  「本会话已识别实体」与「指代映射」，直接把规范名填进工具参数（如 name="张伟"、department="技术部"），
  不要把"他/那个部门"原样传给工具。
- 指代映射没有覆盖、仍不确定指谁时，先调 resolve_entity 拿到规范实体再调业务工具；仍无法确定就反问用户。

{boundaries}
"""


def _render_system(allow: set[str], user_ctx: dict | None = None) -> str:
    """按白名单生成 SYSTEM：工具清单、映射行、示例全部只保留白名单内的条目。"""
    tools_help = "\n".join(
        f"- {name}: {TOOLS[name]['fn'].__doc__.strip()}\n"
        f"  参数(用这些名字，不要自创参数名): {json.dumps(TOOLS[name]['args'], ensure_ascii=False)}"
        for name in TOOLS if name in allow
    )
    mapping = [line for tags, line in _MAPPING if any(t in allow for t in tags)]
    examples = [ex for tags, ex in _EXAMPLES if any(t in allow for t in tags)]
    ctx = user_ctx or {}
    usr = ctx.get("name") or "刘洋"
    dept = ctx.get("department") or (ctx.get("user_department") or "技术部")
    if ctx.get("is_authenticated", True):
        auth = f"已登录，具备查看客户信息的 {ctx.get('user_role') or 'manager'} 权限"
    else:
        auth = "未登录，无客户查看权限"
    return _SYSTEM_TEMPLATE.format(
        TOOLS=tools_help,
        MAPPING="\n".join(f"- {m}" for m in mapping),
        EXAMPLES="\n".join(examples),
        USER_NAME=usr,
        USER_DEPT=dept,
        USER_AUTH=auth,
        boundaries=BOUNDARY_RULES,
        measure_words="、".join(MEASURE_WORDS[:3]),
    )


def resolve_context(question: str, user_ctx: dict | None = None) -> dict:
    """从问题推导身份上下文（与 baseline run_suite.parse_context 一致）。

    user_ctx 来自登录会话（web_app 注入）：姓名/部门/权限角色。
    未提供时回退到默认演示身份（刘洋/技术部/已登录/manager），保证 dsh 与无登录调用可用。
    """
    if user_ctx:
        role = user_ctx.get("user_role")
        ctx = {
            "name": user_ctx.get("name") or "刘洋",
            "user_department": user_ctx.get("department") or "技术部",
            "is_authenticated": True,
            "user_role": role if role is not None else ("manager" if user_ctx.get("level") else ""),
        }
    else:
        ctx = {"name": "刘洋", "user_department": "技术部", "is_authenticated": True, "user_role": "manager"}
    if "##身份:技术部##" in question:
        ctx["user_department"] = "技术部"
    if "##用户身份##" in question:
        ctx["name"] = "刘洋"
    if "没有登录" in question or "未登录" in question:
        ctx["is_authenticated"] = False
        ctx["user_role"] = ""
    return ctx


_ENTITY_TYPE_LABEL = {"employee": "员工", "department": "部门", "customer": "客户", "contract": "合同"}


def _recent_questions(history: list | None, limit: int = 8) -> list[str]:
    qs = [str(h.get("q") or "") for h in (history or []) if isinstance(h, dict)]
    return [q for q in qs if q.strip()][-limit:]


def entity_context_block(history: list | None, question: str, ctx: dict) -> str:
    """生成「本会话已识别实体 + 指代映射」提示块，供 SYSTEM 注入（确定性、不额外调模型）。

    实体来自 entity_store（与 memory_server 同一实现），已按当前会话 RBAC 过滤：
    客户/合同实体仅管理层可见，员工/部门实体需已登录。"""
    hist = " ".join(_recent_questions(history))
    scope = (hist + " " + (question or "")).strip()
    if not scope:
        return ""
    ia = ctx.get("is_authenticated", True)
    role = ctx.get("user_role", "")
    order: list[dict] = []
    seen: set[str] = set()
    for h in _ent.extract_entities(scope, is_authenticated=ia, user_role=role):
        if h["id"] not in seen:
            seen.add(h["id"])
            order.append(h)

    maps: list[str] = []
    for p in _ent.find_pronouns(question or ""):
        r = _ent.resolve_entity(p["mention"], context_text=scope, type_hint=p["type"],
                                is_authenticated=ia, user_role=role)
        if r["resolved"] and r["entity"]:
            t = r["entity"]["type"]
            maps.append(f"「{p['mention']}」→ {r['entity']['name']}（{_ENTITY_TYPE_LABEL.get(t, t)}）")
    if not order and not maps:
        return ""

    lines = ["【本会话已识别实体（用于指代消解，工具参数一律用规范名）】"]
    groups: dict[str, list[str]] = {}
    for h in order:
        groups.setdefault(h["type"], []).append(h["name"])
    for t, names in groups.items():
        uniq = list(dict.fromkeys(names))[:12]
        lines.append(f"- {_ENTITY_TYPE_LABEL.get(t, t)}：" + "、".join(uniq))
    if maps:
        lines.append("- 指代映射：" + "；".join(maps))
    return "\n".join(lines)


def run_tool(name: str, args: dict, ctx: dict, allow: set[str] | None = None) -> str:
    """调用工具并返回观测字符串。先验白名单：白名单外的工具一律拒绝，绝不执行。"""
    if allow is not None and name not in allow:
        return json.dumps(
            {"error": f"工具 {name} 不在当前会话工具白名单中，已拒绝执行（本会话只允许：{sorted(allow)}）"},
            ensure_ascii=False,
        )
    info = TOOLS.get(name)
    if not info:
        return json.dumps({"error": f"未知工具 {name}"}, ensure_ascii=False)
    args = dict(args)
    # 参数别名翻译：模型可能传 customer_name/employee_name/contract_number 等
    fn = info["fn"]
    params = list(inspect.signature(fn).parameters)
    ALIASES = {
        "name": ["employee_name", "employee", "person", "staff", "姓名", "emp_name", "employeeName"],
        "customer": ["customer_name", "client", "客户"],
        "customer_name": ["customer", "客户", "company", "company_name"],
        "department": ["dept", "部门", "department_name", "dept_name", "departmentName"],
        "group": ["事业群", "bg", "bg_name", "group_name"],
        "status": ["合同状态", "状态", "stage", "contract_status"],
        "contract_id": ["contract", "contract_number", "contract_no", "合同号", "合同编号", "contractId"],
        "start_date": ["from", "start", "开始日期", "startDate"],
        "end_date": ["to", "end", "结束日期", "endDate"],
        "query": ["keyword", "关键词", "text"],
        "text": ["content", "内容", "input"],
    }
    resolved = {}
    for p in params:
        if p in args:
            resolved[p] = args[p]
        else:
            for alias in ALIASES.get(p, []):
                if alias in args:
                    resolved[p] = args[alias]
                    break
    # 客户信息：RBAC 无条件以当前会话角色为准——经理可见，普通员工/未登录被拒。
    # 模型可能自行在 args 里写 user_role，这里强制覆盖，杜绝"自我晋升"绕过。
    if name in ("get_customer_info", "list_customers"):
        resolved["user_role"] = ctx.get("user_role", "")
    # 知识库：注入登录态与所属部门（与 baseline parse_context 一致）
    if name == "search_knowledge_base":
        resolved.setdefault("is_authenticated", ctx["is_authenticated"])
        resolved.setdefault("user_department", ctx["user_department"])
    # 实体记忆/指代消解：用户身份与上下文一律以服务端会话为准，模型只需给 mention
    if name == "resolve_entity":
        resolved.setdefault("user_role", ctx.get("user_role", ""))
        resolved.setdefault("is_authenticated", ctx.get("is_authenticated", True))
        resolved.setdefault("context_text", ctx.get("_coref_context", ""))
    # 员工查询/请假：占位姓名替换为当前用户
    if name in ("lookup_employee", "create_leave_request"):
        nm = resolved.get("name", "")
        if not nm or nm in ("用户姓名", "##用户身份##", "我", "本人", "员工"):
            resolved["name"] = ctx["name"]
    try:
        obs = json.dumps(fn(**resolved), ensure_ascii=False)
        # 安全审计：记录谁在何时以何参数调了何工具（含密级敏感标记）
        audit_tool_call(ctx, name, resolved, obs)
        return obs
    except Exception as e:
        err = json.dumps({"error": str(e)}, ensure_ascii=False)
        audit_tool_call(ctx, name, resolved, err)
        return err


def extract_json_object(text: str) -> dict:
    """提取第一个完整的 JSON 对象（容忍代码块、前后缀、连续多个 JSON）。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    i = text.find("{")
    while i != -1:
        depth = 0
        j = i
        in_str = False
        esc = False
        while j < len(text):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[i : j + 1])
                        except json.JSONDecodeError:
                            return {}
            j += 1
        i = text.find("{", j + 1)
    return {}


def extract_answer_text(raw: str) -> str | None:
    """容忍模型输出未转义换行的 answer JSON，直接抓取 answer 字符串内容。"""
    m = re.search(r'"answer"\s*[:：]\s*"(.*)"\s*}', raw, re.S)
    if m:
        return (
            m.group(1)
            .replace("\\n", "\n")
            .replace("\\\"", '"')
            .replace('\\"', '"')
            .strip()
        )
    return None


def strip_to_natural(text: str) -> str:
    """把模型输出里的 JSON 语法剥掉，保留自然语言（用于纯 thought 拒答）。"""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    text = text.strip("{} \n\t")
    for k in ("thought", "reason", "answer", "message"):
        text = re.sub(rf'"{k}"\s*[:：]\s*"', "", text)
        text = text.replace('",', "，").replace('"', "").replace("\\", "")
    return text.strip()


def agent(question: str, model: str = "qwen2.5:14b", max_steps: int = MAX_STEPS, verbose: bool = False, trace: list | None = None, allow_retry: bool = True, allow: list[str] | None = None, user_ctx: dict | None = None, history: list | None = None, out: dict | None = None) -> str:
    """运行 ReAct 循环，返回最终答案。回答前先经过回检器（verifier）：无源断言（DOC 编号/带单位数字/措施词不在工具返回中）
    会触发一次纠正重答（allow_retry=True 时），把幻觉压到最低。
    allow（工具白名单）：默认全部读工具（写工具一律排除）。传更小集合可做到最小暴露——
    模型只能看到/调用白名单内的工具，白名单外调用被 run_tool 拒绝，绝不执行。
    trace（可选）：传入 list，每次工具调用会追加 {"tool", "args", "obs"}，用于 AB 实验判分，不改行为。
    user_ctx（可选）：登录会话身份 {name, department, user_role}——取代硬编码"刘洋"，用于工具参数与客户信息 RBAC。
    history（可选）：多轮对话历史 [{q, a}, ...]，作为本轮之前的 user/assistant 上下文拼进 messages
    （支持"张三在哪个部门 → 那个部门多少人"类指代；限制最近 8 轮 + 单条截断）。
    out（可选）：传入 dict 会写入回检裁决 {"answer", "verdict"(ok/soft/fail), "issues", "soft_notes"}，
    供监控/评测消费；verdict="soft" 表示已交付但存在宽松命中断言，建议标"待核实"。"""
    if allow is None:
        allow_set = set(READ_TOOLS)
    else:
        allow_set = set(n for n in allow if n in READ_TOOLS)  # 白名单以读工具为上限，写工具永不给
    ctx = resolve_context(question, user_ctx)
    ctx["_coref_context"] = " ".join(_recent_questions(history))
    wf_name = detect_workflow(question)

    # 入口防线：用户问题/历史包含注入模式 → 直接拒绝，不进 ReAct 循环
    if scan_injection(question) or any(scan_injection(str(it.get("q") or "")) for it in (history or [])):
        if out is not None:
            out.setdefault("verdict", "ok")
            out.setdefault("issues", [])
            out.setdefault("soft_notes", ["已拦截 prompt 注入输入"])
        return "检测到您的输入包含越权/覆盖指令类内容，为保障企业数据安全，本次请求已被拦截，请改述问题后重试。"

    def redact(ans: str) -> str:
        """出口防线：所有用户可见回答必经 PII 脱敏（手机号/身份证）。"""
        safe, _ = mask_pii(ans)
        return safe

    system = _render_system(allow_set, ctx)
    if wf_name:
        system += workflow_system(wf_name, question)
    ent_block = entity_context_block(history, question, ctx)
    if ent_block:
        system += "\n\n" + ent_block
    messages = [{"role": "system", "content": system}]
    messages += _fmt_history(history)
    messages.append({"role": "user", "content": question})

    def deliver(ans: str) -> str | None:
        """回检：verdict="fail" 且还有重试机会时触发纠正重答并返回 None；
        "ok"/"soft" 一律交付（soft 记录进 out.soft_notes）。"""
        verdict = verify(question, trace or [], ans)
        if verbose:
            print(f"    └─[回检] verdict={verdict['verdict']} issues={verdict['issues'][:5]} soft={verdict['soft_notes'][:3]}")
        if out is not None:
            out.update({
                "verdict": verdict["verdict"],
                "issues": verdict["issues"],
                "soft_notes": verdict["soft_notes"],
            })
        if allow_retry and verdict["verdict"] == "fail":
            if first_ans[0] is None:
                first_ans[0] = ans  # 记住首次自然回答，作兜底
            messages.append({"role": "user", "content": (
                "检查：回答存在无源断言：" + "；".join(verdict["issues"][:5])
                + "。请严格依据已返回的工具观测原文重答，删除所有无依据的数字/措施/DOC 引用，"
                  "只保留有工具返回支撑的内容，1-3 句；不确定就明确说“制度中未写明”，无需重新调用工具。"
            )})
            return None
        return ans

    first_ans: list[str | None] = [None]

    for attempt in range(2):  # 首次 + 回检失败时的纠正重答
        for step in range(1, max_steps + 1):
            if verbose:
                print(f"    └─[步骤{step}] 推理中...")
            raw = chat(model, messages, temperature=0.1, max_tokens=800)
            action = extract_json_object(raw)
            if "answer" in action:
                ans = deliver(str(action["answer"]).strip())
                if ans is not None:
                    return redact(ans)
                break  # 被回检拦截，进入重答轮
            tool = action.get("tool")
            if tool:
                args = action.get("args", {}) or {}
                if verbose:
                    print(f"    └─[{step}] 思考: {action.get('thought','')[:180]}")
                    print(f"    └─[{step}] 调用 {tool} ─ args={json.dumps(args, ensure_ascii=False)[:200]}")
                obs = run_tool(tool, args, ctx, allow_set)
                if trace is not None:
                    trace.append({"tool": tool, "args": dict(args), "obs": obs})
                if verbose:
                    print(f"    └─[{step}] 观测: {obs[:260]}")
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "tool", "name": tool, "content": observation_guard(obs)})
                continue
            # JSON 解析失败但明显是答案：用软解析兜底
            ans0 = extract_answer_text(raw)
            if ans0:
                ans = deliver(ans0)
                if ans is not None:
                    if verbose:
                        print(f"    └─[完成][软解析] {ans[:200]}")
                    return redact(ans)
                break
            # 无工具也无 answer：视为模型直接作答（拒答/说明）
            ans = deliver(strip_to_natural(raw))
            if ans is not None:
                if verbose:
                    print(f"    └─[完成][直接作答] {ans[:200]}")
                return redact(ans)
            break
        else:
            return "[达到步数上限] " + messages[-1]["content"][:400]
    # 重试耗尽：绝不把回检指令/无源内容暴露给用户
    if first_ans[0] and not trace:
        if out is not None:
            out.setdefault("verdict", "soft")
            out.setdefault("issues", [])
            out.setdefault("soft_notes", ["重答耗尽，退回首次自然回答（未通过回检）"])
        return redact(first_ans[0])
    if out is not None:
        out.setdefault("verdict", "fail")
        out.setdefault("issues", ["重答耗尽且无兜底，未能确认证据"])
        out.setdefault("soft_notes", [])
    return "未能从公司制度文档中确认以上信息，请查阅相关制度原文或咨询人事/财务/行政对口部门，以免误用。"


if __name__ == "__main__":
    q = "帮我预测一下明天的股票涨跌"
    print(f"问: {q}")
    print("答:", agent(q, verbose=True))