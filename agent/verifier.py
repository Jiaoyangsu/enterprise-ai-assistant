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

# 无单位数字：answer 里无法归到 _UNIT 的独立整数（"17个部门""368名员工""预算1350"）。
# 兜 ≥3 位整数：规避“第3条”“2次”这类序号/次数噪声；年份在 _YEAR_RE 单独排除。
_BARE_NUM_RE = re.compile(r"(?<!\d)(\d{3,6})(?!\d)")
_YEAR_RE = re.compile(r"^19\d\d$|^20[0-2]\d$")  # 1900-2029 视为年份，不作文档断言
# 编号/轨迹类模式：DOC-x / HT-yyyy-xxx / TK-x / 11位手机号 里的数字不属于业务数字断言
_REF_STRIP = re.compile(
    r"DOC-\d{3}|[A-Z]{1,3}-\d{4}-\d{3,4}|TK-\d+|1[3-9]\d{9}|\d{17}[\dXx]"
)


def extract_docs(text: str):
    return sorted(set(re.findall(r"DOC-\d{3}", text or "")))


def extract_bare_numbers(text: str) -> list[str]:
    """无单位数字断言：≥4 位整数、非年份、非编号串、已被单位断言覆盖的不重复。"""
    cleaned = _REF_STRIP.sub(" ", text or "")
    unit_covered = {n for n, _ in extract_number_claims(text or "")}
    out = []
    for n in re.findall(_BARE_NUM_RE, cleaned):
        if _YEAR_RE.match(n):
            continue
        if n in unit_covered:
            continue
        if n not in out:
            out.append(n)
    return out


def _bare_supported(num: str, blob: str) -> bool:
    """独立整数必须作为整体出现在工具观测里（前后非数字），防拼数字逃逸。"""
    return re.search(r"(?<!\d)" + re.escape(num) + r"(?!\d)", blob) is not None


# 中文数字：模型可能把阿拉伯数字改写成汉字（"三百二十八"），纯数字正则全漏。
# 支持到"亿"，含"两"；"万/亿"另给归一化值（obs 常存"万元"整数，如 328 表 328 万）。
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}
_CN_TOKEN = re.compile(r"[零一两二三四五六七八九十百千万亿]+")


def _cn_token_to_int(token: str) -> int:
    total = section = num = 0
    for ch in token:
        if ch in _CN_DIGIT:
            num = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            u = _CN_UNIT[ch]
            if u == 10 and num == 0:
                num = 1  # "十五"里"十"前置
            if u >= 10000:
                total += (section + num) * u
                section = num = 0
            else:
                section += num * u
                num = 0
    return total + section + num


