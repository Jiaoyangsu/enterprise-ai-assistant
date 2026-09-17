"""回检器（verifier）单元测试：证据约束 + 无单位数字断言的严格性回归。

覆盖三类能力：
1. 有源通过 / 无源拦截（DOC 引用、带单位数字、措施词）
2. 无单位数字断言（328 名员工类漏网）拦截
3. 边界不过严：年份 / 编号 / 手机号 / 有单位数字不误报
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

from verifier import (
    extract_bare_numbers,
    verify,
)


class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.notes = []

    def check(self, name, ok, detail=""):
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        self.notes.append(f"{'✅' if ok else '❌'} {name} {detail}")


def obs(tool, payload):
    return [{"tool": tool, "args": {}, "obs": json.dumps(payload, ensure_ascii=False)}]


def main():
    t = TestResults()

    # ---- 有源通过 ----
    tr = obs("search_knowledge_base", {"found": True, "results": [
        {"id": "DOC-101", "title": "入职培训", "content": "为期 3 天，不通过者延长试用期或终止试用"}]})
    r = verify("培训几天?", tr, "培训为期3天，据DOC-101")
    t.check("有源-DOC+带单位数字通过", r["ok"], str(r["issues"]))

    # ---- 无源拦截 ----
    r = verify("培训几天?", tr, "培训为期5个工作日，不通过可补考，据DOC-101")
    t.check("无源-带单位数字+措施词拦截", not r["ok"], str(r["issues"]))

    r = verify("培训几天?", tr, "培训为期3天，据DOC-999")
    t.check("无源-引用不存在DOC拦截", not r["ok"], str(r["issues"]))

    # ---- 无单位数字断言 ----
    btr = obs("list_departments", {"found": True, "count": 13,
                                   "departments": ["技术部", "人事部"]})
    r = verify("多少部门?", btr, "公司共有328名员工")
    t.check("裸数字-obs无此值拦截", not r["ok"], str(r["issues"]))
    t.check("裸数字-拦截值正确", r.get("bare_claims") == ["328"], str(r.get("bare_claims")))

    r = verify("多少部门?", btr, "共13个部门，含技术部、人事部")
    t.check("裸数字-数字在obs通过", r["ok"], str(r["issues"]))

    r = verify("多少部门?", btr, "13个部门中技术部在列")
    t.check("裸数字-2位数字不误报", r["ok"], str(r["issues"]))

    # ---- 边界不过严 ----
    etr = obs("search_knowledge_base", {"found": True, "results": [
        {"id": "DOC-102", "title": "客户资料",
         "content": "2024年起实行；联系项；电话13812345678；年费350元"}]})
    ans = "据DOC-102，2024年生效，电话13812345678，年费350元，HT-2024-001相关"
    r = verify("客户资料?", etr, ans)
    t.check("边界-年份/手机号/编号/有单位全通过", r["ok"], str(r["issues"]))

    # 年份即便只作裸数字也绝不做裸断言（带单位"年"仍走带单位校验）
    t.check("边界-年份不做裸断言", extract_bare_numbers("协议是2024年签的") == [])

    # ---- extract_bare_numbers 纯函数 ----
    t.check("裸数字-提取", extract_bare_numbers("共328名员工 1350预算") == ["328", "1350"])
    t.check("裸数字-剔除年份", extract_bare_numbers("2024年，有328人") == ["328"])
    t.check("裸数字-剔除编号/手机号", extract_bare_numbers("DOC-101 HT-2024-001 13812345678") == [])
    t.check("裸数字-去重", extract_bare_numbers("328人，另有328人") == ["328"])

    # ---- 中文数字改写（第一处：观察里有 328，回答写"三百二十八"）----
    cntr = obs("lookup_employee", {"found": True, "count": 328, "unit": "人"})
    r = verify("多少人?", cntr, "公司共有三百二十八人")
    t.check("中文数字-有源放行", r["ok"], str(r["issues"]))
    t.check("中文数字-claims", r.get("cn_claims") == [328], str(r.get("cn_claims")))

    r = verify("多少人?", cntr, "公司共有四百多人")
    t.check("中文数字-无源拦截", not r["ok"], str(r["issues"]))

    # 万归一化：obs 是 3283（万元整数），回答"三千二百八十三万元"应反查别名 3283
    wtr = obs("query_budget", {"annual_budget": 3283, "unit": "万元"})
    r = verify("预算?", wtr, "预算三千二百八十三万元")
    t.check("中文数字-万归一化有源", r["ok"], str(r["issues"]))

    # 中文数字噪声不误报：枚举性"第一年/三次"不产生断言
    r = verify("考核?", obs("q", {"found": True}), "第一年考核两次")
    t.check("中文数字-小数字噪声不误报", r["ok"], r["issues"] and str(r["issues"]) or "")

    # ---- 三级裁决：ok / soft / fail ----
    # ok：数字单位连缀命中
    r = verify("年假?", obs("lookup_employee", {"name": "刘洋", "leave_balance": 12, "unit": "天"}),
               "刘洋年假剩余12天")
    t.check("三态-ok连缀", r["verdict"] == "ok" and r["ok"], str(r))

    # soft：数字在一条记录（{"x": 12}）、单位"天"只在另一记录提及，
    #   既无连缀也无语义单位推断 → 宽松命中，交付但提示核实
    two_step = [
        {"tool": "lookup_employee", "args": {},
         "obs": json.dumps({"found": True, "x": 12}, ensure_ascii=False)},
        {"tool": "search_knowledge_base", "args": {},
         "obs": json.dumps({"note": "公司年假按天计"}, ensure_ascii=False)},
    ]
    r = verify("年假?", two_step, "刘洋年假还剩12天")
    t.check("三态-soft宽松命中", r["verdict"] == "soft", str(r))
    t.check("三态-soft仍可交付", r["ok"], str(r))
    t.check("三态-soft有notes", len(r["soft_notes"]) >= 1, str(r["soft_notes"]))

    # fail：数字完全无源
    r = verify("年假?", obs("lookup_employee", {"name": "刘洋", "leave_balance": 12, "unit": "天"}),
               "刘洋年假剩余30天")
    t.check("三态-fail无源", r["verdict"] == "fail" and not r["ok"], str(r))
    t.check("三态-fail有issues", len(r["issues"]) >= 1, str(r["issues"]))

    # ---- 语义单位推断（无 unit 字段的工具返回）----
    # get_customer_info 返回 contract_amount: 260 无 unit → 应为 260万 强配对（exact）
    r = verify("华宇科技等级和合同额?",
               obs("get_customer_info", {"found": True, "level": "VIP", "contract_amount": 260}),
               "华宇科技是VIP客户，合同额260万")
    t.check("语义单位-有源通过", r["verdict"] == "ok" and r["ok"], str(r))
    t.check("语义单位-无issues", not r["issues"], str(r["issues"]))
    # 数值确实无源时仍要拦截（如编了个不在返回里的 350）
    r = verify("华宇科技合同额?",
               obs("get_customer_info", {"found": True, "contract_amount": 260}),
               "华宇科技合同额350万")
    t.check("语义单位-无源仍拦截", r["verdict"] == "fail", str(r))

    print(f"\n结果：{t.passed}/{t.passed + t.failed} 通过，{t.failed} 失败")
    for n in t.notes:
        print(f"  {n}")
    sys.exit(1 if t.failed else 0)


if __name__ == "__main__":
    main()