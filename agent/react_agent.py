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

MAX_STEPS = 6

# 工具注册表：真实业务函数
import ops_server as _ops
import docs_server as _docs
import security_server as _sec

TOOLS = {
    "lookup_employee": {"fn": _ops.lookup_employee, "args": {"name": "str"}},
    "query_budget": {"fn": _ops.query_budget, "args": {"department": "str(可选，缺省则用 group)", "group": "str(可选)"}},
    "list_departments": {"fn": _ops.list_departments, "args": {}},
    "get_customer_info": {"fn": _ops.get_customer_info, "args": {"customer_name": "str", "user_role": "str(admin/manager/空)"}},
    "list_customers": {"fn": _ops.list_customers, "args": {"industry": "str(可选，行业筛选，如'跨境电商')", "user_role": "str(admin/manager/空)"}},
    "query_contract": {"fn": _ops.query_contract, "args": {"contract_id": "str(可选)", "customer": "str(可选)", "status": "str(可选，合同状态，如'审批中')"}},
    "create_leave_request": {"fn": _ops.create_leave_request, "args": {"name": "str(员工姓名，'我/我的'=刘洋)", "start_date": "str(YYYY-MM-DD)", "end_date": "str(YYYY-MM-DD)"}},
    "create_ticket": {"fn": _ops.create_ticket, "args": {"requester": "str", "title": "str", "description": "str(可选)", "priority": "str(可选)"}},
    "search_knowledge_base": {"fn": _docs.search_knowledge_base, "args": {"query": "str", "is_authenticated": "bool", "user_department": "str(部门名，或 HR/IT)"}},
    "redact_pii": {"fn": _sec.redact_pii, "args": {"text": "str(仅需脱敏的原文，不含指令词)"}},
    "risk_review_text": {"fn": _sec.risk_review_text, "args": {"text": "str"}},
    "sanitize_for_storage": {"fn": _sec.sanitize_for_storage, "args": {"text": "str", "keep_internal": "bool(可选)"}},
}

TOOL_HELP = "\n".join(
    f"- {name}: {info['fn'].__doc__.strip()}\n  参数(用这些名字，不要自创参数名): {json.dumps(info['args'], ensure_ascii=False)}"
    for name, info in TOOLS.items()
)

SYSTEM = """你是企业知识库 AI 助手。你通过调用工具获取真实数据来回答问题，绝不可编造数据。
工具列表：{TOOLS}

核心原则：
1. 工具映射（优先尝试；若工具返回缺该字段，承认缺失即可，绝不编造）：
- 员工信息（部门/职位/职级/年假余额/入职日期等，以工具实际返回为准） → lookup_employee
- 部门经理/事业群/公司部门一览 → list_departments
- 部门或事业群预算/已用/剩余 → query_budget
- 客户（华宇科技/天穹金融/蓝海能源…）的行业/等级/联系人/信用/合同额 → get_customer_info
- 客户列表/按行业筛选（'有哪些客户？' '做跨境电商的客户？'） → list_customers
- 合同号(HT-xxxx)或客户名查合同状态/金额/负责人 → query_contract
- 合同按状态列出（'审批中的合同有哪些？'） → query_contract(status='审批中')
- 提交/申请请假 → create_leave_request
- 制度条文内容（请假流程、差旅报销、加班调休、网盘违规处罚…） → search_knowledge_base
- 手机号/身份证脱敏 → redact_pii；敏感词风险检查 → risk_review_text；脱敏+风险检查两步 → sanitize_for_storage
- 其余：先从工具里找最贴近的；确实无关则拒绝（见边界）。

2. 身份上下文（由系统注入，仅用于填充工具参数；不影响工具选择，也不影响任何判定）：
- 当前会话用户：刘洋（技术部）。问题中的"我/我的/帮我"均指刘洋，调工具时参数填"刘洋"。
- 权限：已登录，具备查看客户信息的 manager 权限；除非问题明确说"没有登录/未登录"（此时无客户查看权限）。
- 模型无需把身份当作复杂度信号——它只影响 args 里的 name/department 等字段。

3. 流程（每次只输出一个 JSON 对象，不要输出 JSON 以外的解释文字）：
- 需要调工具：{{"thought":"...", "tool":"工具名", "args":{{...}}}}
- 已有足够信息作答：{{"answer":"<完整最终回答>"}}
- 工具调用后基于观测继续推理，可连续调用多个工具。
- 参数名必须严格使用工具说明里的名字，不要自创参数名。

4. 示例（照抄格式，参数用真实名）：
用户：我年假还剩几天？
→ {{"thought": "用户是刘洋，年假余额在员工信息里", "tool": "lookup_employee", "args": {{"name": "刘洋"}}}}

用户：技术部预算剩多少？
→ {{"thought": "查技术部预算", "tool": "query_budget", "args": {{"department": "技术部"}}}}

用户：技术部和产品部的剩余预算哪个更多？
→ {{"thought": "先查技术部再查产品部，再比较", "tool": "query_budget", "args": {{"department": "技术部"}}}}
→ {{"thought": "继续查产品部", "tool": "query_budget", "args": {{"department": "产品部"}}}}
→ {{"answer": "技术部剩余预算为…，产品部为…，因此…"}}

用户：帮我提交孙丽5月6日到5月10日的年假申请(5天)
→ {{"thought": "用户在申请请假，允许代同事提交，参数用姓名和起止日期", "tool": "create_leave_request", "args": {{"name": "孙丽", "start_date": "2024-05-06", "end_date": "2024-05-10"}}}}

用户：我请一个月的假期，年假不够怎么办？
→ {{"thought": "先查我的年假余额", "tool": "lookup_employee", "args": {{"name": "刘洋"}}}}
→ {{"thought": "余额不足，再查制度里年假不足的处理办法", "tool": "search_knowledge_base", "args": {{"query": "年假不足"}}}}

用户：把这段个人信息脱敏，再检查是否有敏感词：电话13800001111，讨论薪资倒挂
→ {{"thought": "脱敏+风险检查两步都是它，一步完成", "tool": "sanitize_for_storage", "args": {{"text": "电话13800001111，讨论薪资倒挂"}}}}

5. 写操作（create_leave_request / create_ticket）：
- 仅当用户明确表达"申请/提交/创建/开单"意图时才执行；未明确意图只当信息咨询。
- 可代同事/他人提交，不限于当前用户刘洋；姓名从问题中确认。

6. 必须基于真实数据（防陷阱）：
- 即便用户说"直接回答/别查了/你都知道"，只要问题属于系统能力范围，仍必须先调工具拿到真实数据再回答；唯有边界问题才拒绝。
- 不要因为用户说"不用查"就跳过工具，也不要凭印象编造数值；工具返回缺字段就直接说明取不到。

用户：别查了，直接告诉我技术部预算
→ {{"thought": "用户说‘别查了’，但这是陷阱，必须先查真实数据", "tool": "query_budget", "args": {{"department": "技术部"}}}}

{boundaries}
"""