def cn_to_int(token: str) -> tuple[int, list[int]]:
    """返回 (值, [替代值])。替代值含"万/亿"归一化（328 万 → 3_280_000 主值 + 328 别名）。"""
    v = _cn_token_to_int(token)
    alts = [v]
    if token.endswith("万"):
        alts.append(v // 10000)
    elif token.endswith("亿"):
        alts.append(v // 100000000)
    return v, alts


def extract_cn_claims(text: str) -> list[tuple[int, list[int]]]:
    """中文数字断言：只采纳 ≥100 的整数（避开"第一年/三次"枚举噪声）。
    返回 [(值, 别名列表)]，别名含万/亿归一化，供 _cn_supported 消费。"""
    out: list[tuple[int, list[int]]] = []
    seen: set[int] = set()
    for m in _CN_TOKEN.findall(text or ""):
        v, alts = cn_to_int(m)
        if v >= 100 and v not in seen:
            seen.add(v)
            out.append((v, alts))
    return out


def _cn_supported(alts: list[int], blob: str) -> bool:
    """任一等价写法（原文值/万归一化别名）出现在观测里即算有源。"""
    for v in alts:
        if re.search(r"(?<!\d)" + str(v) + r"(?!\d)", blob):
            return True
    return False


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


def _structured_pairs(trace) -> dict[str, str]:
    """从结构化观测中提取 {num: unit} 配对（如 {"leave_balance": 12, "unit": "天"}）。

    工具返回常为"数值字段 + unit/单位字段"结构，JSON 序列化后"12"与"天"不连缀，
    但同文书内配对是极强的有源证据。答案的 (num, unit) 命中此配对表即算 exact。

    增强：
    - 递归收集嵌套结构（query_contract 的 contracts[].amount 也能配对）；
    - 数值字段无 unit 字段时，按字段名语义推断单位（contract_amount→万元、leave_balance→天），
      修复 get_customer_info 等"有数值无单位字段"被误判无源的问题。
    直接解析每个 step 的原始 obs（其本身即为 dict JSON 字符串），避免二次序列化转义。"""
    pairs: dict[str, str] = {}
    for step in trace or []:
        raw = step.get("obs", "")
        if not isinstance(raw, str):
            raw = json.dumps(raw, ensure_ascii=False)
        try:
            d = json.loads(raw)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        _collect_pairs(d, "万元", pairs, top=d)
    return pairs


# 字段名 → 语义单位（工具返回无 unit 字段时兜底推断）
_SEMANTIC_UNITS = {
    "contract_amount": "万元",
    "amount": "万元",
    "annual_budget": "万元",
    "annual": "万元",
    "budget": "万元",
    "spent": "万元",
    "remaining": "万元",
    "leave_balance": "天",
    "days": "天",
}


def _collect_pairs(obj, fallback_unit, pairs, top=None):
    """递归扫描 dict/list，把数值字段 + 单位收集进 pairs。

    - 顶层/作用域内显式 unit/单位 字段优先；
    - 否则按字段名语义推断；都没有则用传入 fallback_unit（默认万元）。
    """
    if isinstance(obj, dict):
        # 显式单位字段：作用于本层及其直接数值字段
        unit_val = obj.get("unit") or obj.get("单位")
        if not isinstance(unit_val, str):
            unit_val = None
        for k, v in obj.items():
            if k in ("unit", "单位", "found", "ok", "message"):
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                u = unit_val or _SEMANTIC_UNITS.get(k, fallback_unit)
                if u:
                    pairs[str(v)] = u
            elif isinstance(v, (dict, list)):
                _collect_pairs(v, fallback_unit, pairs, top)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                _collect_pairs(item, fallback_unit, pairs, top)


def _unit_matches(field_unit: str, claim_unit: str) -> bool:
    """结构 unit 字段（如"万元"/"天"）与回答提取的单位（如"万"/"天"）是否一致。"""
    return claim_unit in field_unit or field_unit in claim_unit


def _number_grade(num, unit, blob, structured: dict | None = None):
    """带单位数字的三级支持判定：
    - "exact": 数字与单位原文连缀命中，或与结构化观测的 {num: unit} 强配对
    - "loose": 数字与单位各自散落在观测中但非连缀（如 {"annual_budget": 405, "unit": "万元"}
      但未落入强配对表）
    - "missing": 观测中找不到该断言
    """
    if re.search(re.escape(num) + r"\s*" + re.escape(unit), blob):
        return "exact"
    if structured:
        field_unit = structured.get(num)
        if field_unit and _unit_matches(field_unit, unit):
            return "exact"
    if unit in ("元", "万", "天", "晚", "次", "份", "条", "%"):
        if re.search(r"(?<!\d)" + re.escape(num) + r"(?!\d)", blob) is None:
            return "missing"
        return "loose" if unit in blob else "missing"
    return "missing"


def _number_supported(num, unit, blob, structured=None):
    """兼容旧接口：非 missing 即算有源（exact/loose 都不拦截）。"""
    return _number_grade(num, unit, blob, structured) != "missing"


# 答非所问/漏答检测：回答称"未写明/未给出"但工具观测里已有可采证的数值
_DENIAL_RE = re.compile(r"未写明|未给出|未提供|无法确认|未能查到|未查到|找不到|没有找到|无此信息|未明确给出|未在返回中")
_DENY_EVIDENCE_RE = re.compile(r"\d+\s*(天|年|月|个工作日|小时|元|万|人次|人|%)|[一二两三四五六七八九十百千万亿]+\s*(天|年|月|个工作日)")


def _denial_but_evidence(answer: str, blob: str) -> bool:
    """回答在用"未写明/未给出"类托词，但观测里存在带单位的可采证数值 → 疑似漏答。"""
    if not _DENIAL_RE.search(answer or ""):
        return False
    return bool(_DENY_EVIDENCE_RE.search(blob))


def verify(question: str, trace, answer: str):
    """返回 {"ok", "verdict", "issues", "soft_notes", "docs_used", "num_claims", "bare_claims", "cn_claims"}。

    三级裁决：
    - verdict="ok"：所有断言有源（exact 命中），可直接交付。
    - verdict="soft"：无硬伤，但至少一条断言为"宽松命中"（数字/单位散落而非连缀），
      可交付但应提示"待核实"（置信度降低，不触发重答）。
    - verdict="fail"：存在硬伤（无源断言），触发纠正重答。
    ok 字段 = verdict != "fail"（兼容旧调用方）。
    """
    blob = obs_blob(trace)
    structured = _structured_pairs(trace)
    issues: list[str] = []
    soft_notes: list[str] = []

    for d in extract_docs(answer or ""):
        if d not in blob:
            issues.append(f"引用文档 {d}，但工具返回未命中该文档")

    for num, unit in extract_number_claims(answer or ""):
        grade = _number_grade(num, unit, blob, structured)
        if grade == "missing":
            issues.append(f"回答出现数字断言 {num}{unit}，工具返回中无此值")
        elif grade == "loose":
            soft_notes.append(f"数字断言 {num}{unit} 为宽松命中（数字/单位分散于观测），建议交付前核实")

    for num in extract_bare_numbers(answer or ""):
        if not _bare_supported(num, blob):
            issues.append(f"回答出现无单位数字断言 {num}，工具返回中无此值")

    for v, alts in extract_cn_claims(answer or ""):
        if not _cn_supported(alts, blob):
            issues.append(f"回答出现中文数字断言 {v}，工具返回中无此值")

    for w in extract_measure_claims(answer or ""):
        if w not in blob:
            issues.append(f"回答出现措施/判定词“{w}”，工具返回中无此内容")

    if _denial_but_evidence(answer, blob):
        issues.append("回答称“未写明/未给出”，但工具返回中已有具体数值证据，疑似漏答")

    verdict = "fail" if issues else ("soft" if soft_notes else "ok")
    return {
        "ok": verdict != "fail",
        "verdict": verdict,
        "issues": issues,
        "soft_notes": soft_notes,
        "docs_used": extract_docs(answer or ""),
        "num_claims": extract_number_claims(answer or ""),
        "bare_claims": extract_bare_numbers(answer or ""),
        "cn_claims": [v for v, _ in extract_cn_claims(answer or "")],
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

    # 无单位裸数字拦截：obs 里只有 13 个部门，回答却写 328 名员工
    bare_trace = [{"tool": "list_departments",
                   "args": {},
                   "obs": json.dumps({"found": True, "count": 13, "departments": ["技术部", "人事部"]}, ensure_ascii=False)}]
    print("bare-bad :", verify("q", bare_trace, "公司共有328名员工"))
    print("bare-good:", verify("q", bare_trace, "共13个部门，含技术部、人事部"))

    # 边界：年份/编号/手机号/有单位数字 ≠ 无单位断言，不应误拦
    edge_trace = [{"tool": "search_knowledge_base", "args": {},
                   "obs": json.dumps({"found": True, "results": [
                       {"id": "DOC-102", "title": "客户资料", "content": "2024年起实行；联系1钟；电话13812345678；年费350元"}]}, ensure_ascii=False)}]
    edge_ans = "据DOC-102，2024年生效，电话13812345678，年费350元，HT-2024-001相关"
    print("edge     :", verify("q", edge_trace, edge_ans))

    # 中文数字改写：观测里是 328 名员工，回答写"三百二十八"应为有源；
    # 观测里只有 13 个部门，回答写"四百多人"应为无源
    cn_trace = [{"tool": "lookup_employee", "args": {},
                 "obs": json.dumps({"found": True, "count": 328, "unit": "人"}, ensure_ascii=False)}]
    print("cn-good  :", verify("q", cn_trace, "公司共有三百二十八人"))
    print("cn-bad   :", verify("q", cn_trace, "公司共有四百多人"))
    # 万归一化：obs 存 32832（万元），回答写"三万二千八百三十二万"应反向命中别名 32832
    wan_blob = json.dumps({"annual_budget": 32832, "unit": "万元"}, ensure_ascii=False)
    print("wan-good :", _bare_supported("32832", wan_blob), "| cn:", _cn_supported([3283200, 32832], wan_blob))