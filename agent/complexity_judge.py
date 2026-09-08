"""复杂度裁判：用 LLM 判断一个问题应走哪个路由（三态）。

verdict ∈ {simple, complex, reject}：
- simple : 单点事实查询，一次工具调用即可（含明确参数的一次写操作、一次通用返回）→ 14b
- complex: 多步/跨工具聚合、比较计算、RBAC 权限语义、空值/边界兜底、需查多家再综合 → 32b
- reject : 系统外/离题、他人隐私、纯闲聊，直接礼貌拒答，不进 ReAct → 14b 拒答

用户明确要求：**判定必须由模型完成**，不能用关键词/规则。
few-shot 校准示例来自 36 题黄金集实测误分（做过的错题），帮助模型对齐业务语义。
规则引擎只做最外层防呆（LLM 不可用时 fallback=complex，不丢信息）。
模型输出 JSON {verdict, reason}。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from llm import chat_json, MODEL_SMALL  # noqa: E402

VALID = {"simple", "complex", "reject"}

FEWSHOT = [
    # (user, assistant_json)  都是从真实误分样本提炼的
    ("帮我提交黄国栋2024-05-06到05-07的年假申请",
     '{"verdict": "simple", "reason": "参数齐全的一次写操作，无需权限推理或计算"}'),
    ("检查这句话有没有风险：我们拿到了竞品的数据",
     '{"verdict": "simple", "reason": "单次风险检查工具即可，无需多步"}'),
    ("如果文本为空，脱敏和风险检查分别返回什么？",
     '{"verdict": "complex", "reason": "空值边界情况，需要工具确认默认行为，易想当然，值得更强模型"}'),
    ("领导为啥没上班？",
     '{"verdict": "reject", "reason": "他人考勤/隐私，属系统外，直接拒答"}'),
    ("同事的工资是多少？",
     '{"verdict": "reject", "reason": "他人隐私，直接拒答"}'),
    ("帮我预测一下明天的股票涨跌",
     '{"verdict": "reject", "reason": "股票预测与系统无关，直接拒答"}'),
    ("技术部经理是谁？和运维部经理是同一个人吗？",
     '{"verdict": "complex", "reason": "需查询两个部门再比较"}'),
    ("技术部预算多少？",
     '{"verdict": "simple", "reason": "单点事实查询，一次工具调用"}'),
]

JUDGE_PROMPT = """你是企业 AI 助手的"路由裁判"。给定一条用户问题，判断它该交给哪条路由：

- "simple"：单点事实查询，一次工具调用就能答。包括：查一个字段、参数明确的一次写操作、一次通用返回。
  例如"技术部预算多少？""陈志强在哪个部门？""帮我提交某月某日到某日的年假申请"。
- "complex"：需要多步推理、跨工具聚合、比较计算、权限/RBAC 语义、空值与边界兜底。
  例如"两个部门预算谁多？""没登录时查客户会怎样？""空文本时工具返回什么？"。
- "reject"：与系统无关（天气、股票、新闻）、打听他人隐私（同事薪资、领导行踪）、无需业务数据的闲聊，直接拒答即可。

判断要点：
- 别被"字多"迷惑——一次查询就叫 simple；要查多次再综合/比较/推理的才算 complex。
- 明确给全参数的写操作（提交请假）是 simple，不是 complex。
- 涉及"对比""比例""边界值/空值""权限受限时如何"这类语义，判 complex。
- 问别人隐私/离谱事/=预测类，判 reject。

只输出一个 JSON：
{"verdict": "simple" | "complex" | "reject", "reason": "一句话理由"}
"""


def _build_messages(question: str):
    msgs = [{"role": "system", "content": JUDGE_PROMPT}]
    for u, a in FEWSHOT:
        msgs.append({"role": "user", "content": u})
        msgs.append({"role": "assistant", "content": a})
    msgs.append({"role": "user", "content": question})
    return msgs


def judge(question: str, model: str | None = None) -> dict:
    """返回 {'verdict': 'simple'|'complex'|'reject', 'reason': str}。"""
    use = model or MODEL_SMALL
    try:
        out = chat_json(
            use,
            _build_messages(question),
            temperature=0.0,
            max_tokens=256,
        )
    except Exception as e:
        # LLM 不可用 -> 保守判 complex，保证不丢信息
        return {"verdict": "complex", "reason": f"judge fallback (llm error: {e})"}
    verdict = out.get("verdict")
    if verdict not in VALID:
        verdict = "complex"
    return {"verdict": verdict, "reason": out.get("reason", "")}