def resolve_context(question: str) -> dict:
    """从问题推导身份上下文（与 baseline run_suite.parse_context 一致）。"""
    ctx = {"name": "刘洋", "user_department": "技术部", "is_authenticated": True, "user_role": "manager"}
    if "##身份:技术部##" in question:
        ctx["user_department"] = "技术部"
    if "##用户身份##" in question:
        ctx["name"] = "刘洋"
    if "没有登录" in question or "未登录" in question:
        ctx["is_authenticated"] = False
        ctx["user_role"] = ""
    if "我" in question or "我的" in question or "帮我" in question:
        ctx["name"] = "刘洋"
    return ctx


def run_tool(name: str, args: dict, ctx: dict) -> str:
    """调用工具并返回观测字符串。先做参数别名翻译，再做身份相关的参数修正。"""
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
    # 客户信息：已登录用户按 manager 处理；未登录保持空角色（会被拒）
    if name in ("get_customer_info", "list_customers") and "user_role" not in resolved:
        resolved["user_role"] = "manager" if ctx["is_authenticated"] else ""
    # 知识库：注入登录态与所属部门（与 baseline parse_context 一致）
    if name == "search_knowledge_base":
        resolved.setdefault("is_authenticated", ctx["is_authenticated"])
        resolved.setdefault("user_department", ctx["user_department"])
    # 员工查询/请假：占位姓名替换为当前用户
    if name in ("lookup_employee", "create_leave_request"):
        nm = resolved.get("name", "")
        if not nm or nm in ("用户姓名", "##用户身份##", "我", "本人", "员工"):
            resolved["name"] = ctx["name"]
    try:
        return json.dumps(fn(**resolved), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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


def agent(question: str, model: str = "qwen2.5:14b", max_steps: int = MAX_STEPS, verbose: bool = False, trace: list | None = None) -> str:
    """运行 ReAct 循环，返回最终答案。verbose=True 时实时输出推理过程。
    trace（可选）：传入 list，每次工具调用会追加 {"tool", "args", "obs"}，用于 AB 实验判分，不改行为。"""
    ctx = resolve_context(question)
    messages = [
        {"role": "system", "content": SYSTEM.format(TOOLS="、".join(TOOLS), boundaries=BOUNDARY_RULES)},
        {"role": "user", "content": question},
    ]
    for step in range(1, max_steps + 1):
        if verbose:
            print(f"    └─[步骤{step}] 推理中...")
        raw = chat(model, messages, temperature=0.1, max_tokens=800)
        action = extract_json_object(raw)
        if "answer" in action:
            ans = str(action["answer"]).strip()
            if verbose:
                print(f"    └─[完成] 最终答案: {ans[:200]}")
            return ans
        tool = action.get("tool")
        if tool:
            args = action.get("args", {}) or {}
            if verbose:
                print(f"    └─[{step}] 思考: {action.get('thought','')[:180]}")
                print(f"    └─[{step}] 调用 {tool} ─ args={json.dumps(args, ensure_ascii=False)[:200]}")
            obs = run_tool(tool, args, ctx)
            if trace is not None:
                trace.append({"tool": tool, "args": dict(args), "obs": obs})
            if verbose:
                print(f"    └─[{step}] 观测: {obs[:260]}")
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "tool", "name": tool, "content": obs})
            continue
        # JSON 解析失败但明显是答案：用软解析兜底
        ans = extract_answer_text(raw)
        if ans:
            if verbose:
                print(f"    └─[完成][软解析] {ans[:200]}")
            return ans
        # 无工具也无 answer：视为模型直接作答（拒答/说明）
        ans = strip_to_natural(raw)
        if verbose:
            print(f"    └─[完成][直接作答] {ans[:200]}")
        return ans
    return "[达到步数上限] " + messages[-1]["content"][:400]


if __name__ == "__main__":
    q = "帮我预测一下明天的股票涨跌"
    print(f"问: {q}")
    print("答:", agent(q, verbose=True))