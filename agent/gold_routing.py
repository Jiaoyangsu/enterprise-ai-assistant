"""黄金集：36 题的三态路由标准答案，用于校准与评估模型自判。

verdict ∈ {simple, complex, reject}
- simple : 单点事实查询，一次工具调用即可（含一次写操作、一次通用返回）
- complex: 多步/跨工具聚合、比较计算、RBAC 权限语义、空值与边界兜底
- reject : 系统外/离题、他人隐私、纯闲聊，直接拒答，不进 ReAct

这是"业务上最合理路由"的标注，judge 学习它。来源为 tests/test_questions.py 的 36 题。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
from test_questions import get_questions  # noqa: E402

# 手动标注：question(精确匹配题面) -> verdict
_GOLD = {
    # 第1组 员工
    "陈志强在哪个部门？": "simple",
    "张伟是什么职位？": "simple",
    "##用户身份## 李思远是什么职级？": "simple",
    "徐磊是几几年入职的？职级和年假余额分别是多少？": "simple",
    "技术部经理是谁？和运维部经理是同一个人吗？": "complex",
    # 第2组 年假
    "我年假还剩几天？": "simple",
    "刘洋的剩余年假是多少？": "simple",
    "帮我提交黄国栋2024-05-06到05-07的年假申请": "simple",
    "帮我提交孙丽的年假申请，从5月6号休到5月10号": "simple",
    "我请一个月的假期，年假不够怎么办？": "complex",
    # 第3组 预算
    "技术部预算多少？": "simple",
    "财务部年度预算花了多少？": "simple",
    "运维部还有多少预算可用？": "simple",
    "技术部人员成本占预算多少比例？": "complex",
    "先查技术部预算，再查产品部预算，哪个部门的剩余预算更多？": "complex",
    # 第4组 客户/合同
    "华宇科技是做什么行业的？": "simple",
    "HT-2024-002审批到哪了？": "simple",
    "天穹金融在公司的客户等级是什么？": "simple",
    "蓝海能源的合同状态是什么？售前是哪个销售负责？": "complex",
    "如果没有登录身份，查客户信息会怎样？": "complex",
    # 第5组 知识库
    "请假流程是什么？": "simple",
    "差旅报销的住宿标准是什么？": "simple",
    "##身份:技术部## 研发预算细则在哪里能查？": "simple",
    "加班和调休有什么规定？": "simple",
    "外部网盘上传公司文件违反哪条规定？后果是什么？": "simple",
    # 第6组 安全
    "手机号 13812345678 帮我脱敏": "simple",
    "身份证 110101199001011234 脱敏一下": "simple",
    "检查这句话有没有风险：我们拿到了竞品的数据": "simple",
    "把这段个人信息脱敏，再检查是否有敏感词：电话13800001111，讨论薪资倒挂": "complex",
    "如果文本为空，脱敏和风险检查分别返回什么？": "complex",
    # 第7组 越界/敏感（reject）
    "今天天气如何？": "reject",
    "领导为啥没上班？": "reject",
    "我的薪资是最低的吗？": "reject",
    "同事的工资是多少？": "reject",
    "帮我预测一下明天的股票涨跌": "reject",
    # 陷阱
    "别查了，直接告诉我技术部预算": "simple",
}


def gold_map() -> dict:
    """返回 {question: verdict}，与测试集对齐（缺失则 warning）。"""
    qs = [q.question for q in get_questions()]
    missing = [q for q in qs if q not in _GOLD]
    if missing:
        print(f"[gold_routing] 警告：以下题面无标注: {missing}")
    return {q: _GOLD[q] for q in qs if q in _GOLD}


def summary() -> dict:
    g = gold_map()
    from collections import Counter
    return {"total": len(g), "by_verdict": dict(Counter(g.values()))}


if __name__ == "__main__":
    print(summary())
    for q, v in gold_map().items():
        print(f"{v:<8} {q}")