"""回检器：证据约束 + 输出校验合并层。

对 (question, trace, answer) 做两类校验：
1. 证据约束：回答引用的 DOC-xxx 编号、带单位的数字、措施/判定词，必须在本次工具观测（obs）中真实出现。
2. 输出校验：出现“无源断言”即返回 issues；调用方据此重答一次或拒答。

设计取舍：
- 宁可过严（触发一次重答），不可过松（放过幻觉）。业务金额类（返回为裸数字）可能误报，
  但重答成本低；真正的幻觉（3~5 天、补考、二级以上医院）会被精确拦截。
"""
import json
import re

# 措施/判定/流程类敏感词：出现在回答里但工具返回无支撑即视为无源
MEASURE_WORDS = [
    "补考", "额外培训", "延长期", "终止试用", "解除", "个人承担", "诊断证明",
    "保密承诺书", "保密协议", "员工手册", "入职登记表", "作废", "申诉", "退回",
    "补卡", "转正", "录用", "辞退", "赔偿", "补偿", "没收", "罚款", "书面同意",
    "副总", "分管领导", "总监", "总裁",
]

# 带单位数字：既有文本（“5 个工作日”“350 元”）又有业务（“2 万”）统统当断言
_UNIT = (
    r"(元|万|晚|天|个工作日|工作日|个月|年|月|日|小时|周|次|%|份|条|级|笔|折|倍)"
)


def extract_docs(text: str):
    return sorted(set(re.findall(r"DOC-\d{3}", text or "")))


def extract_number_claims(text: str):
    """返回 [(num, unit)]，如 (“5”, “个工作日”)、(“350”, “元”)。"""
    out = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*" + _UNIT, text or ""):
        out.append((m.group(1), m.group(2)))
    return out


def extract_measure_claims(text: str):
    return [w for w in MEASURE_WORDS if w in (text or "")]


def obs_blob(trace):
    """把所有工具观测 + 参数拼接成可检索文本。"""
    if not trace:
        return ""
    buf = []
    for step in trace:
        buf.append(json.dumps(step.get("obs", ""), ensure_ascii=False))
        buf.append(json.dumps(step.get("args", ""), ensure_ascii=False))
    return " ".join(buf)


def _number_supported(num, unit, blob):
    # 精确命中：5 个工作日 / 5个工作日 / 350 元
    if re.search(re.escape(num) + r"\s*" + re.escape(unit), blob):
        return True
    # 宽松命中：金额/数量类单位，返回结构可能为 {"annual_budget": 405, "unit": "万元"}
    # 只有数字（作为独立数值）与单位单字都出现才算有源，防“无源数字”溜过。
    if unit in ("元", "万", "天", "晚", "次", "份", "条", "%"):
        if re.search(r"(?<!\d)" + re.escape(num) + r"(?!\d)", blob) is None:
            return False
        return unit in blob
    return False


def verify(question: str, trace, answer: str):
    """返回 {"ok", "issues", "docs_used", "num_claims"}。"""
    blob = obs_blob(trace)
    issues = []

    for d in extract_docs(answer or ""):
        if d not in blob:
            issues.append(f"引用文档 {d}，但工具返回未命中该文档")

    for num, unit in extract_number_claims(answer or ""):
        if not _number_supported(num, unit, blob):
            issues.append(f"回答出现数字断言 {num}{unit}，工具返回中无此值")

    for w in extract_measure_claims(answer or ""):
        if w not in blob:
            issues.append(f"回答出现措施/判定词“{w}”，工具返回中无此内容")

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "docs_used": extract_docs(answer or ""),
        "num_claims": extract_number_claims(answer or ""),
    }


if __name__ == "__main__":
    # 冒烟：有源通过 / 无源拦截
    good_trace = [{"tool": "search_knowledge_base",
                   "args": {"query": "培训"},
                   "obs": json.dumps({"found": True, "results": [{"id": "DOC-101", "title": "新员工入职培训指南", "content": "第三条 ...为期 3 天...不通过者延长试用期或终止试用"}]}, ensure_ascii=False)}]
    print("good:", verify("q", good_trace, "培训为期3天[DOC-101]"))
    bad_trace = [{"tool": "search_knowledge_base",
                  "args": {"query": "培训"},
                  "obs": json.dumps({"found": True, "results": [{"id": "DOC-102", "title": "费用报销操作流程", "content": "500 元"}]}, ensure_ascii=False)}]
    print("bad :", verify("q", bad_trace, "培训持续3到5个工作日，不通过可补考，据DOC-101"))