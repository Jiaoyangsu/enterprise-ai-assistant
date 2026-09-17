# -*- coding: utf-8 -*-
"""guard↔verifier 等价性 golden 语料。

每条目 = {id, question, trace_obs(工具返回JSON), answer, expect_block}
expect_block=True 表示护栏应拦截（存在无源断言），False 表示应放行。

两侧（自研 verifier.py 与 dsh guard/index.js）都必须给出相同拦截结论。
新增护栏断言类型时，在此补一条正+反用例，两侧测试才会覆盖它。
"""

GOLDEN_CORPUS = [
    # ==== 带单位数字 ====
    {"id": "num-ok-3d",
     "trace_obs": {"found": True, "results": [{"id": "DOC-101", "title": "培训指南", "content": "为期 3 天"}]},
     "answer": "培训为期3天[DOC-101]", "expect_block": False},
    {"id": "num-block-5day",
     "trace_obs": {"found": True, "results": [{"id": "DOC-102", "title": "报销", "content": "500元"}]},
     "answer": "培训持续5个工作日，据DOC-101", "expect_block": True},
    # ==== 语义单位推断 ====
    {"id": "sem-cust-260ok",
     "trace_obs": {"found": True, "data": {"name": "华宇科技", "contract_amount": 260}},
     "answer": "华宇科技合同额260万元", "expect_block": False},
    {"id": "sem-cust-350block",
     "trace_obs": {"found": True, "data": {"name": "华宇科技", "contract_amount": 100}},
     "answer": "华宇科技合同额350万元", "expect_block": True},
    {"id": "sem-balance-12ok",
     "trace_obs": {"found": True, "data": {"employee": "李雷", "leave_balance": 12}},
     "answer": "李雷年假余额12天", "expect_block": False},
    # ==== 裸数字 ====
    {"id": "bare-328-block",
     "trace_obs": {"found": True, "count": 13, "departments": ["技术部", "人事部"]},
     "answer": "公司共有328名员工", "expect_block": True},
    {"id": "bare-328-ok",
     "trace_obs": {"found": True, "count": 328},
     "answer": "共328个部门", "expect_block": False},
    {"id": "bare-2digit-ok",
     "trace_obs": {"found": True, "count": 13},
     "answer": "有13个部门", "expect_block": False},
    # ==== 中文数字 ====
    {"id": "cn-328-ok",
     "trace_obs": {"found": True, "count": 328, "unit": "人"},
     "answer": "公司共有三百二十八人", "expect_block": False},
    {"id": "cn-400-block",
     "trace_obs": {"found": True, "count": 13, "unit": "个"},
     "answer": "公司共有四百多人", "expect_block": True},
    # ==== DOC 引用 ====
    {"id": "doc-102-ok",
     "trace_obs": {"found": True, "results": [{"id": "DOC-102", "title": "报销", "content": "住宿标准200元"}]},
     "answer": "据DOC-102住宿标准200元", "expect_block": False},
    {"id": "doc-101-mismatch",
     "trace_obs": {"found": True, "results": [{"id": "DOC-102", "title": "报销", "content": "500元"}]},
     "answer": "据DOC-101培训为期3天", "expect_block": True},
    # ==== 漏答检测 ====
    {"id": "denial-evidence-block",
     "trace_obs": {"found": True, "results": [{"id": "DOC-101", "title": "培训", "content": "为期 3 天"}]},
     "answer": "制度中未写明新员工集中培训的具体天数。", "expect_block": True},
    {"id": "denial-noinfo-ok",
     "trace_obs": {"found": False, "message": "未找到相关文档"},
     "answer": "制度中未写明食堂开放时间。", "expect_block": False},
    # ==== 措施词 ====
    {"id": "measure-ok-保密字",
     "trace_obs": {"found": True, "results": [{"id": "DOC-015", "title": "保密", "content": "需签署保密承诺书"}]},
     "answer": "需签署保密承诺书[DOC-015]", "expect_block": False},
    {"id": "measure-block-员工手册",
     "trace_obs": {"found": True, "results": [{"id": "DOC-101", "title": "保密", "content": "保密承诺书"}]},
     "answer": "要求签署额外的员工手册", "expect_block": True},
]


def trace_for(entry) -> list[dict]:
    """把 golden 条目转成 verifier 接受的 trace。"""
    import json as _j
    return [{"tool": "t", "args": {}, "obs": _j.dumps(entry["trace_obs"], ensure_ascii=False)}]